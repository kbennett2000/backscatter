"""Reflectivity color table (dBZ → RGBA).

The classic NWS NEXRAD reflectivity scale, in discrete 5-dBZ steps. Discrete (not
interpolated) so a value's color is exact and testable at every breakpoint. Below
the lowest threshold — and any NaN / masked gate — renders fully transparent.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

# (lower dBZ bound inclusive, (R, G, B)). Classic NWS reflectivity palette.
NWS_REFLECTIVITY: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (5.0, (4, 233, 231)),
    (10.0, (1, 159, 244)),
    (15.0, (3, 0, 244)),
    (20.0, (2, 253, 2)),
    (25.0, (1, 197, 1)),
    (30.0, (0, 142, 0)),
    (35.0, (253, 248, 2)),
    (40.0, (229, 188, 0)),
    (45.0, (253, 149, 0)),
    (50.0, (253, 0, 0)),
    (55.0, (212, 0, 0)),
    (60.0, (188, 0, 0)),
    (65.0, (248, 0, 253)),
    (70.0, (152, 84, 198)),
    (75.0, (255, 255, 255)),
)

_THRESHOLDS = np.array([t for t, _ in NWS_REFLECTIVITY], dtype=np.float64)
_COLORS = np.array([c for _, c in NWS_REFLECTIVITY], dtype=np.uint8)


def dbz_to_rgba(grid: NDArray[np.float64]) -> NDArray[np.uint8]:
    """Map a 2D dBZ array to an ``(H, W, 4)`` uint8 RGBA image.

    NaN and sub-threshold (< lowest bound) cells are transparent (alpha 0).
    """
    height, width = grid.shape
    rgba = np.zeros((height, width, 4), dtype=np.uint8)

    # Bucket index for each cell: index into NWS_REFLECTIVITY, or -1 if below range.
    idx = np.searchsorted(_THRESHOLDS, grid, side="right") - 1
    visible = (idx >= 0) & ~np.isnan(grid)

    sel = idx[visible]
    rgba[visible, :3] = _COLORS[sel]
    rgba[visible, 3] = 255
    return rgba
