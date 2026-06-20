"""Tests for the serve layer: /api/latest now reads the SQLite index."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from backscatter.api.app import create_app
from backscatter.api.frames import latest_frame
from backscatter.config import Config
from backscatter.store import db

BOUNDS = (-107.23, 37.71, -101.86, 41.86)  # west, south, east, north


def _config(tmp_path: Path) -> Config:
    return Config(
        lat=39.3603,
        lon=-104.5969,
        site="KFTG",
        site_override=False,
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
    write_png: bool = False,
) -> bytes:
    """Index a volume + render, optionally writing the PNG file under data/renders."""
    scan = datetime.fromisoformat(scan_time)
    conn = db.connect(config.db_path)
    db.init_db(conn)
    db.record_volume(
        conn, site=site, scan_time=scan, s3_key=f"k/{volume}",
        path=Path(volume), size_bytes=1, downloaded_at=scan,
    )
    image_path = f"{site}/{volume}.png"
    db.record_render(
        conn, site=site, scan_time=scan, image_path=image_path,
        elevation_deg=0.483, width=2, height=2, bounds=bounds, rendered_at=scan,
    )
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
