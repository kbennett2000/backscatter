"""Tests for the serve layer: frame discovery + HTTP endpoints."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from backscatter.api.app import create_app
from backscatter.api.frames import latest_frame
from backscatter.config import Config

BOUNDS = {"west": -107.23, "south": 37.71, "east": -101.86, "north": 41.86}


def _config(tmp_path: Path) -> Config:
    return Config(
        lat=39.3603,
        lon=-104.5969,
        site="KFTG",
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "backscatter.db",
    )


def _write_frame(
    data_dir: Path,
    *,
    site: str = "KFTG",
    volume: str = "KFTG20260620_215107_V06",
    scan_time: str = "2026-06-20T21:51:07+00:00",
    bounds: dict[str, float] = BOUNDS,
) -> bytes:
    out = data_dir / "renders" / site
    out.mkdir(parents=True, exist_ok=True)
    img = np.zeros((2, 2, 4), dtype=np.uint8)
    img[0, 0] = (253, 0, 0, 255)  # one opaque pixel
    Image.fromarray(img, "RGBA").save(out / f"{volume}.png")
    png_bytes = (out / f"{volume}.png").read_bytes()
    sidecar = {
        "site": site,
        "scan_time": scan_time,
        "elevation_deg": 0.483,
        "field": "reflectivity",
        "crs": "EPSG:3857",
        "bounds_3857": [-11936795.6, 4539210.0, -11339174.8, 5139697.7],
        "bounds_wgs84": bounds,
        "width": 2,
        "height": 2,
        "max_range_km": 230.0,
        "source_volume": volume,
    }
    (out / f"{volume}.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return png_bytes


def test_latest_frame_none_when_empty(tmp_path: Path) -> None:
    assert latest_frame(tmp_path / "data") is None


def test_latest_frame_picks_newest(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _write_frame(
        data, volume="KFTG20260620_200000_V06", scan_time="2026-06-20T20:00:00+00:00"
    )
    _write_frame(
        data, volume="KFTG20260620_215107_V06", scan_time="2026-06-20T21:51:07+00:00"
    )
    frame = latest_frame(data)
    assert frame is not None
    assert frame.scan_time == "2026-06-20T21:51:07+00:00"
    assert frame.image_url == "/renders/KFTG/KFTG20260620_215107_V06.png"


def test_api_config_uses_config_center(tmp_path: Path) -> None:
    config = _config(tmp_path)
    client = TestClient(create_app(config))
    body = client.get("/api/config").json()
    assert body["center"] == [config.lon, config.lat]
    assert body["site"] == "KFTG"


def test_api_latest_matches_sidecar(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _write_frame(config.data_dir)
    client = TestClient(create_app(config))
    body = client.get("/api/latest").json()
    assert body["site"] == "KFTG"
    assert body["scan_time"] == "2026-06-20T21:51:07+00:00"
    assert body["bounds"] == BOUNDS  # round-trips exactly
    assert body["image_url"] == "/renders/KFTG/KFTG20260620_215107_V06.png"


def test_api_latest_404_when_empty(tmp_path: Path) -> None:
    client = TestClient(create_app(_config(tmp_path)))
    assert client.get("/api/latest").status_code == 404


def test_render_png_served_with_content_type(tmp_path: Path) -> None:
    config = _config(tmp_path)
    png_bytes = _write_frame(config.data_dir)
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
