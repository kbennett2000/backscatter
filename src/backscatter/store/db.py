"""SQLite frame index — one row per stored volume scan.

The raw `_V06` files on disk are the source of truth (ADR-0003); this index records
what we have (including render status + the rendered frame's bounds) so playback,
dedupe, and "what's the latest frame" are simple SQL. Dedupe is keyed on
``(site, scan_time)``: the same volume scan is never stored twice.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS volumes (
    id            INTEGER PRIMARY KEY,
    site          TEXT    NOT NULL,
    scan_time     TEXT    NOT NULL,   -- ISO-8601 UTC
    s3_key        TEXT    NOT NULL,
    path          TEXT    NOT NULL,
    size_bytes    INTEGER NOT NULL,
    downloaded_at TEXT    NOT NULL,
    render_status TEXT    NOT NULL DEFAULT 'pending',  -- pending|rendered|failed
    image_path    TEXT,              -- rendered PNG, relative to data/renders
    rendered_at   TEXT,
    elevation_deg REAL,
    width         INTEGER,
    height        INTEGER,
    bounds_west   REAL,
    bounds_south  REAL,
    bounds_east   REAL,
    bounds_north  REAL,
    UNIQUE(site, scan_time)
);
"""

# Render columns, added to `volumes` after the base table. Old dev DBs created
# before Slice 5 are migrated by ALTER (see _ensure_render_columns).
_RENDER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("render_status", "TEXT NOT NULL DEFAULT 'pending'"),
    ("image_path", "TEXT"),
    ("rendered_at", "TEXT"),
    ("elevation_deg", "REAL"),
    ("width", "INTEGER"),
    ("height", "INTEGER"),
    ("bounds_west", "REAL"),
    ("bounds_south", "REAL"),
    ("bounds_east", "REAL"),
    ("bounds_north", "REAL"),
)


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (creating parent dirs as needed) and return a connection."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # WAL lets the serve process read while the collect process writes.
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_render_columns(conn: sqlite3.Connection) -> None:
    """Add any missing render columns to a pre-Slice-5 `volumes` table."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(volumes)")}
    for name, decl in _RENDER_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE volumes ADD COLUMN {name} {decl}")


def init_db(conn: sqlite3.Connection) -> None:
    """Create the schema (and migrate old DBs) if needed. Idempotent."""
    conn.executescript(_SCHEMA)
    _ensure_render_columns(conn)
    conn.commit()


def volume_exists(conn: sqlite3.Connection, site: str, scan_time: datetime) -> bool:
    """Return whether a volume for this site + scan time is already indexed."""
    row = conn.execute(
        "SELECT 1 FROM volumes WHERE site = ? AND scan_time = ? LIMIT 1",
        (site, scan_time.isoformat()),
    ).fetchone()
    return row is not None


def record_volume(
    conn: sqlite3.Connection,
    *,
    site: str,
    scan_time: datetime,
    s3_key: str,
    path: Path,
    size_bytes: int,
    downloaded_at: datetime,
) -> None:
    """Insert one volume row.

    The ``UNIQUE(site, scan_time)`` constraint is the dedupe backstop: a duplicate
    insert raises :class:`sqlite3.IntegrityError` even if a pre-check missed it.
    """
    conn.execute(
        "INSERT INTO volumes "
        "(site, scan_time, s3_key, path, size_bytes, downloaded_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            site,
            scan_time.isoformat(),
            s3_key,
            str(path),
            size_bytes,
            downloaded_at.isoformat(),
        ),
    )
    conn.commit()


def record_render(
    conn: sqlite3.Connection,
    *,
    site: str,
    scan_time: datetime,
    image_path: str,
    elevation_deg: float,
    width: int,
    height: int,
    bounds: tuple[float, float, float, float],  # west, south, east, north
    rendered_at: datetime,
) -> None:
    """Record a successful render against an existing volume row."""
    west, south, east, north = bounds
    conn.execute(
        "UPDATE volumes SET render_status = 'rendered', image_path = ?, "
        "rendered_at = ?, elevation_deg = ?, width = ?, height = ?, "
        "bounds_west = ?, bounds_south = ?, bounds_east = ?, bounds_north = ? "
        "WHERE site = ? AND scan_time = ?",
        (
            image_path,
            rendered_at.isoformat(),
            elevation_deg,
            width,
            height,
            west,
            south,
            east,
            north,
            site,
            scan_time.isoformat(),
        ),
    )
    conn.commit()


def mark_render_failed(
    conn: sqlite3.Connection, site: str, scan_time: datetime
) -> None:
    """Flag a volume's render as failed (the raw volume is still kept)."""
    conn.execute(
        "UPDATE volumes SET render_status = 'failed' "
        "WHERE site = ? AND scan_time = ?",
        (site, scan_time.isoformat()),
    )
    conn.commit()


