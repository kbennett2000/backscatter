"""Synthetic-sweep tests for the rasterizer — the orientation/flip guard.

A single bright gate at a known azimuth must land in the correct part of the image:
north -> top, east -> right. This is where a vertical/horizontal flip hides.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from backscatter.decode.volume import Sweep
from backscatter.render.geometry import lonlat_to_mercator, polar_to_lonlat
from backscatter.render.raster import rasterize

SITE_LAT, SITE_LON = 39.7866, -104.5458
ELEV = 0.5


def _sweep_with_gate(target_az: float, target_range_m: float) -> tuple[Sweep, float]:
    """A sweep (unsorted rays, like real data) with a small strong cell near target.

    The cell spans a few rays × a few gates so it reliably covers pixel centers
    under nearest-neighbour inverse mapping (a single gate is sub-pixel).
    """
    az = (np.arange(720, dtype=np.float64) * 0.5 + 155.0) % 360.0  # starts mid-rotation
    ranges = 2125.0 + 250.0 * np.arange(400, dtype=np.float64)

    refl = np.ma.masked_all((720, 400), dtype=np.float64)
    raw = np.abs(az - target_az) % 360.0
    circ = np.minimum(raw, 360.0 - raw)
    ray = int(np.argmin(circ))
    gate = int(np.argmin(np.abs(ranges - target_range_m)))
    # A ~7-ray x 7-gate patch centered on (ray, gate); rays are index-contiguous
    # in azimuth so this is a small angular sector, not scattered.
    rays = np.arange(ray - 3, ray + 4) % 720
    gates = np.arange(gate - 3, gate + 4)
    refl[np.ix_(rays, gates)] = 60.0

    sweep = Sweep(
        site_id="KFTG",
        scan_time=datetime(2026, 6, 20, 21, 51, 7, tzinfo=UTC),
        elevation_deg=ELEV,
        azimuths_deg=az,
        ranges_m=ranges,
        reflectivity=refl,
    )
    return sweep, ranges[gate]


def test_north_gate_lands_in_top_half() -> None:
    sweep, _r = _sweep_with_gate(0.0, 50_000.0)
    res = rasterize(sweep, SITE_LAT, SITE_LON, max_range_km=70, pixel_size_m=1000)
    rows, cols = np.where(~np.isnan(res.dbz))
    assert rows.size > 0
    assert rows.mean() < res.height / 2  # north -> top
    assert abs(cols.mean() - res.width / 2) < res.width * 0.15  # ~centered in x


def test_east_gate_lands_in_right_half() -> None:
    sweep, _r = _sweep_with_gate(90.0, 50_000.0)
    res = rasterize(sweep, SITE_LAT, SITE_LON, max_range_km=70, pixel_size_m=1000)
    rows, cols = np.where(~np.isnan(res.dbz))
    assert cols.size > 0
    assert cols.mean() > res.width / 2  # east -> right
    assert abs(rows.mean() - res.height / 2) < res.height * 0.15  # ~centered in y


def test_blob_matches_polar_to_lonlat_projection() -> None:
    # The colored blob's centroid should sit where geometry says the gate is.
    sweep, gate_range = _sweep_with_gate(0.0, 50_000.0)
    res = rasterize(sweep, SITE_LAT, SITE_LON, max_range_km=70, pixel_size_m=1000)
    rows, cols = np.where(~np.isnan(res.dbz))

    lon, lat = polar_to_lonlat(SITE_LAT, SITE_LON, gate_range, 0.0, ELEV)
    mx, my = (float(v) for v in lonlat_to_mercator(lon, lat))
    x_min, y_min, x_max, y_max = res.bounds_3857
    exp_col = (mx - x_min) / (x_max - x_min) * res.width
    exp_row = (y_max - my) / (y_max - y_min) * res.height

    assert abs(cols.mean() - exp_col) < 3  # within a few pixels
    assert abs(rows.mean() - exp_row) < 3


def test_bounds_are_ordered() -> None:
    sweep, _r = _sweep_with_gate(0.0, 50_000.0)
    res = rasterize(sweep, SITE_LAT, SITE_LON, max_range_km=70, pixel_size_m=1000)
    west, south, east, north = res.bounds_wgs84
    assert west < east and south < north
    x_min, y_min, x_max, y_max = res.bounds_3857
    assert x_min < x_max and y_min < y_max
