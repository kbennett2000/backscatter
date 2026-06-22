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
    """Associate this frame's ``curr`` cells with the previous frame's ``prev`` tracks.

    Returns one :class:`TrackedCell` per ``curr`` cell, in the same order. Matched
    cells inherit their predecessor's ``track_id`` and an EMA-smoothed motion; new
    cells get a fresh id (via ``allocate_id``) and zero motion; vanished ``prev``
    tracks simply end. With no usable predecessor frame (empty ``prev`` or
    non-positive ``dt_s`` — e.g. an archive gap), every cell starts a new track.
    """
    if not curr:
        return []
    if not prev or dt_s <= 0:
        return [TrackedCell(c, allocate_id(), 0.0, 0.0) for c in curr]

    # Cost matrix: ground distance from each prev's predicted position to each curr.
    pred = [_predict_forward(tc, dt_s) for tc in prev]
    cost = np.empty((len(prev), len(curr)), dtype=np.float64)
    for i, (plon, plat) in enumerate(pred):
        for j, c in enumerate(curr):
            _az, dist = geodesic_between(plon, plat, c.centroid_lon, c.centroid_lat)
            cost[i, j] = dist

    radius = max(MIN_RADIUS_M, MAX_SPEED_MS * dt_s)
    row_ind, col_ind = linear_sum_assignment(cost)
    # curr index → prev index, keeping only assignments within the search radius.
    matched: dict[int, int] = {
        int(j): int(i)
        for i, j in zip(row_ind, col_ind, strict=True)
        if cost[i, j] <= radius
    }

    result: list[TrackedCell] = []
    for j, c in enumerate(curr):
        if j not in matched:
            result.append(TrackedCell(c, allocate_id(), 0.0, 0.0))
            continue
        p = prev[matched[j]]
        # Measured step velocity from the actual (not predicted) prior centroid.
        mu, mv = _velocity(
            p.cell.centroid_lon, p.cell.centroid_lat,
            c.centroid_lon, c.centroid_lat, dt_s,
        )
        if math.hypot(p.u_ms, p.v_ms) == 0.0:
            u, v = mu, mv  # first continuation: no prior motion to blend
        else:
            a = MOTION_SMOOTHING
            u = a * mu + (1.0 - a) * p.u_ms
            v = a * mv + (1.0 - a) * p.v_ms
        result.append(TrackedCell(c, p.track_id, u, v))
    return result
