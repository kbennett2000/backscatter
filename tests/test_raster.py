"""Synthetic-sweep tests for the rasterizer — the orientation/flip guard.

A single bright gate at a known azimuth must land in the correct part of the image:
north -> top, east -> right. This is where a vertical/horizontal flip hides.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from backscatter.decode.volume import Sweep
from backscatter.render.colormap import dbz_to_rgba
from backscatter.render.geometry import lonlat_to_mercator, polar_to_lonlat
from backscatter.render.raster import (
    _bilinear_sample,
    _bracket_gates,
    _bracket_rays,
    rasterize,
)

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


# --- bilinear sampling (Slice 20) -------------------------------------------


def test_bracket_rays_weights_and_wrap() -> None:
    # Unsorted rays; weight is the fractional angular position lo->hi.
    az = np.array([30.0, 10.0, 40.0, 20.0])  # original indices 0..3
    lo, hi, w = _bracket_rays(np.array([15.0]), az)
    assert (int(lo[0]), int(hi[0])) == (1, 3)  # az 10 (idx1) -> az 20 (idx3)
    assert abs(float(w[0]) - 0.5) < 1e-9

    # Exactly on a ray -> w collapses so the blend returns that node.
    _lo, hi2, w2 = _bracket_rays(np.array([20.0]), az)
    assert int(hi2[0]) == 3 and abs(float(w2[0]) - 1.0) < 1e-9

    # Wrap: 0° sits halfway between a 359° ray and a 1° ray.
    azw = np.array([359.0, 1.0, 90.0, 180.0, 270.0])  # idx0=359, idx1=1
    lo3, hi3, w3 = _bracket_rays(np.array([0.0]), azw)
    assert (int(lo3[0]), int(hi3[0])) == (0, 1)  # 359 -> 1 across the seam
    assert abs(float(w3[0]) - 0.5) < 1e-9


def test_bracket_gates_weights_and_clip() -> None:
    g = np.array([0.0, 250.0, 500.0, 750.0])
    lo, hi, t = _bracket_gates(np.array([375.0]), g)
    assert (int(lo[0]), int(hi[0])) == (1, 2)
    assert abs(float(t[0]) - 0.5) < 1e-9

    # Exactly on a gate -> t=1 picks that gate as hi.
    _lo, hi2, t2 = _bracket_gates(np.array([500.0]), g)
    assert int(hi2[0]) == 2 and abs(float(t2[0]) - 1.0) < 1e-9

    # Beyond the last gate clamps (no extrapolation).
    lo3, hi3, t3 = _bracket_gates(np.array([9999.0]), g)
    assert (int(lo3[0]), int(hi3[0])) == (2, 3) and abs(float(t3[0]) - 1.0) < 1e-9


def test_bilinear_blend_on_node_and_halfway() -> None:
    # 1 ray-pair x 1 gate-pair; corners c00,c01,c10,c11 = (ray_lo/hi, gate_lo/hi).
    refl = np.ma.MaskedArray([[10.0, 20.0], [30.0, 40.0]], mask=False)
    idx = lambda v: np.array([v], dtype=np.intp)  # noqa: E731
    nn = np.array([-999.0])

    # Center of all four -> mean.
    out = _bilinear_sample(refl, idx(0), idx(1), idx(0), idx(1),
                           np.array([0.5]), np.array([0.5]), nn)
    assert abs(float(out[0]) - 25.0) < 1e-9

    # On a node (w=t=0) -> exactly c00.
    out0 = _bilinear_sample(refl, idx(0), idx(1), idx(0), idx(1),
                            np.array([0.0]), np.array([0.0]), nn)
    assert abs(float(out0[0]) - 10.0) < 1e-9

    # Halfway along gate only (w=0,t=0.5) -> blend of c00,c01 = 15.
    outg = _bilinear_sample(refl, idx(0), idx(1), idx(0), idx(1),
                            np.array([0.0]), np.array([0.5]), nn)
    assert abs(float(outg[0]) - 15.0) < 1e-9


def test_bilinear_masked_corner_falls_back_to_nn_no_invented_data() -> None:
    # One masked corner -> the pixel must NOT blend; it keeps the NN value.
    data = [[10.0, 20.0], [30.0, 40.0]]
    refl = np.ma.MaskedArray(data, mask=[[False, False], [False, True]])  # c11 masked
    idx = lambda v: np.array([v], dtype=np.intp)  # noqa: E731

    # nearest is a real gate -> keep that exact value, never the blend (25).
    out = _bilinear_sample(refl, idx(0), idx(1), idx(0), idx(1),
                           np.array([0.5]), np.array([0.5]), np.array([10.0]))
    assert float(out[0]) == 10.0  # NN value, not 25.0

    # nearest gate is itself no-data -> NaN (transparent), never invented.
    out_nan = _bilinear_sample(refl, idx(0), idx(1), idx(0), idx(1),
                               np.array([0.5]), np.array([0.5]), np.array([np.nan]))
    assert np.isnan(out_nan[0])


def test_end_to_end_blend_stays_in_range_and_interpolates() -> None:
    # All rays valid; a radial dBZ gradient over gates 100..199, masked elsewhere.
    az = (np.arange(720, dtype=np.float64) * 0.5 + 155.0) % 360.0
    ranges = 2125.0 + 250.0 * np.arange(400, dtype=np.float64)
    refl = np.ma.masked_all((720, 400), dtype=np.float64)
    vals = 40.0 + 0.2 * np.arange(100, dtype=np.float64)  # 40.0 .. 59.8
    refl[:, 100:200] = vals[np.newaxis, :]
    sweep = Sweep(
        site_id="KFTG", scan_time=datetime(2026, 6, 20, 21, 51, 7, tzinfo=UTC),
        elevation_deg=ELEV, azimuths_deg=az, ranges_m=ranges, reflectivity=refl,
    )
    res = rasterize(sweep, SITE_LAT, SITE_LON, max_range_km=70, pixel_size_m=500)
    finite = res.dbz[~np.isnan(res.dbz)]
    assert finite.size > 0
    # No invented data: every value stays within the real valid range (blend or NN).
    assert finite.min() >= 40.0 - 1e-6 and finite.max() <= 59.8 + 1e-6
    # Interpolation actually happened: many more distinct values than the 100 gates.
    assert np.unique(np.round(finite, 4)).size > 100


def test_subthreshold_and_nan_stay_transparent_after_blend() -> None:
    # A blend landing below the 5 dBZ palette floor stays transparent; one crossing
    # the floor colors. Confirms interpolation + color order preserves the contract.
    refl = np.ma.MaskedArray([[2.0, 4.0], [4.0, 6.0]], mask=False)
    idx = lambda v: np.array([v], dtype=np.intp)  # noqa: E731
    below = _bilinear_sample(refl, idx(0), idx(0), idx(0), idx(1),
                             np.array([0.0]), np.array([0.5]), np.array([np.nan]))
    assert abs(float(below[0]) - 3.0) < 1e-9  # blend of 2 and 4
    grid = np.array([[float(below[0]), np.nan, 5.0]])
    rgba = dbz_to_rgba(grid)
    assert rgba[0, 0, 3] == 0  # 3.0 dBZ < 5 floor -> transparent
    assert rgba[0, 1, 3] == 0  # NaN -> transparent
    assert rgba[0, 2, 3] == 255  # 5.0 dBZ -> colored
