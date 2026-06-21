"""render_sweep tests (Slice 26a): the decode-free render entry the chunks path uses."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from backscatter.config import Config, SeedLocation
from backscatter.decode.volume import Sweep
from backscatter.render.render import render_sweep


def _config(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "db.sqlite",
        poll_interval_s=0.0,
        site_override=None,
        seed_locations=(SeedLocation("Home", 39.3603, -104.5969, True),),
    )


def test_render_sweep_writes_png_named_like_the_volume(tmp_path: Path) -> None:
    az = (np.arange(720, dtype=np.float64) * 0.5) % 360.0
    ranges = 2125.0 + 250.0 * np.arange(400, dtype=np.float64)
    refl = np.ma.masked_all((720, 400), dtype=np.float64)
    refl[:60, :60] = 40.0  # a small cell so something renders
    sweep = Sweep(
        site_id="KFTG",
        scan_time=datetime(2026, 6, 21, 21, 0, 0, tzinfo=UTC),
        elevation_deg=0.5,
        azimuths_deg=az,
        ranges_m=ranges,
        reflectivity=refl,
    )
    res = render_sweep(
        sweep, _config(tmp_path), site_icao="KFTG",
        scan_time=sweep.scan_time, out_dir=tmp_path / "out",
    )
    # PNG name matches the assembled volume convention so live + assembled coincide.
    assert res.png_path.name == "KFTG20260621_210000_V06.png"
    assert res.png_path.exists() and res.sidecar_path.exists()
    assert res.site == "KFTG" and res.width > 0 and res.height > 0
