"""Cross-frame storm-cell association + motion (Slice 28b).

Turn per-frame cell detections (Slice 28a) into persistent tracks with estimated
motion, ported from the documented TITAN/SCIT method: predict each known cell
forward by its current motion, match predictions to this frame's detections by
minimum ground displacement (globally optimal assignment), and read each track's
velocity off the matched step. This is the pure, DB-free core — fully testable
against known displacements; the collect loop supplies ``prev`` from storage and
an id allocator.

Motion here is **estimation**, not the provably-correct render: it is surfaced
in-UI (Slice 28c) as estimated cell motion, never a nowcast. Velocities are true
ground m/s (east ``u``, north ``v``) — distances use the geodesic, not the
~sec(lat)-inflated Mercator grid.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from backscatter.render.geometry import geodesic_between, ground_destination
from backscatter.track.detect import Cell

# Generous storm-motion cap: 30 m/s ≈ 108 km/h. A candidate match implying motion
# faster than this over the inter-frame Δt is rejected as a teleport (a different
# storm), not linked into the track.
MAX_SPEED_MS = 30.0
# Floor on the search radius so a short Δt (dense SAILS cadence) still admits a
# plausible step; ~5 km covers a slow cell plus centroid jitter.
MIN_RADIUS_M = 5_000.0
# Velocity smoothing: blend the measured step velocity with the track's prior
# motion. 0.5 = equal weight — damps centroid jitter without lagging real motion.
MOTION_SMOOTHING = 0.5


@dataclass(frozen=True)
class TrackedCell:
    """A detected cell with its persistent track identity + estimated motion."""

    cell: Cell
    track_id: int
    u_ms: float  # ground velocity east, m/s
    v_ms: float  # ground velocity north, m/s


@dataclass(frozen=True)
class Candidate:
    """A prior track offered for matching, with its age since last real detection.

    ``age_s`` is the time since this track was actually detected (one frame for a
    normally-continuing track; several for one coasting through missed frames, Slice
    28e). Prediction, search radius, and the measured step velocity all scale by it.
    """

    track: TrackedCell
    age_s: float


def _predict_forward(tc: TrackedCell, dt_s: float) -> tuple[float, float]:
    """First-guess ``(lon, lat)`` for ``tc`` advanced along its motion over ``dt_s``.

    A cell with zero motion (its first appearance) predicts to its own centroid.
    """
    speed = math.hypot(tc.u_ms, tc.v_ms)
    if speed == 0.0:
        return tc.cell.centroid_lon, tc.cell.centroid_lat
    az = math.degrees(math.atan2(tc.u_ms, tc.v_ms)) % 360.0  # u=east, v=north → cw-N
    return ground_destination(
        tc.cell.centroid_lat, tc.cell.centroid_lon, az, speed * dt_s
    )


def _velocity(
    lon1: float, lat1: float, lon2: float, lat2: float, dt_s: float
) -> tuple[float, float]:
    """Ground velocity ``(u east, v north)`` m/s for a step from point 1 to point 2."""
    az, dist = geodesic_between(lon1, lat1, lon2, lat2)
    speed = dist / dt_s
    return speed * math.sin(math.radians(az)), speed * math.cos(math.radians(az))


def associate(
    prev: list[TrackedCell],
    curr: list[Cell],
    dt_s: float,
    *,
    allocate_id: Callable[[], int],
) -> list[TrackedCell]:
    """Associate ``curr`` against a single previous frame, all tracks the same age.

    Thin wrapper over :func:`associate_candidates` (every prior track aged by the same
    ``dt_s``). A non-positive ``dt_s`` (or empty ``prev``) means no usable predecessor,
    so every cell starts a new track.
    """
    return associate_candidates(
        [Candidate(p, dt_s) for p in prev], curr, allocate_id=allocate_id
    )


def associate_candidates(
    prev: list[Candidate],
    curr: list[Cell],
    *,
    allocate_id: Callable[[], int],
) -> list[TrackedCell]:
    """Associate this frame's ``curr`` cells with prior tracks (each with its own age).

    Returns one :class:`TrackedCell` per ``curr`` cell, in the same order. Each
    candidate is predicted forward by **its own** ``age_s`` (so a track coasting through
    a missed frame is matched at where it should now be), with a per-candidate search
    radius. A matched cell inherits the candidate's ``track_id`` and an EMA-smoothed
    motion (measured over that age, so a resumed track keeps moving); an unmatched cell
    gets a fresh id and zero motion; an unmatched candidate's track simply ends. With no
    usable candidate (none, or all non-positive age), every cell starts a new track.
    """
    if not curr:
        return []
    usable = [c for c in prev if c.age_s > 0]
    if not usable:
        return [TrackedCell(c, allocate_id(), 0.0, 0.0) for c in curr]

    # Cost matrix: ground distance from each candidate's predicted spot to each curr.
    pred = [_predict_forward(c.track, c.age_s) for c in usable]
    cost = np.empty((len(usable), len(curr)), dtype=np.float64)
    for i, (plon, plat) in enumerate(pred):
        for j, c in enumerate(curr):
            _az, dist = geodesic_between(plon, plat, c.centroid_lon, c.centroid_lat)
            cost[i, j] = dist

    # Per-candidate search radius: an older (coasting) track may have moved farther.
    radii = [max(MIN_RADIUS_M, MAX_SPEED_MS * c.age_s) for c in usable]
    row_ind, col_ind = linear_sum_assignment(cost)
    # curr index → candidate index, keeping assignments within that candidate's radius.
    matched: dict[int, int] = {
        int(j): int(i)
        for i, j in zip(row_ind, col_ind, strict=True)
        if cost[i, j] <= radii[i]
    }

    result: list[TrackedCell] = []
    for j, c in enumerate(curr):
        if j not in matched:
            result.append(TrackedCell(c, allocate_id(), 0.0, 0.0))
            continue
        cand = usable[matched[j]]
        p = cand.track
        # Measured step velocity from the actual prior centroid over the cand's age.
        mu, mv = _velocity(
            p.cell.centroid_lon, p.cell.centroid_lat,
            c.centroid_lon, c.centroid_lat, cand.age_s,
        )
        if math.hypot(p.u_ms, p.v_ms) == 0.0:
            u, v = mu, mv  # first continuation: no prior motion to blend
        else:
            a = MOTION_SMOOTHING
            u = a * mu + (1.0 - a) * p.u_ms
            v = a * mv + (1.0 - a) * p.v_ms
        result.append(TrackedCell(c, p.track_id, u, v))
    return result
