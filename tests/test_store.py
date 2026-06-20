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
