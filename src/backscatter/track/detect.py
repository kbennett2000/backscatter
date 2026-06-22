"""Identify storm cells in a rasterized reflectivity grid (Slice 28a).

The SCIT/TITAN identification step, simplified to our 2-D lowest-tilt grid: threshold
the dBZ field, label connected components, drop specks below a minimum area, and reduce
each surviving component to an intensity-weighted centroid + its peak dBZ and ground
area. Cross-frame association and motion (the persistent track id) come in Slice 28b;
this module is per-frame only.

Geometry is exact reuse of the renderer's grid: a cell's pixel centroid is mapped back
to lon/lat with the **same** ``PIXEL_SIZE_M`` origin convention ``rasterize`` used to
build the grid (x east from ``x_min``, y down from ``y_max``), so a cell lands exactly
where the painted reflectivity does. Area corrects the Web-Mercator scale (a 250 m
pixel is ``sec(lat)`` too wide on the ground) by the ``cos(lat)`` factor — otherwise
areas would read ~70% high at CONUS mid-latitudes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from backscatter.render.geometry import mercator_to_lonlat
from backscatter.render.raster import PIXEL_SIZE_M

# A convective cell floor. 40 dBZ is the conventional storm-cell threshold (heavy rain /
# the SCIT default family); below this is general precip, not a trackable cell.
DEFAULT_DBZ_THRESHOLD = 40.0
# Drop components smaller than this real ground area — single-pixel speckle and clutter,
# not storms. ~10 km^2 ≈ a 3 km cell, the small end of what's worth a marker.
DEFAULT_MIN_AREA_KM2 = 10.0


@dataclass(frozen=True)
class Cell:
    """One identified storm cell in a single frame (no track identity yet)."""

    centroid_lon: float
    centroid_lat: float
    max_dbz: float
    area_km2: float


def detect_cells(
    dbz: NDArray[np.float64],
    bounds_3857: tuple[float, float, float, float],
    *,
    threshold_dbz: float = DEFAULT_DBZ_THRESHOLD,
    min_area_km2: float = DEFAULT_MIN_AREA_KM2,
    pixel_size_m: float = PIXEL_SIZE_M,
) -> list[Cell]:
    """Identify storm cells in a rasterized dBZ grid.

    ``dbz`` is the ``RasterResult.dbz`` grid (row 0 = north, NaN = no data) and
    ``bounds_3857`` its ``(x_min, y_min, x_max, y_max)`` Web-Mercator extent — both
    straight off the renderer, so no geometry is re-derived here. Returns cells whose
    ground area is at least ``min_area_km2``, in descending peak-dBZ order (strongest
    first) for stable, meaningful output.
    """
    mask = np.isfinite(dbz) & (dbz >= threshold_dbz)
    if not mask.any():
        return []

    labeled, n = ndimage.label(mask)
    if n == 0:
        return []

    index = list(range(1, n + 1))
    # Intensity-weighted centroid (the SCIT "mass" centroid): weight by dBZ inside the
    # cell, 0 elsewhere so no-data/NaN never enters the sum.
    weights = np.where(mask, dbz, 0.0)
    centroids = ndimage.center_of_mass(weights, labeled, index)
    maxima = ndimage.maximum(dbz, labeled, index)
    counts = np.bincount(labeled.ravel(), minlength=n + 1)[1:]

    x_min, _y_min, _x_max, y_max = bounds_3857
    cells: list[Cell] = []
    for (row, col), peak, count in zip(centroids, maxima, counts, strict=True):
        x = x_min + (col + 0.5) * pixel_size_m
        y = y_max - (row + 0.5) * pixel_size_m
        lon_m, lat_m = mercator_to_lonlat(x, y)
        lon, lat = float(lon_m), float(lat_m)
        # Web-Mercator overstates ground distance by sec(lat); a pixel's true ground
        # side is pixel_size_m * cos(lat), so its ground area scales by cos(lat)^2.
        ground_px_m = pixel_size_m * math.cos(math.radians(lat))
        area_km2 = int(count) * (ground_px_m * ground_px_m) / 1_000_000.0
        if area_km2 < min_area_km2:
            continue
        cells.append(
            Cell(
                centroid_lon=lon,
                centroid_lat=lat,
                max_dbz=float(peak),
                area_km2=area_km2,
            )
        )

    cells.sort(key=lambda c: c.max_dbz, reverse=True)
    return cells
