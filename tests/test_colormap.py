"""Value tests for the dBZ → RGBA color mapping at known breakpoints."""

from __future__ import annotations

import numpy as np

from backscatter.render.colormap import NWS_REFLECTIVITY, dbz_to_rgba


def test_breakpoints_map_to_exact_colors() -> None:
    grid = np.array([[5.0, 50.0, 75.0]])
    rgba = dbz_to_rgba(grid)
    # 5 dBZ -> first bucket, 50 -> the 50 bucket, 75 -> top bucket.
    assert tuple(rgba[0, 0]) == (4, 233, 231, 255)
    assert tuple(rgba[0, 1]) == (253, 0, 0, 255)
    assert tuple(rgba[0, 2]) == (255, 255, 255, 255)


def test_value_inside_bucket_uses_lower_bound_color() -> None:
    # 52 dBZ falls in the [50, 55) bucket -> the 50 dBZ color.
    rgba = dbz_to_rgba(np.array([[52.0]]))
    assert tuple(rgba[0, 0]) == (253, 0, 0, 255)


def test_below_threshold_and_nan_are_transparent() -> None:
    rgba = dbz_to_rgba(np.array([[4.9, -10.0, np.nan]]))
    assert rgba[0, 0, 3] == 0
    assert rgba[0, 1, 3] == 0
    assert rgba[0, 2, 3] == 0


def test_every_breakpoint_is_opaque_and_correct() -> None:
    grid = np.array([[t for t, _ in NWS_REFLECTIVITY]])
    rgba = dbz_to_rgba(grid)
    for i, (_dbz, color) in enumerate(NWS_REFLECTIVITY):
        assert tuple(rgba[0, i]) == (*color, 255)
