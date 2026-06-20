"""Tests for the SQLite frame index."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backscatter.store import db


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = db.connect(tmp_path / "nested" / "backscatter.db")
    db.init_db(conn)
    return conn


def test_connect_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b" / "backscatter.db"
    db.connect(path)
    assert path.parent.is_dir()


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    db.init_db(conn)  # second call must not raise
    db.init_db(conn)


def test_record_and_exists(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    scan = datetime(2026, 6, 20, 0, 15, 30, tzinfo=UTC)

    assert not db.volume_exists(conn, "KFTG", scan)
    db.record_volume(
        conn,
        site="KFTG",
        scan_time=scan,
        s3_key="2026/06/20/KFTG/KFTG20260620_001530_V06",
        path=tmp_path / "KFTG20260620_001530_V06",
        size_bytes=1234,
        downloaded_at=datetime(2026, 6, 20, 0, 16, 0, tzinfo=UTC),
    )
    assert db.volume_exists(conn, "KFTG", scan)


def test_duplicate_scan_is_rejected(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    scan = datetime(2026, 6, 20, 0, 15, 30, tzinfo=UTC)
    kwargs = dict(
        site="KFTG",
        scan_time=scan,
        s3_key="2026/06/20/KFTG/KFTG20260620_001530_V06",
        path=tmp_path / "x_V06",
        size_bytes=1,
        downloaded_at=scan,
    )
    db.record_volume(conn, **kwargs)  # type: ignore[arg-type]
    with pytest.raises(sqlite3.IntegrityError):
        db.record_volume(conn, **kwargs)  # type: ignore[arg-type]


def _record_a_volume(conn: sqlite3.Connection, scan: datetime) -> None:
    db.record_volume(
        conn,
        site="KFTG",
        scan_time=scan,
        s3_key="2026/06/20/KFTG/KFTG20260620_001530_V06",
        path=Path("KFTG20260620_001530_V06"),
        size_bytes=1,
        downloaded_at=scan,
    )


def test_record_render_sets_fields_and_status(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    scan = datetime(2026, 6, 20, 0, 15, 30, tzinfo=UTC)
    _record_a_volume(conn, scan)

    db.record_render(
        conn,
        site="KFTG",
        scan_time=scan,
        image_path="KFTG/KFTG20260620_001530_V06.png",
        elevation_deg=0.5,
        width=100,
        height=200,
        bounds=(-107.0, 37.0, -101.0, 42.0),
        rendered_at=scan,
    )
    row = db.latest_rendered_frame(conn)
    assert row is not None
    assert row["render_status"] == "rendered"
    assert row["image_path"] == "KFTG/KFTG20260620_001530_V06.png"
    assert row["width"] == 100 and row["height"] == 200
    assert row["bounds_west"] == -107.0 and row["bounds_north"] == 42.0


def test_mark_render_failed(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    scan = datetime(2026, 6, 20, 0, 15, 30, tzinfo=UTC)
    _record_a_volume(conn, scan)
    db.mark_render_failed(conn, "KFTG", scan)
    assert db.latest_rendered_frame(conn) is None  # failed != rendered


def test_latest_rendered_frame_newest_wins(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    older = datetime(2026, 6, 20, 20, 0, 0, tzinfo=UTC)
    newer = datetime(2026, 6, 20, 21, 51, 7, tzinfo=UTC)
    for scan in (older, newer):
        db.record_volume(
            conn,
            site="KFTG",
            scan_time=scan,
            s3_key=f"k/{scan.isoformat()}",
            path=Path("v"),
            size_bytes=1,
            downloaded_at=scan,
        )
        db.record_render(
            conn,
            site="KFTG",
            scan_time=scan,
            image_path=f"KFTG/{scan:%H%M%S}.png",
            elevation_deg=0.5,
            width=1,
            height=1,
            bounds=(0.0, 0.0, 1.0, 1.0),
            rendered_at=scan,
        )
    row = db.latest_rendered_frame(conn)
    assert row is not None and row["scan_time"] == newer.isoformat()


def test_migration_adds_render_columns_to_old_db(tmp_path: Path) -> None:
    # Simulate a pre-Slice-5 DB: base table only, no render columns.
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        "CREATE TABLE volumes (id INTEGER PRIMARY KEY, site TEXT NOT NULL, "
        "scan_time TEXT NOT NULL, s3_key TEXT NOT NULL, path TEXT NOT NULL, "
        "size_bytes INTEGER NOT NULL, downloaded_at TEXT NOT NULL, "
        "UNIQUE(site, scan_time));"
    )
    raw.commit()
    raw.close()

    conn = db.connect(path)
    db.init_db(conn)  # should ALTER in the missing render columns
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(volumes)")}
    assert {"render_status", "image_path", "bounds_west", "elevation_deg"} <= cols


def test_latest_rendered_frame_no_table(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "empty.db")  # never init_db'd
    assert db.latest_rendered_frame(conn) is None
