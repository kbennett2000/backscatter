"""SQLite frame index — one row per stored volume scan.

The raw `_V06` files on disk are the source of truth (ADR-0003); this index records
what we have so playback and dedupe are simple SQL. Dedupe is keyed on
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
    UNIQUE(site, scan_time)
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (creating parent dirs as needed) and return a connection."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create the schema if it does not exist. Idempotent."""
    conn.executescript(_SCHEMA)
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