def latest_rendered_frame(
    conn: sqlite3.Connection, site: str | None = None
) -> sqlite3.Row | None:
    """Newest rendered frame, optionally for one site, or None (incl. no table)."""
    sql = "SELECT * FROM volumes WHERE render_status = 'rendered'"
    params: list[object] = []
    if site is not None:
        sql += " AND site = ?"
        params.append(site)
    sql += " ORDER BY scan_time DESC LIMIT 1"
    try:
        row: sqlite3.Row | None = conn.execute(sql, params).fetchone()
        return row
    except sqlite3.OperationalError:
        return None  # table doesn't exist yet (serve before any collect)


def rendered_frames(
    conn: sqlite3.Connection,
    *,
    site: str,
    start: datetime | None,
    end: datetime | None,
    limit: int,
) -> list[sqlite3.Row]:
    """Rendered frames for a site in [start, end], ascending, capped to the most
    recent ``limit``. Returns ``[]`` if there is no table yet."""
    where = ["render_status = 'rendered'", "site = ?"]
    params: list[object] = [site]
    if start is not None:
        where.append("scan_time >= ?")
        params.append(start.isoformat())
    if end is not None:
        where.append("scan_time <= ?")
        params.append(end.isoformat())
    sql = (
        f"SELECT * FROM volumes WHERE {' AND '.join(where)} "
        "ORDER BY scan_time DESC LIMIT ?"
    )
    params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
    # Query takes the most-recent `limit` (DESC); present oldest-first for playback.
    return list(reversed(rows))


def frames_window(
    conn: sqlite3.Connection,
    *,
    site: str,
    start: datetime | None,
    end: datetime | None,
    after: datetime | None,
    limit: int,
) -> list[sqlite3.Row]:
    """One ascending page of rendered frames for forward cursor-pagination.

    Filters to ``[start, end]`` and, when ``after`` is given, to ``scan_time >
    after`` (exclusive cursor) — so paging is contiguous with no dupes or gaps.
    Returns ``[]`` if there is no table yet."""
    where = ["render_status = 'rendered'", "site = ?"]
    params: list[object] = [site]
    if start is not None:
        where.append("scan_time >= ?")
        params.append(start.isoformat())
    if end is not None:
        where.append("scan_time <= ?")
        params.append(end.isoformat())
    if after is not None:
        where.append("scan_time > ?")
        params.append(after.isoformat())
    sql = (
        f"SELECT * FROM volumes WHERE {' AND '.join(where)} "
        "ORDER BY scan_time ASC LIMIT ?"
    )
    params.append(limit)
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def frames_extent(
    conn: sqlite3.Connection, *, site: str
) -> tuple[str | None, str | None, int]:
    """(min scan_time, max scan_time, count) of rendered frames for a site."""
    try:
        row = conn.execute(
            "SELECT MIN(scan_time) AS mn, MAX(scan_time) AS mx, COUNT(*) AS n "
            "FROM volumes WHERE render_status = 'rendered' AND site = ?",
            (site,),
        ).fetchone()
    except sqlite3.OperationalError:
        return (None, None, 0)
    return (row["mn"], row["mx"], row["n"])
