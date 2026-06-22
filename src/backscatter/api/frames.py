"""The newest rendered frame, read from the SQLite index.

The collect loop records each render in the index (ADR-0003), so the index is the
single source of truth for "what's the latest frame" — no scanning render files off
disk.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from backscatter.store import db

RENDERS_SUBDIR = "renders"

# How far ahead the estimated-motion vector projects a cell, in minutes. The drawn
# arrow runs from the cell to where its current motion would carry it in this long,
# so the arrow's length encodes speed. Estimation only — not a nowcast. This is the
# single source of truth for the horizon; the overlay (web/stormtracks.js) draws the
# arrow itself and mirrors this value (TICK_COUNT × TICK_INTERVAL_MIN).
PROJECTION_MINUTES = 30.0
# Below this ground speed a cell is treated as stationary (new tracks have zero
# motion until a second frame); bearing is nulled so no zero-length arrow is drawn.
_MIN_MOTION_MS = 0.5
# How many observations (births + continuations) a track needs before its motion is
# trusted enough to draw a vector (Slice 28f). 3 = birth + 2 measured steps, so the
# EMA has blended ≥2 steps and a single first-step fluke never draws a full arrow; the
# marker still shows. Below this, bearing is nulled (marker only).
_MIN_TRACK_OBS = 3

# How many frames /api/frames returns by default, and the hard ceiling. The
# timeline window is bounded by "most recent N" rather than handing back an
# unbounded archive; pagination can come later if a deeper window is needed.
DEFAULT_FRAMES_LIMIT = 500
MAX_FRAMES_LIMIT = 2000


@dataclass(frozen=True)
class FrameMeta:
    """Everything the frontend needs to place one frame on the map."""

    site: str
    scan_time: str  # ISO-8601 UTC
    elevation_deg: float
    width: int
    height: int
    bounds: dict[str, float]  # west, south, east, north (WGS84)
    image_url: str  # served path, e.g. /renders/KFTG/KFTG..._V06.png

    def to_json(self) -> dict[str, object]:
        return {
            "site": self.site,
            "scan_time": self.scan_time,
            "elevation_deg": self.elevation_deg,
            "width": self.width,
            "height": self.height,
            "bounds": self.bounds,
            "image_url": self.image_url,
        }


def renders_dir(data_dir: Path) -> Path:
    """Directory holding rendered frames for a given data dir."""
    return data_dir / RENDERS_SUBDIR


def _frame_from_row(row: sqlite3.Row) -> FrameMeta:
    return FrameMeta(
        site=row["site"],
        scan_time=row["scan_time"],
        elevation_deg=row["elevation_deg"],
        width=row["width"],
        height=row["height"],
        bounds={
            "west": row["bounds_west"],
            "south": row["bounds_south"],
            "east": row["bounds_east"],
            "north": row["bounds_north"],
        },
        image_url=f"/renders/{row['image_path']}",
    )


def latest_frame(
    conn: sqlite3.Connection, site: str | None = None
) -> FrameMeta | None:
    """Return the newest rendered frame (optionally for one site), or None."""
    row = db.latest_rendered_frame(conn, site)
    return _frame_from_row(row) if row is not None else None


def frames_in_range(
    conn: sqlite3.Connection,
    *,
    site: str,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = DEFAULT_FRAMES_LIMIT,
) -> list[FrameMeta]:
    """Most-recent `limit` rendered frames for a site, oldest-first (the default)."""
    rows = db.rendered_frames(conn, site=site, start=start, end=end, limit=limit)
    return [_frame_from_row(row) for row in rows]


def frames_window(
    conn: sqlite3.Connection,
    *,
    site: str,
    start: datetime | None,
    end: datetime | None,
    after: datetime | None,
    limit: int,
) -> list[FrameMeta]:
    """One ascending page of frames for forward cursor-pagination."""
    rows = db.frames_window(
        conn, site=site, start=start, end=end, after=after, limit=limit
    )
    return [_frame_from_row(row) for row in rows]


def frames_extent(
    conn: sqlite3.Connection, *, site: str
) -> tuple[str | None, str | None, int]:
    """(min scan_time, max scan_time, count) of rendered frames for a site."""
    return db.frames_extent(conn, site=site)


def cell_payload(row: sqlite3.Row) -> dict[str, object]:
    """Shape one stored cell row for the map overlay (Slice 28c).

    Derives display fields from the stored ground velocity ``(u_ms east, v_ms north)``:
    speed (km/h) and bearing (deg cw from north, the direction the cell is *heading*).
    The overlay (``web/stormtracks.js``) draws the vector itself from speed+bearing over
    the ``PROJECTION_MINUTES`` horizon, so the backend returns motion, not an endpoint.

    A near-stationary cell, or one whose track has too few observations to trust its
    motion yet, gets ``bearing_deg = None`` so no arrow is drawn — just the marker.
    """
    u = row["u_ms"] if row["u_ms"] is not None else 0.0
    v = row["v_ms"] if row["v_ms"] is not None else 0.0
    speed = math.hypot(u, v)
    n_obs = row["n_obs"] if row["n_obs"] is not None else 1

    bearing: float | None = None
    if speed >= _MIN_MOTION_MS and n_obs >= _MIN_TRACK_OBS:
        bearing = math.degrees(math.atan2(u, v)) % 360.0  # u=east, v=north → cw-N

    return {
        "track_id": row["track_id"],
        "lon": row["centroid_lon"],
        "lat": row["centroid_lat"],
        "max_dbz": row["max_dbz"],
        "area_km2": row["area_km2"],
        "speed_kmh": round(speed * 3.6, 1),
        "bearing_deg": round(bearing, 1) if bearing is not None else None,
    }


def frame_cells(
    conn: sqlite3.Connection, *, site: str, scan_time: datetime
) -> list[dict[str, object]]:
    """All cells for one frame, shaped for the overlay (strongest dBZ first)."""
    rows = db.cells_for_frame(conn, site=site, scan_time=scan_time)
    return [cell_payload(row) for row in rows]
