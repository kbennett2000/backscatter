"""Value-based tests for cross-frame storm-cell association + motion (Slice 28b).

Synthetic cells displaced by *known* azimuth/distance over a known Δt let us assert
both the bookkeeping (track-id continuity, new/lost tracks, no resurrection) and the
estimated motion (ground velocity within tolerance of the analytic value). The
crossing-cells case proves predict-forward + optimal assignment keeps identities
right where a naive nearest-centroid match would swap them.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable

import pytest

from backscatter.render.geometry import ground_destination
from backscatter.track.associate import (
    MAX_SPEED_MS,
    MIN_RADIUS_M,
    Candidate,
    TrackedCell,
    associate,
    associate_candidates,
)
from backscatter.track.detect import Cell

LON0, LAT0 = -104.5, 39.8
DT = 300.0  # 5 min, a typical inter-volume cadence


def _ids(start: int = 100) -> Callable[[], int]:
    counter = itertools.count(start)
    return lambda: next(counter)


def _cell(lon: float, lat: float, dbz: float = 50.0, area: float = 20.0) -> Cell:
    return Cell(centroid_lon=lon, centroid_lat=lat, max_dbz=dbz, area_km2=area)


def _moved(lon: float, lat: float, az_deg: float, dist_m: float) -> Cell:
    nlon, nlat = ground_destination(lat, lon, az_deg, dist_m)
    return _cell(nlon, nlat)


def test_continuity_and_motion_from_known_displacement() -> None:
    prev = [TrackedCell(_cell(LON0, LAT0), track_id=7, u_ms=0.0, v_ms=0.0)]
    # Move due east 6 km over 5 min → 20 m/s eastward, ~0 northward.
    curr = [_moved(LON0, LAT0, 90.0, 6_000.0)]

    alloc = _ids()
    (out,) = associate(prev, curr, DT, allocate_id=alloc)

    assert out.track_id == 7  # continued, not a fresh id
    assert out.u_ms == pytest.approx(20.0, abs=0.05)
    assert out.v_ms == pytest.approx(0.0, abs=0.05)


def test_new_cell_with_no_predecessor_gets_fresh_id_and_zero_motion() -> None:
    out = associate([], [_cell(LON0, LAT0)], DT, allocate_id=_ids(42))
    assert len(out) == 1
    assert out[0].track_id == 42
    assert (out[0].u_ms, out[0].v_ms) == (0.0, 0.0)


def test_lost_track_is_not_carried_forward() -> None:
    prev = [
        TrackedCell(_cell(LON0, LAT0), track_id=7, u_ms=0.0, v_ms=0.0),
        TrackedCell(_cell(LON0 + 1.0, LAT0), track_id=8, u_ms=0.0, v_ms=0.0),
    ]
    # Only one cell this frame, near track 7; track 8 vanishes.
    curr = [_moved(LON0, LAT0, 90.0, 3_000.0)]

    (out,) = associate(prev, curr, DT, allocate_id=_ids())
    assert out.track_id == 7  # 8 is simply gone, not matched


def test_reappearance_gets_new_id_no_resurrection() -> None:
    # A cell appears where a *different* prior track lived, but that track isn't in
    # `prev` (it vanished a frame earlier). Association only sees the immediate prev,
    # so the reappearance is correctly a new track, not a resurrection of id 7.
    prev = [TrackedCell(_cell(LON0 + 1.0, LAT0), track_id=9, u_ms=0.0, v_ms=0.0)]
    curr = [_cell(LON0, LAT0)]  # back at the old id-7 spot, far from id 9
    (out,) = associate(prev, curr, DT, allocate_id=_ids(500))
    assert out.track_id == 500


def test_crossing_cells_keep_identity_via_predicted_position() -> None:
    # Two cells genuinely crossing: separation ~0.05° (~4.3 km) but each travels 6 km,
    # so A (west, heading east) ends up EAST of B (east, heading west) — they swap
    # sides. A naive nearest-centroid match (ignoring motion) would assign A to the
    # western curr (nearest its *old* position) and swap the ids; predict-forward + the
    # optimal assignment must keep them straight.
    a = TrackedCell(_cell(LON0 - 0.05, LAT0), track_id=1, u_ms=20.0, v_ms=0.0)
    b = TrackedCell(_cell(LON0 + 0.05, LAT0), track_id=2, u_ms=-20.0, v_ms=0.0)
    step = 20.0 * DT
    a_pred = ground_destination(a.cell.centroid_lat, a.cell.centroid_lon, 90.0, step)
    b_pred = ground_destination(b.cell.centroid_lat, b.cell.centroid_lon, 270.0, step)
    curr = [_cell(*a_pred), _cell(*b_pred)]  # curr[0] at A's prediction, curr[1] at B's

    out = associate([a, b], curr, DT, allocate_id=_ids())
    by_lon = sorted(out, key=lambda t: t.cell.centroid_lon)
    # A has moved east of B and keeps id 1; B is now westernmost and keeps id 2.
    assert by_lon[-1].track_id == 1  # easternmost is A (it crossed over)
    assert by_lon[0].track_id == 2  # westernmost is B


def test_beyond_radius_jump_is_not_linked() -> None:
    prev = [TrackedCell(_cell(LON0, LAT0), track_id=7, u_ms=0.0, v_ms=0.0)]
    far = _moved(LON0, LAT0, 90.0, 100_000.0)  # 100 km in 5 min → ~333 m/s, a teleport
    (out,) = associate(prev, [far], DT, allocate_id=_ids(900))
    assert out.track_id == 900  # new track, not a 333 m/s "continuation"


def test_zero_dt_starts_fresh_tracks() -> None:
    prev = [TrackedCell(_cell(LON0, LAT0), track_id=7, u_ms=0.0, v_ms=0.0)]
    (out,) = associate(prev, [_cell(LON0, LAT0)], 0.0, allocate_id=_ids(800))
    assert out.track_id == 800


def test_empty_curr_returns_empty() -> None:
    prev = [TrackedCell(_cell(LON0, LAT0), track_id=7, u_ms=0.0, v_ms=0.0)]
    assert associate(prev, [], DT, allocate_id=_ids()) == []


# --- coasting (Slice 28e): a candidate aged across a missed frame ---------------


def test_coast_resume_within_radius_keeps_id() -> None:
    # Track 7 moving east at 10 m/s, last seen 2 frames ago (it missed one). The cell
    # reappears where its motion would have carried it → resumes the SAME id.
    track = TrackedCell(_cell(LON0, LAT0), track_id=7, u_ms=10.0, v_ms=0.0)
    age = 2 * DT
    reappear = _moved(LON0, LAT0, 90.0, 10.0 * age)  # exactly the coasted position
    (out,) = associate_candidates(
        [Candidate(track, age)], [reappear], allocate_id=_ids(900)
    )
    assert out.track_id == 7  # resumed, not a fresh id
    assert out.u_ms == pytest.approx(10.0, abs=0.1)


def test_coast_beyond_radius_gets_new_id() -> None:
    # A stationary-motion candidate (predicts to itself) aged 2 frames; a cell beyond
    # the age-scaled radius is a different cell → new id, no false resume.
    track = TrackedCell(_cell(LON0, LAT0), track_id=7, u_ms=0.0, v_ms=0.0)
    age = 2 * DT
    radius = max(MIN_RADIUS_M, MAX_SPEED_MS * age)
    far = _moved(LON0, LAT0, 90.0, radius + 5_000.0)
    (out,) = associate_candidates([Candidate(track, age)], [far], allocate_id=_ids(900))
    assert out.track_id == 900


def test_motion_smoothing_blends_with_prior() -> None:
    # Prior motion 10 m/s east; measured step 20 m/s east → EMA(0.5) = 15 m/s east.
    prev = [TrackedCell(_cell(LON0, LAT0), track_id=7, u_ms=10.0, v_ms=0.0)]
    # Place curr at where a 20 m/s eastward step lands (measured = 20), but the cell
    # predicted from 10 m/s only reaches half as far — still well within radius.
    curr = [_moved(LON0, LAT0, 90.0, 20.0 * DT)]
    (out,) = associate(prev, curr, DT, allocate_id=_ids())
    assert out.track_id == 7
    assert out.u_ms == pytest.approx(15.0, abs=0.1)
    assert math.isclose(out.v_ms, 0.0, abs_tol=0.05)
