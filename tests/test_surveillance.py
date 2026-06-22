"""Multi-cut 0.5° surveillance extraction (Slice 27a) — the SAILS correctness gate.

The new, risky logic is *which* sweeps are surveillance cuts and *what time* each one
starts; the geometry/color path that turns a chosen sweep into a frame is unchanged
(reused from 26a, already proven max-diff-0). So these tests pin the selection + the
per-sweep timestamp against KNOWN VALUES — synthetic split-cut/SAILS layouts plus a
real layout captured from a KFTG SAILS volume (``sails_KFTG_layout.npz``: the volume's
fixed_angle / sweep_start_ray_index / time arrays + per-sweep reflectivity counts).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from backscatter.decode.volume import surveillance_indices, sweep_start_time

LAYOUT = Path(__file__).parent / "fixtures" / "sails_KFTG_layout.npz"


def test_surveillance_indices_single_cut() -> None:
    """Clear-air / legacy volume (one visit to the lowest tilt) → exactly one cut."""
    # 0.5 base, then ascending tilts — the proven single-cut case.
    assert surveillance_indices(np.array([0.5, 1.5, 2.4, 3.4])) == [0]


def test_surveillance_indices_split_cut_no_sails() -> None:
    """A split cut (surveillance then Doppler at 0.5°) is ONE surveillance cut.

    Both halves are at the minimum elevation; only the first (surveillance) counts —
    the Doppler twin at index 1 is dropped."""
    assert surveillance_indices(np.array([0.5, 0.5, 1.5, 1.5, 2.4])) == [0]


def test_surveillance_indices_sails() -> None:
    """SAILS re-scans the lowest tilt mid-volume → the base cut plus each SAILS cut.

    Layout mirrors a real precip VCP: base split cut (0,1), higher tilts, a SAILS
    split cut (5,6), more tilts. Surveillance = the first sweep of each 0.5° visit."""
    fixed = np.array([0.5, 0.5, 0.9, 1.3, 1.8, 0.5, 0.5, 2.4])
    assert surveillance_indices(fixed) == [0, 5]


def test_surveillance_indices_real_layout() -> None:
    """Real KFTG SAILS volume: 4 sweeps at 0.5° (split base + SAILS) → cuts [0, 9].

    Independent cross-check: each surveillance half has MORE valid reflectivity gates
    than its Doppler twin (surveillance has the longer unambiguous range), confirming
    'first of the visit' selects the reflectivity-rich cut, not the Doppler one."""
    fx = np.load(LAYOUT)
    idx = surveillance_indices(fx["fixed_angle"])
    assert idx == [0, 9]
    counts = fx["refl_counts"]
    assert counts[0] > counts[1] and counts[9] > counts[10]


def test_sweep_start_times_real_layout() -> None:
    """Per-sweep start times: base == volume start, SAILS lands minutes later.

    The base cut (sweep 0) MUST equal the volume's _V06 timestamp (00:24:20) so the
    live row reconciles to its assembled volume; the SAILS cut (sweep 9) lands ~2.4 min
    later at 00:26:44. Times are strictly increasing."""
    fx = np.load(LAYOUT)
    units = str(fx["time_units"])
    times = [
        sweep_start_time(units, fx["time_data"], fx["sweep_start_ray_index"], i)
        for i in surveillance_indices(fx["fixed_angle"])
    ]
    assert times == [
        datetime(2026, 6, 22, 0, 24, 20, tzinfo=UTC),
        datetime(2026, 6, 22, 0, 26, 44, tzinfo=UTC),
    ]
    assert times[0] < times[1]


def test_base_surveillance_time_is_whole_seconds_utc() -> None:
    """Timestamps are tz-aware UTC, microseconds truncated (like scan_time)."""
    fx = np.load(LAYOUT)
    t = sweep_start_time(
        str(fx["time_units"]), fx["time_data"], fx["sweep_start_ray_index"], 0
    )
    assert t.tzinfo is UTC and t.microsecond == 0
