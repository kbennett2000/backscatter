"""Tests for the serve layer: /api/latest now reads the SQLite index."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from backscatter.api.app import create_app
from backscatter.api.frames import latest_frame
from backscatter.config import Config, Location
from backscatter.store import db

BOUNDS = (-107.23, 37.71, -101.86, 41.86)  # west, south, east, north


def _config(tmp_path: Path, *, extra: tuple[Location, ...] = ()) -> Config:
    home = Location("Home", 39.3603, -104.5969, "KFTG", True, False)
    return Config(
        locations=(home, *extra),
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "backscatter.db",
        poll_interval_s=60.0,
    )


def _seed_frame(
    config: Config,
    *,
    site: str = "KFTG",
    volume: str = "KFTG20260620_215107_V06",
    scan_time: str = "2026-06-20T21:51:07+00:00",
    bounds: tuple[float, float, float, float] = BOUNDS,
    status: str = "rendered",  # rendered | pending | failed
    write_png: bool = False,
) -> bytes:
    """Index a volume (+render), optionally writing the PNG file under data/renders."""
    scan = datetime.fromisoformat(scan_time)
    conn = db.connect(config.db_path)
    db.init_db(conn)
    db.record_volume(
        conn, site=site, scan_time=scan, s3_key=f"k/{volume}",
        path=Path(volume), size_bytes=1, downloaded_at=scan,
    )
    image_path = f"{site}/{volume}.png"
    if status == "rendered":
        db.record_render(
            conn, site=site, scan_time=scan, image_path=image_path,
            elevation_deg=0.483, width=2, height=2, bounds=bounds, rendered_at=scan,
        )
    elif status == "failed":
        db.mark_render_failed(conn, site, scan)
    conn.close()

    png_bytes = b""
    if write_png:
        out = config.data_dir / "renders" / site
        out.mkdir(parents=True, exist_ok=True)
        img = np.zeros((2, 2, 4), dtype=np.uint8)
        img[0, 0] = (253, 0, 0, 255)
        Image.fromarray(img, "RGBA").save(out / f"{volume}.png")
        png_bytes = (out / f"{volume}.png").read_bytes()
    return png_bytes


def test_latest_frame_none_when_empty(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "data" / "db.sqlite")
    db.init_db(conn)
    assert latest_frame(conn) is None


def test_api_config_uses_config_center(tmp_path: Path) -> None:
    config = _config(tmp_path)
    client = TestClient(create_app(config))
    body = client.get("/api/config").json()
    assert body["center"] == [config.lon, config.lat]
    assert body["site"] == "KFTG"


def test_api_latest_matches_index(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _seed_frame(config)
    client = TestClient(create_app(config))
    body = client.get("/api/latest").json()
    assert body["site"] == "KFTG"
    assert body["scan_time"] == "2026-06-20T21:51:07+00:00"
    assert body["elevation_deg"] == 0.483
    assert body["bounds"] == {
        "west": -107.23, "south": 37.71, "east": -101.86, "north": 41.86
    }
    assert body["image_url"] == "/renders/KFTG/KFTG20260620_215107_V06.png"


def test_api_latest_newest_wins(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _seed_frame(
        config, volume="KFTG20260620_200000_V06",
        scan_time="2026-06-20T20:00:00+00:00",
    )
    _seed_frame(
        config, volume="KFTG20260620_215107_V06",
        scan_time="2026-06-20T21:51:07+00:00",
    )
    client = TestClient(create_app(config))
    body = client.get("/api/latest").json()
    assert body["scan_time"] == "2026-06-20T21:51:07+00:00"


def test_api_latest_404_when_empty(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(tmp_path)))
    assert client.get("/api/latest").status_code == 404


def test_render_png_served_with_content_type(tmp_path: Path) -> None:
    config = _config(tmp_path)
    png_bytes = _seed_frame(config, write_png=True)
    client = TestClient(create_app(config))
    resp = client.get("/renders/KFTG/KFTG20260620_215107_V06.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == png_bytes


def test_index_served_as_html(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(tmp_path)))
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")


# --- /api/frames (timeline) --------------------------------------------------


def _seed_three_rendered(config: Config) -> list[str]:
    times = [
        "2026-06-20T20:00:00+00:00",
        "2026-06-20T21:00:00+00:00",
        "2026-06-20T22:00:00+00:00",
    ]
    for t in times:
        _seed_frame(config, volume=f"KFTG20260620_{t[11:13]}0000_V06", scan_time=t)
    return times


def test_frames_only_rendered_ascending(tmp_path: Path) -> None:
    config = _config(tmp_path)
    times = _seed_three_rendered(config)
    # A pending and a failed frame must be excluded.
    _seed_frame(config, volume="KFTG20260620_230000_V06",
                scan_time="2026-06-20T23:00:00+00:00", status="pending")
    _seed_frame(config, volume="KFTG20260620_233000_V06",
                scan_time="2026-06-20T23:30:00+00:00", status="failed")

    body = TestClient(create_app(config)).get("/api/frames").json()
    assert body["site"] == "KFTG"
    assert body["count"] == 3
    assert [f["scan_time"] for f in body["frames"]] == times


def test_frames_range_filter(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _seed_three_rendered(config)
    client = TestClient(create_app(config))

    # Query timestamps use Z form ('+' would decode to a space in a URL).
    after = client.get("/api/frames?start=2026-06-20T21:00:00Z").json()
    assert [f["scan_time"] for f in after["frames"]] == [
        "2026-06-20T21:00:00+00:00", "2026-06-20T22:00:00+00:00",
    ]
    before = client.get("/api/frames?end=2026-06-20T21:00:00Z").json()
    assert [f["scan_time"] for f in before["frames"]] == [
        "2026-06-20T20:00:00+00:00", "2026-06-20T21:00:00+00:00",
    ]


def test_frames_empty_range_is_ok(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _seed_three_rendered(config)
    resp = TestClient(create_app(config)).get(
        "/api/frames?start=2027-01-01T00:00:00Z"
    )
    assert resp.status_code == 200
    assert resp.json()["frames"] == []


def test_frames_limit_keeps_most_recent(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _seed_three_rendered(config)
    body = TestClient(create_app(config)).get("/api/frames?limit=2").json()
    assert body["limit"] == 2
    # Most recent two, still ascending.
    assert [f["scan_time"] for f in body["frames"]] == [
        "2026-06-20T21:00:00+00:00", "2026-06-20T22:00:00+00:00",
    ]


def test_frames_site_filter_and_default(tmp_path: Path) -> None:
    config = _config(tmp_path)  # config.site == KFTG
    _seed_frame(config, scan_time="2026-06-20T21:00:00+00:00")
    _seed_frame(config, site="KPUX", volume="KPUX20260620_210000_V06",
                scan_time="2026-06-20T21:00:00+00:00")
    client = TestClient(create_app(config))

    default = client.get("/api/frames").json()  # defaults to config.site
    assert {f["site"] for f in default["frames"]} == {"KFTG"}
    pux = client.get("/api/frames?site=KPUX").json()
    assert {f["site"] for f in pux["frames"]} == {"KPUX"}


def test_frames_bad_timestamp_is_400(tmp_path: Path) -> None:
    resp = TestClient(create_app(_config(tmp_path))).get("/api/frames?start=nope")
    assert resp.status_code == 400


# --- Slice 7: archive navigation (range + cursor pagination) -----------------


def _seed_n(config: Config, n: int) -> list[str]:
    times = []
    for i in range(n):
        t = f"2026-06-20T12:{i:02d}:00+00:00"
        _seed_frame(config, volume=f"KFTG20260620_12{i:02d}00_V06", scan_time=t)
        times.append(t)
    return times


def test_frames_range_endpoint(tmp_path: Path) -> None:
    config = _config(tmp_path)
    times = _seed_n(config, 3)
    client = TestClient(create_app(config))
    body = client.get("/api/frames/range").json()
    assert body == {"site": "KFTG", "min": times[0], "max": times[-1], "count": 3}


def test_frames_range_endpoint_empty(tmp_path: Path) -> None:
    body = TestClient(create_app(_config(tmp_path))).get("/api/frames/range").json()
    assert body == {"site": "KFTG", "min": None, "max": None, "count": 0}


def test_frames_default_next_cursor_null(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _seed_n(config, 3)
    body = TestClient(create_app(config)).get("/api/frames").json()
    assert body["next_cursor"] is None
    assert body["count"] == 3


def test_frames_window_over_cap_paginates_cleanly(tmp_path: Path) -> None:
    config = _config(tmp_path)
    times = _seed_n(config, 5)
    client = TestClient(create_app(config))
    window = "start=2026-06-20T12:00:00Z&end=2026-06-20T12:59:00Z"

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        url = f"/api/frames?{window}&limit=2"
        if cursor is not None:
            url += f"&cursor={quote(cursor)}"
        body = client.get(url).json()
        pages += 1
        seen.extend(f["scan_time"] for f in body["frames"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert len(body["frames"]) == 2  # full pages until the last
    assert seen == times  # contiguous, ordered
    assert len(seen) == len(set(seen)) == 5  # no dupes
    assert pages == 3


def test_frames_window_empty_range_has_null_cursor(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _seed_n(config, 3)
    resp = TestClient(create_app(config)).get(
        "/api/frames?start=2027-01-01T00:00:00Z"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["frames"] == []
    assert body["next_cursor"] is None


# --- Slice 8: multiple locations ---------------------------------------------

_OKC = Location("OKC", 35.4676, -97.5164, "KTLX", False, False)


def test_api_locations(tmp_path: Path) -> None:
    config = _config(tmp_path, extra=(_OKC,))
    body = TestClient(create_app(config)).get("/api/locations").json()
    assert body["locations"] == [
        {"name": "Home", "lat": 39.3603, "lon": -104.5969,
         "default": True, "site": "KFTG"},
        {"name": "OKC", "lat": 35.4676, "lon": -97.5164,
         "default": False, "site": "KTLX"},
    ]


def test_api_frames_location_param_resolves_site(tmp_path: Path) -> None:
    config = _config(tmp_path, extra=(_OKC,))
    _seed_frame(config, scan_time="2026-06-20T21:00:00+00:00")  # KFTG
    _seed_frame(config, site="KTLX", volume="KTLX20260620_210000_V06",
                scan_time="2026-06-20T21:00:00+00:00")
    client = TestClient(create_app(config))

    default = client.get("/api/frames").json()  # Home -> KFTG
    assert {f["site"] for f in default["frames"]} == {"KFTG"}
    okc = client.get("/api/frames?location=OKC").json()
    assert {f["site"] for f in okc["frames"]} == {"KTLX"}


def test_api_frames_unknown_location_400(tmp_path: Path) -> None:
    resp = TestClient(create_app(_config(tmp_path))).get("/api/frames?location=Nope")
    assert resp.status_code == 400


def test_api_latest_is_site_scoped_to_home(tmp_path: Path) -> None:
    config = _config(tmp_path, extra=(_OKC,))
    # KTLX frame is newer than KFTG, but /api/latest defaults to Home (KFTG).
    _seed_frame(config, scan_time="2026-06-20T21:00:00+00:00")  # KFTG
    _seed_frame(config, site="KTLX", volume="KTLX20260620_230000_V06",
                scan_time="2026-06-20T23:00:00+00:00")  # KTLX, newer
    client = TestClient(create_app(config))

    assert client.get("/api/latest").json()["site"] == "KFTG"  # Home
    assert client.get("/api/latest?location=OKC").json()["site"] == "KTLX"
