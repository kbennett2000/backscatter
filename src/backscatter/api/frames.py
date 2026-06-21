"""The newest rendered frame, read from the SQLite index.

The collect loop records each render in the index (ADR-0003), so the index is the
single source of truth for "what's the latest frame" — no scanning render files off
disk.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from backscatter.store import db

RENDERS_SUBDIR = "renders"

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


def latest_frame(conn: sqlite3.Connection) -> FrameMeta | None:
    """Return the newest rendered frame from the index, or None."""
    row = db.latest_rendered_frame(conn)
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
