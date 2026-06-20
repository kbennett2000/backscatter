"""Rasterize a polar reflectivity sweep onto a Web Mercator grid.

Inverse mapping: for every output pixel we compute (azimuth, ground range) back to
the radar and sample the nearest gate. This is exact (no scatter-gridding seams)
and makes the geometry directly testable. Row 0 of the output is **north** (top).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import cast

import numpy as np
from numpy.typing import NDArray

from backscatter.decode.volume import Sweep
from backscatter.render.geometry import (
    beam_ground_range,
    geodesic_inverse,
    ground_destination,
    lonlat_to_mercator,
    mercator_to_lonlat,
)
from backscatter.sites.select import COVERAGE_RANGE_KM

# Native gate spacing; output pixels match it so we neither up- nor down-sample.
PIXEL_SIZE_M = 250.0


@dataclass(frozen=True)
class RasterResult:
    """A georeferenced dBZ grid (NaN = no data) plus its extent."""

    dbz: NDArray[np.float64]  # (height, width), row 0 = north
    width: int
    height: int
    bounds_3857: tuple[float, float, float, float]  # xmin, ymin, xmax, ymax
    bounds_wgs84: tuple[float, float, float, float]  # west, south, east, north
    max_range_m: float


def _circular_diff(
    a: NDArray[np.float64], b: NDArray[np.float64]
) -> NDArray[np.float64]:
    """Smallest absolute angular difference (degrees) between two azimuth arrays."""
    d = np.abs(a - b) % 360.0
    return np.minimum(d, 360.0 - d)


def _nearest_ray(
    az_query: NDArray[np.float64], azimuths: NDArray[np.float64]
) -> NDArray[np.intp]:
    """Index of the nearest ray for each query azimuth, handling unsorted rays."""
    nrays = azimuths.shape[0]
    order = np.argsort(azimuths)
    az_sorted = azimuths[order]

    pos = np.searchsorted(az_sorted, az_query, side="left")
    hi = pos % nrays
    lo = (pos - 1) % nrays
    diff_hi = _circular_diff(az_query, az_sorted[hi])
    diff_lo = _circular_diff(az_query, az_sorted[lo])
    choose = np.where(diff_lo <= diff_hi, lo, hi)
    return cast("NDArray[np.intp]", order[choose])


def _nearest_gate(
    dist: NDArray[np.float64], gate_ground: NDArray[np.float64]
) -> NDArray[np.intp]:
    """Index of the nearest range gate (by ground range) for each distance."""
    ngates = gate_ground.shape[0]
    idx = np.clip(np.searchsorted(gate_ground, dist), 1, ngates - 1)
    take_lo = (dist - gate_ground[idx - 1]) <= (gate_ground[idx] - dist)
    return cast("NDArray[np.intp]", np.where(take_lo, idx - 1, idx))


def rasterize(
    sweep: Sweep,
    site_lat: float,
    site_lon: float,
    *,
    max_range_km: float = COVERAGE_RANGE_KM,
    pixel_size_m: float = PIXEL_SIZE_M,
) -> RasterResult:
    """Project ``sweep`` to a Web Mercator dBZ grid centered on the site."""
    gate_ground = np.asarray(
        beam_ground_range(sweep.ranges_m, sweep.elevation_deg), dtype=np.float64
    )
    max_range_m = min(max_range_km * 1000.0, float(gate_ground[-1]))

    # Coverage-circle bbox in WGS84 (cardinal extremes), then Mercator.
    west = ground_destination(site_lat, site_lon, 270.0, max_range_m)[0]
    east = ground_destination(site_lat, site_lon, 90.0, max_range_m)[0]
    south = ground_destination(site_lat, site_lon, 180.0, max_range_m)[1]
    north = ground_destination(site_lat, site_lon, 0.0, max_range_m)[1]
    x_min, y_min = (float(v) for v in lonlat_to_mercator(west, south))
    x_max, y_max = (float(v) for v in lonlat_to_mercator(east, north))

    width = max(1, math.ceil((x_max - x_min) / pixel_size_m))
    height = max(1, math.ceil((y_max - y_min) / pixel_size_m))

    # Pixel centers: x increasing east, y decreasing south so row 0 is north.
    xs = x_min + (np.arange(width, dtype=np.float64) + 0.5) * pixel_size_m
    ys = y_max - (np.arange(height, dtype=np.float64) + 0.5) * pixel_size_m
    grid_x, grid_y = np.meshgrid(xs, ys)

    lon, lat = mercator_to_lonlat(grid_x.ravel(), grid_y.ravel())
    az, dist = geodesic_inverse(site_lat, site_lon, lon, lat)
    az = az % 360.0

    ray_idx = _nearest_ray(az, sweep.azimuths_deg)
    gate_idx = _nearest_gate(dist, gate_ground)

    sampled = sweep.reflectivity[ray_idx, gate_idx]
    values = np.ma.filled(sampled.astype(np.float64), np.nan)
    values[dist > max_range_m] = np.nan

    dbz = values.reshape(height, width)

    # Final bounds straight from the Mercator rectangle (exact round-trip).
    w2, s2 = (float(v) for v in mercator_to_lonlat(x_min, y_min))
    e2, n2 = (float(v) for v in mercator_to_lonlat(x_max, y_max))
    return RasterResult(
        dbz=dbz,
        width=width,
        height=height,
        bounds_3857=(x_min, y_min, x_max, y_max),
        bounds_wgs84=(w2, s2, e2, n2),
        max_range_m=max_range_m,
    )
