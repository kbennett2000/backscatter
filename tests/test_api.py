"""Tests for the serve layer: /api/latest now reads the SQLite index."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import boto3
import numpy as np
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws
from PIL import Image

from backscatter.api.app import create_app
from backscatter.api.frames import latest_frame
from backscatter.config import Config, SeedLocation
from backscatter.ingest import naming, s3
from backscatter.jobs.manager import BackfillJob, JobConflict, JobManager, JobState
from backscatter.prune.prune import run_prune
from backscatter.render.render import RenderResult
from backscatter.store import db
from backscatter.store.settings import RetentionPolicy

BOUNDS = (-107.23, 37.71, -101.86, 41.86)  # west, south, east, north

_OKC = SeedLocation("OKC", 35.4676, -97.5164, False)  # -> KTLX


def _config(tmp_path: Path, *, extra: tuple[SeedLocation, ...] = ()) -> Config:
    home = SeedLocation("Home", 39.3603, -104.5969, True)
    return Config(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "backscatter.db",
        poll_interval_s=60.0,
        site_override=None,
        seed_locations=(home, *extra),
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


def _seed_cells(
    config: Config,
    *,
    site: str = "KFTG",
    scan_time: str = "2026-06-20T21:51:07+00:00",
    cells: list | None = None,
) -> None:
    """Store storm cells for a frame (Slice 28c overlay tests)."""
    from backscatter.track.associate import TrackedCell
    from backscatter.track.detect import Cell

    if cells is None:
        cells = [
            # A fast mover (east 20 m/s) on an established track (n_obs ≥ 3, so its
            # motion is drawn) + a stationary cell (no motion → no arrow).
            TrackedCell(
                Cell(centroid_lon=-104.5, centroid_lat=39.8, max_dbz=58.0,
                     area_km2=240.0),
                track_id=1, u_ms=20.0, v_ms=0.0, n_obs=3,
            ),
            TrackedCell(
                Cell(centroid_lon=-103.9, centroid_lat=39.6, max_dbz=44.0,
                     area_km2=30.0),
                track_id=2, u_ms=0.0, v_ms=0.0, n_obs=3,
            ),
        ]
    conn = db.connect(config.db_path)
    db.init_db(conn)
    db.record_cells(
        conn, site=site, scan_time=datetime.fromisoformat(scan_time), cells=cells
    )
    conn.close()


def test_api_cells_returns_frame_cells(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _seed_frame(config)
    _seed_cells(config)
    client = TestClient(create_app(config))

    body = client.get(
        "/api/cells", params={"site": "KFTG", "scan_time": "2026-06-20T21:51:07Z"}
    ).json()
    assert body["site"] == "KFTG"
    assert len(body["tracks"]) == 2
    # Strongest dBZ first; the mover has a heading ~east (90°) — the overlay draws the
    # arrow itself from speed+bearing, so the payload carries motion, not an endpoint.
    mover = body["tracks"][0]
    assert mover["track_id"] == 1
    assert mover["max_dbz"] == pytest.approx(58.0)
    assert mover["speed_kmh"] == pytest.approx(72.0, abs=0.5)  # 20 m/s
    assert mover["bearing_deg"] == pytest.approx(90.0, abs=0.5)
    assert "proj_lon" not in mover and "proj_lat" not in mover
    # The stationary cell: no heading → marker only, no arrow.
    still = body["tracks"][1]
    assert still["track_id"] == 2
    assert still["bearing_deg"] is None


def test_api_cells_young_track_draws_no_vector(tmp_path: Path) -> None:
    """A moving cell whose track has too few observations (Slice 28f, n_obs < 3) shows
    as a marker only — no heading — so a one-frame mismatch fluke draws no arrow."""
    from backscatter.track.associate import TrackedCell
    from backscatter.track.detect import Cell

    config = _config(tmp_path)
    _seed_frame(config)
    # Two equally fast movers; only the established one (n_obs ≥ 3) gets a heading.
    young = TrackedCell(
        Cell(centroid_lon=-104.5, centroid_lat=39.8, max_dbz=58.0, area_km2=99.0),
        track_id=1, u_ms=20.0, v_ms=0.0, n_obs=2,
    )
    established = TrackedCell(
        Cell(centroid_lon=-104.0, centroid_lat=39.8, max_dbz=50.0, area_km2=40.0),
        track_id=2, u_ms=20.0, v_ms=0.0, n_obs=3,
    )
    _seed_cells(config, cells=[young, established])
    client = TestClient(create_app(config))

    tracks = client.get(
        "/api/cells", params={"site": "KFTG", "scan_time": "2026-06-20T21:51:07Z"}
    ).json()["tracks"]
    by_id = {t["track_id"]: t for t in tracks}
    assert by_id[1]["bearing_deg"] is None  # young → marker only
    assert by_id[1]["speed_kmh"] == pytest.approx(72.0, abs=0.5)  # speed still reported
    assert by_id[2]["bearing_deg"] == pytest.approx(90.0, abs=0.5)  # established: drawn


def test_api_cells_only_that_frame(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _seed_frame(config)
    _seed_cells(config, scan_time="2026-06-20T21:51:07+00:00")
    client = TestClient(create_app(config))
    # A different scan_time has no cells.
    body = client.get(
        "/api/cells", params={"site": "KFTG", "scan_time": "2026-06-20T22:99:99Z"}
    )
    assert body.status_code == 400  # malformed timestamp
    empty = client.get(
        "/api/cells", params={"site": "KFTG", "scan_time": "2026-06-20T20:00:00Z"}
    ).json()
    assert empty["tracks"] == []


def test_api_cells_bad_timestamp_is_400(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _seed_frame(config)
    client = TestClient(create_app(config))
    resp = client.get("/api/cells", params={"site": "KFTG", "scan_time": "not-a-time"})
    assert resp.status_code == 400


def test_frontend_assets_are_revalidated_renders_are_cacheable(tmp_path: Path) -> None:
    """index.html + /static/* carry Cache-Control: no-cache so a deploy is never masked
    by a stale browser cache; immutable rendered PNGs stay long-cacheable."""
    config = _config(tmp_path)
    _seed_frame(config, write_png=True)
    client = TestClient(create_app(config))

    assert client.get("/").headers.get("cache-control") == "no-cache"
    assert client.get("/static/app.js").headers.get("cache-control") == "no-cache"
    # A rendered PNG must NOT be forced to revalidate (immutable, keyed by scan_time).
    png = client.get("/renders/KFTG/KFTG20260620_215107_V06.png")
    assert png.status_code == 200
    assert png.headers.get("cache-control") != "no-cache"


def test_latest_frame_none_when_empty(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "data" / "db.sqlite")
    db.init_db(conn)
    assert latest_frame(conn) is None


def test_api_config_uses_default_location_center(tmp_path: Path) -> None:
    config = _config(tmp_path)  # Home at Elizabeth, CO
    client = TestClient(create_app(config))
    body = client.get("/api/config").json()
    assert body["center"] == [-104.5969, 39.3603]  # [lon, lat]
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


def test_pruned_frame_leaves_timeline_cleanly(tmp_path: Path) -> None:
    # A pruned frame must vanish from /api/frames AND its PNG must be gone — no
    # dangling row pointing at a deleted file (which would 404 the scrubber).
    config = _config(tmp_path)  # age limit 30 days (default ON)
    old_vol = "KFTG20260101_000000_V06"
    new_vol = "KFTG20260620_215107_V06"
    _seed_frame(
        config, volume=old_vol, scan_time="2026-01-01T00:00:00+00:00", write_png=True
    )
    _seed_frame(
        config, volume=new_vol, scan_time="2026-06-20T21:51:07+00:00", write_png=True
    )

    conn = db.connect(config.db_path)
    db.init_db(conn)
    policy = RetentionPolicy(
        config.retention_max_age_days, config.retention_max_size_bytes
    )
    now = datetime(2026, 6, 20, 22, 0, tzinfo=UTC)
    run_prune(conn, config, policy, now=now, dry_run=False)
    conn.close()

    client = TestClient(create_app(config))
    scans = [f["scan_time"] for f in client.get("/api/frames").json()["frames"]]
    assert scans == ["2026-06-20T21:51:07+00:00"]  # only the new frame remains
    # The kept frame's PNG still serves; the pruned one's file is gone (no 404 row).
    assert client.get(f"/renders/KFTG/{new_vol}.png").status_code == 200
    assert client.get(f"/renders/KFTG/{old_vol}.png").status_code == 404
    assert not (config.data_dir / "renders" / "KFTG" / f"{old_vol}.png").exists()


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


# --- locations: read (seeded into the store on first create_app) -------------


def test_api_locations(tmp_path: Path) -> None:
    config = _config(tmp_path, extra=(_OKC,))
    body = TestClient(create_app(config)).get("/api/locations").json()
    assert body["locations"] == [
        {"id": 1, "name": "Home", "lat": 39.3603, "lon": -104.5969,
         "default": True, "site": "KFTG"},
        {"id": 2, "name": "OKC", "lat": 35.4676, "lon": -97.5164,
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


# --- locations: write (CRUD) -------------------------------------------------


def _names(client: TestClient) -> list[str]:
    return [loc["name"] for loc in client.get("/api/locations").json()["locations"]]


def test_create_location(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(tmp_path)))
    resp = client.post(
        "/api/locations", json={"name": "OKC", "lat": 35.4676, "lon": -97.5164}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "OKC" and body["site"] == "KTLX" and body["default"] is False
    assert _names(client) == ["Home", "OKC"]


def test_create_duplicate_name_400(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(tmp_path)))
    resp = client.post(
        "/api/locations", json={"name": "home", "lat": 1, "lon": 2}
    )
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]


def test_create_as_default_demotes_previous(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(tmp_path)))
    client.post(
        "/api/locations",
        json={"name": "OKC", "lat": 35.4676, "lon": -97.5164, "default": True},
    )
    locs = {loc["name"]: loc["default"] for loc in
            client.get("/api/locations").json()["locations"]}
    assert locs == {"OKC": True, "Home": False}


def test_update_location_recomputes_site(tmp_path: Path) -> None:
    config = _config(tmp_path, extra=(_OKC,))
    client = TestClient(create_app(config))
    # Move OKC (id 2) to Chicago -> site should re-resolve to KLOT.
    resp = client.put("/api/locations/2", json={"lat": 41.8781, "lon": -87.6298})
    assert resp.status_code == 200
    assert resp.json()["site"] == "KLOT"


def test_set_default_via_update(tmp_path: Path) -> None:
    config = _config(tmp_path, extra=(_OKC,))
    client = TestClient(create_app(config))
    client.put("/api/locations/2", json={"default": True})  # OKC becomes default
    locs = {loc["name"]: loc["default"] for loc in
            client.get("/api/locations").json()["locations"]}
    assert locs == {"OKC": True, "Home": False}


def test_delete_location(tmp_path: Path) -> None:
    config = _config(tmp_path, extra=(_OKC,))
    client = TestClient(create_app(config))
    assert client.delete("/api/locations/2").status_code == 204  # OKC (non-default)
    assert _names(client) == ["Home"]


def test_delete_default_rejected(tmp_path: Path) -> None:
    config = _config(tmp_path, extra=(_OKC,))
    client = TestClient(create_app(config))
    resp = client.delete("/api/locations/1")  # Home is the default
    assert resp.status_code == 400
    assert "default" in resp.json()["detail"]
    assert _names(client) == ["Home", "OKC"]  # nothing deleted


def test_delete_last_location_rejected(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(tmp_path)))  # only Home
    resp = client.delete("/api/locations/1")
    assert resp.status_code == 400
    assert "only location" in resp.json()["detail"]


def test_update_missing_location_404(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(tmp_path)))
    resp = client.put("/api/locations/999", json={"lat": 1, "lon": 2})
    assert resp.status_code == 404


def test_store_wins_over_env_on_restart(tmp_path: Path) -> None:
    # First app seeds Home from env and adds OKC.
    config = _config(tmp_path)
    client = TestClient(create_app(config))
    client.post("/api/locations", json={"name": "OKC", "lat": 35.4676, "lon": -97.5164})
    # A second app over the same DB (even with a different seed) must NOT re-seed.
    config2 = Config(
        data_dir=config.data_dir, db_path=config.db_path, poll_interval_s=60.0,
        site_override=None,
        seed_locations=(SeedLocation("Somewhere", 47.6, -122.3, True),),
    )
    client2 = TestClient(create_app(config2))
    assert _names(client2) == ["Home", "OKC"]  # DB wins; the new seed is ignored


# --- backfill endpoints (Slice 19) -------------------------------------------


@pytest.fixture
def s3_client() -> Iterator[object]:
    with mock_aws():
        client = boto3.client("s3", region_name=s3.REGION)
        client.create_bucket(Bucket=s3.BUCKET)
        yield client


def _stub_render() -> Callable[..., RenderResult]:
    def render(volume_path: Path, config: Config) -> RenderResult:
        name = Path(volume_path).name
        png = config.data_dir / "renders" / naming.parse_site(name) / f"{name}.png"
        return RenderResult(
            png_path=png, sidecar_path=png.with_suffix(".json"),
            site=naming.parse_site(name), scan_time=naming.parse_scan_time(name),
            elevation_deg=0.5, width=10, height=20,
            bounds_wgs84=(-107.0, 37.0, -101.0, 42.0), bounds_3857=(0.0, 0.0, 1.0, 1.0),
        )

    return render


def test_backfill_start_returns_202_and_completes(
    tmp_path: Path, s3_client: object
) -> None:
    config = _config(tmp_path)
    manager = JobManager(
        config, client_factory=lambda: s3_client, render_fn=_stub_render(),
    )
    client = TestClient(create_app(config, job_manager=manager))

    resp = client.post("/api/backfill", json={"hours": 6})
    assert resp.status_code == 202
    body = resp.json()
    assert body["site"] == "KFTG"  # Home → KFTG
    assert body["state"] in (JobState.QUEUED.value, JobState.RUNNING.value)

    manager.wait(timeout=10)  # let the (empty-range) job finish
    got = client.get(f"/api/backfill/{body['id']}")
    assert got.status_code == 200
    assert got.json()["state"] == JobState.DONE.value


def test_backfill_over_cap_400(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(tmp_path)))
    resp = client.post("/api/backfill", json={"hours": 25})
    assert resp.status_code == 400
    assert "24" in resp.json()["detail"]


def test_backfill_unknown_location_400(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(tmp_path)))
    resp = client.post("/api/backfill", json={"location": "Nowhere"})
    assert resp.status_code == 400


def test_backfill_conflict_409(tmp_path: Path) -> None:
    config = _config(tmp_path)
    running = BackfillJob(
        id="abc123", site="KFTG",
        start=datetime(2026, 6, 20, tzinfo=UTC),
        end=datetime(2026, 6, 20, 6, tzinfo=UTC),
        state=JobState.RUNNING,
    )

    class _Busy(JobManager):
        def start(self, **kw: object) -> dict[str, object]:
            raise JobConflict(running)

    client = TestClient(create_app(config, job_manager=_Busy(config)))
    resp = client.post("/api/backfill", json={"hours": 6})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["job"]["id"] == "abc123"
    assert "already running" in detail["message"]


def test_backfill_status_unknown_404(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(tmp_path)))
    assert client.get("/api/backfill/missing").status_code == 404


def test_backfill_current_empty_when_none(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(tmp_path)))
    resp = client.get("/api/backfill")
    assert resp.status_code == 200 and resp.json() == {}


# --- retention settings (Slice 29 / ADR-0013) --------------------------------


def test_retention_get_returns_seeded_defaults(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(tmp_path)))
    body = client.get("/api/retention").json()
    assert body["max_age_days"] == 30.0  # env default, seeded on bootstrap
    assert body["max_size_gb"] is None


def test_retention_put_round_trips(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(tmp_path)))
    resp = client.put("/api/retention", json={"max_age_days": 7, "max_size_gb": 50})
    assert resp.status_code == 200
    assert resp.json() == {"max_age_days": 7.0, "max_size_gb": 50.0}
    # persisted: a fresh GET reflects it
    assert client.get("/api/retention").json() == {
        "max_age_days": 7.0,
        "max_size_gb": 50.0,
    }


def test_retention_put_both_off_allowed(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(tmp_path)))
    resp = client.put(
        "/api/retention", json={"max_age_days": None, "max_size_gb": None}
    )
    assert resp.status_code == 200
    assert resp.json() == {"max_age_days": None, "max_size_gb": None}


def test_retention_put_zero_age_disables(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(tmp_path)))
    resp = client.put("/api/retention", json={"max_age_days": 0, "max_size_gb": None})
    assert resp.status_code == 200
    assert resp.json()["max_age_days"] is None  # 0 → off


def test_retention_put_rejects_bad_input(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(tmp_path)))
    bad_age = client.put("/api/retention", json={"max_age_days": -1})
    assert bad_age.status_code == 400 and "max_age_days" in bad_age.json()["detail"]
    bad_gb = client.put("/api/retention", json={"max_size_gb": 0})
    assert bad_gb.status_code == 400 and "max_size_gb" in bad_gb.json()["detail"]
