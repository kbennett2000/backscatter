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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backscatter.track.associate import TrackedCell

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
    source        TEXT    NOT NULL DEFAULT 'assembled',  -- assembled|live (26b)
    UNIQUE(site, scan_time)
);

-- Storm cells per frame (Slice 28). One row per identified cell, keyed to a
-- volume's (site, scan_time). track_id/u_ms/v_ms are filled by cross-frame
-- association (Slice 28b) and stay NULL for identification-only (28a) rows.
CREATE TABLE IF NOT EXISTS cells (
    id            INTEGER PRIMARY KEY,
    site          TEXT    NOT NULL,
    scan_time     TEXT    NOT NULL,   -- ISO-8601 UTC, matches volumes.scan_time
    centroid_lon  REAL    NOT NULL,
    centroid_lat  REAL    NOT NULL,
    max_dbz       REAL    NOT NULL,
    area_km2      REAL    NOT NULL,
    track_id      INTEGER,            -- persistent cell id (28b); NULL until associated
    u_ms          REAL,               -- eastward motion, m/s (28b)
    v_ms          REAL                -- northward motion, m/s (28b)
);
CREATE INDEX IF NOT EXISTS idx_cells_frame ON cells(site, scan_time);

-- Persistent track identities (Slice 28b). One row per track; its AUTOINCREMENT
-- id is the race-free source of cells.track_id (collect + a concurrent backfill
-- worker can both allocate without colliding, which MAX(track_id)+1 can't promise).
CREATE TABLE IF NOT EXISTS tracks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    site        TEXT NOT NULL,
    created_at  TEXT NOT NULL   -- ISO-8601 UTC; the scan that first saw the track
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

# The live-frame source flag (Slice 26b). Added by ALTER on pre-26b DBs; the
# 'assembled' default makes every existing row read correctly (it was an assembled
# volume), so the migration is safe and needs no data backfill.
_SOURCE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("source", "TEXT NOT NULL DEFAULT 'assembled'"),
)


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (creating parent dirs as needed) and return a connection."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # WAL lets readers (serve) run while a writer (collect) commits; the busy timeout
    # lets a writer wait out another writer instead of failing immediately with
    # "database is locked". There can now be three potential writers across two
    # processes — the collect loop, a web-triggered backfill job (Slice 19), and API
    # location edits — so we give the timeout generous headroom. Each write is a
    # single-statement commit (the slow download/render happens *outside* the write
    # lock), so the contended critical section is sub-millisecond; 15s is pure
    # insurance against a pathological collision, never a number we expect to reach.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def _add_missing_columns(
    conn: sqlite3.Connection, columns: tuple[tuple[str, str], ...]
) -> None:
    """Idempotently ALTER in any of ``columns`` not already on ``volumes``."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(volumes)")}
    for name, decl in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE volumes ADD COLUMN {name} {decl}")


def init_db(conn: sqlite3.Connection) -> None:
    """Create the schema (and migrate old DBs) if needed. Idempotent."""
    conn.executescript(_SCHEMA)
    _add_missing_columns(conn, _RENDER_COLUMNS)  # pre-Slice-5 DBs
    _add_missing_columns(conn, _SOURCE_COLUMNS)  # pre-Slice-26b DBs
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
    source: str = "assembled",
) -> None:
    """Insert one volume row.

    The ``UNIQUE(site, scan_time)`` constraint is the dedupe backstop: a duplicate
    insert raises :class:`sqlite3.IntegrityError` even if a pre-check missed it.
    ``source`` is ``'assembled'`` for archive volumes; the live-chunks path (26b)
    passes ``'live'`` and the row is later upgraded by ``upgrade_to_assembled``.
    """
    conn.execute(
        "INSERT INTO volumes "
        "(site, scan_time, s3_key, path, size_bytes, downloaded_at, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            site,
            scan_time.isoformat(),
            s3_key,
            str(path),
            size_bytes,
            downloaded_at.isoformat(),
            source,
        ),
    )
    conn.commit()


def volume_source(
    conn: sqlite3.Connection, site: str, scan_time: datetime
) -> str | None:
    """Return a scan's ``source`` (``'assembled'``/``'live'``), or ``None`` if absent.

    Used by the live path to skip a scan it already has and by the reconcile sweep
    to find live rows; distinguishes "no row" from "have it".
    """
    row = conn.execute(
        "SELECT source FROM volumes WHERE site = ? AND scan_time = ? LIMIT 1",
        (site, scan_time.isoformat()),
    ).fetchone()
    return None if row is None else str(row["source"])


def upgrade_to_assembled(
    conn: sqlite3.Connection,
    *,
    site: str,
    scan_time: datetime,
    s3_key: str,
    path: Path,
    size_bytes: int,
) -> None:
    """Upgrade a live row to assembled in place (26b reconciliation).

    Rewrites only the source + raw-artifact identity (``source``/``s3_key``/``path``/
    ``size_bytes``); ``render_status`` and every render column are left untouched, so
    the displayed PNG never changes (the assembled tilt is byte-identical to the live
    one it replaces — proven in 26a). One ``UPDATE``, so no duplicate row is possible.
    """
    conn.execute(
        "UPDATE volumes SET source = 'assembled', s3_key = ?, path = ?, "
        "size_bytes = ? WHERE site = ? AND scan_time = ?",
        (s3_key, str(path), size_bytes, site, scan_time.isoformat()),
    )
    conn.commit()


def live_rows_before(
    conn: sqlite3.Connection, *, before: datetime
) -> list[sqlite3.Row]:
    """``(id, site, scan_time)`` of every ``source='live'`` row older than ``before``.

    The reconcile sweep's worklist: live frames old enough that their assembled
    volume should have landed. Oldest-first; ``[]`` if there is no table yet."""
    try:
        return conn.execute(
            "SELECT id, site, scan_time FROM volumes "
            "WHERE source = 'live' AND scan_time < ? ORDER BY scan_time ASC",
            (before.isoformat(),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


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


def allocate_track_id(
    conn: sqlite3.Connection, *, site: str, created_at: datetime
) -> int:
    """Reserve a fresh persistent track id (Slice 28b) and return it.

    Inserts a ``tracks`` row and returns its AUTOINCREMENT id, so two writers
    (collect + a backfill worker) never collide on an id. Caller commits as part of
    the frame's ``record_cells`` write.
    """
    cur = conn.execute(
        "INSERT INTO tracks (site, created_at) VALUES (?, ?)",
        (site, created_at.isoformat()),
    )
    return int(cur.lastrowid)  # type: ignore[arg-type]


def record_cells(
    conn: sqlite3.Connection,
    *,
    site: str,
    scan_time: datetime,
    cells: list[TrackedCell],
) -> None:
    """Replace the stored storm cells for one frame, with track identity + motion.

    Cells are keyed to a frame's ``(site, scan_time)``; this deletes any existing rows
    for that frame first so a re-render (or a live→assembled reconcile that re-detects)
    is idempotent rather than additive. ``track_id``/``u_ms``/``v_ms`` come from the
    Slice 28b association pass.
    """
    conn.execute(
        "DELETE FROM cells WHERE site = ? AND scan_time = ?",
        (site, scan_time.isoformat()),
    )
    conn.executemany(
        "INSERT INTO cells "
        "(site, scan_time, centroid_lon, centroid_lat, max_dbz, area_km2, "
        "track_id, u_ms, v_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                site,
                scan_time.isoformat(),
                tc.cell.centroid_lon,
                tc.cell.centroid_lat,
                tc.cell.max_dbz,
                tc.cell.area_km2,
                tc.track_id,
                tc.u_ms,
                tc.v_ms,
            )
            for tc in cells
        ],
    )
    conn.commit()


def cells_for_frame(
    conn: sqlite3.Connection, *, site: str, scan_time: datetime
) -> list[sqlite3.Row]:
    """All stored storm cells for one frame's ``(site, scan_time)``, strongest first.

    Read side of the map overlay (Slice 28c). Returns ``[]`` if there is no cells
    table yet (serve running against a pre-28a DB), mirroring ``rendered_frames``."""
    try:
        return conn.execute(
            "SELECT centroid_lon, centroid_lat, max_dbz, area_km2, track_id, "
            "u_ms, v_ms FROM cells WHERE site = ? AND scan_time = ? "
            "ORDER BY max_dbz DESC",
            (site, scan_time.isoformat()),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def latest_tracked_cells_before(
    conn: sqlite3.Connection, *, site: str, scan_time: datetime
) -> tuple[datetime | None, list[TrackedCell]]:
    """The most recent earlier frame's tracked cells, for cross-frame association.

    Returns ``(prev_scan_time, cells)`` for the newest frame strictly before
    ``scan_time`` (same site) that has any cells, or ``(None, [])`` if there is none
    (or no table yet). Only rows with a non-NULL ``track_id`` are returned as track
    anchors; ``u_ms``/``v_ms`` default to 0.0 when absent.
    """
    from backscatter.track.associate import TrackedCell
    from backscatter.track.detect import Cell

    try:
        head = conn.execute(
            "SELECT scan_time FROM cells WHERE site = ? AND scan_time < ? "
            "ORDER BY scan_time DESC LIMIT 1",
            (site, scan_time.isoformat()),
        ).fetchone()
    except sqlite3.OperationalError:
        return (None, [])  # no cells table yet
    if head is None:
        return (None, [])

    prev_iso = str(head["scan_time"])
    rows = conn.execute(
        "SELECT centroid_lon, centroid_lat, max_dbz, area_km2, track_id, u_ms, v_ms "
        "FROM cells WHERE site = ? AND scan_time = ? AND track_id IS NOT NULL",
        (site, prev_iso),
    ).fetchall()
    tracked = [
        TrackedCell(
            cell=Cell(
                centroid_lon=row["centroid_lon"],
                centroid_lat=row["centroid_lat"],
                max_dbz=row["max_dbz"],
                area_km2=row["area_km2"],
            ),
            track_id=int(row["track_id"]),
            u_ms=row["u_ms"] if row["u_ms"] is not None else 0.0,
            v_ms=row["v_ms"] if row["v_ms"] is not None else 0.0,
        )
        for row in rows
    ]
    return (datetime.fromisoformat(prev_iso), tracked)


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


def frames_for_retention(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """All volume rows (every site, every render status), oldest scan first.

    Retention is global across the whole archive (ADR-0009), so this is unfiltered
    by site. ``scan_time`` is uniform ISO-8601 UTC, so the string sort is chrono.
    Returns ``[]`` if there is no table yet."""
    try:
        return conn.execute(
            "SELECT id, site, scan_time, path, image_path, size_bytes, "
            "render_status FROM volumes ORDER BY scan_time ASC"
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def delete_frame(conn: sqlite3.Connection, *, site: str, scan_time: str) -> None:
    """Delete one volume row by its ``(site, scan_time)`` key and commit.

    ``scan_time`` is the stored ISO-8601 string (as read back from a row), not a
    datetime — retention works off rows it already holds."""
    conn.execute(
        "DELETE FROM volumes WHERE site = ? AND scan_time = ?", (site, scan_time)
    )
    # Storm cells (Slice 28) are keyed to the same frame; drop them with it so a
    # pruned frame leaves no orphan cell rows.
    conn.execute(
        "DELETE FROM cells WHERE site = ? AND scan_time = ?", (site, scan_time)
    )
    conn.commit()


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
