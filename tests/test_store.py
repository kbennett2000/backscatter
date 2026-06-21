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


def test_migration_adds_source_column_to_pre_26b_db(tmp_path: Path) -> None:
    # A pre-Slice-26b DB: full render columns but no `source`, with one existing row.
    path = tmp_path / "pre26b.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        "CREATE TABLE volumes (id INTEGER PRIMARY KEY, site TEXT NOT NULL, "
        "scan_time TEXT NOT NULL, s3_key TEXT NOT NULL, path TEXT NOT NULL, "
        "size_bytes INTEGER NOT NULL, downloaded_at TEXT NOT NULL, "
        "render_status TEXT NOT NULL DEFAULT 'pending', image_path TEXT, "
        "UNIQUE(site, scan_time));"
    )
    raw.execute(
        "INSERT INTO volumes (site, scan_time, s3_key, path, size_bytes, "
        "downloaded_at, render_status) VALUES "
        "('KFTG', '2026-06-20T00:00:00+00:00', 'k', 'p', 1, 'd', 'rendered')"
    )
    raw.commit()
    raw.close()

    conn = db.connect(path)
    db.init_db(conn)  # should ALTER in the `source` column
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(volumes)")}
    assert "source" in cols
    # The existing (assembled) row reads back as 'assembled' — no data lost/changed.
    scan = datetime(2026, 6, 20, 0, 0, 0, tzinfo=UTC)
    assert db.volume_source(conn, "KFTG", scan) == "assembled"
    assert db.volume_exists(conn, "KFTG", scan)


def test_source_helpers_record_query_and_upgrade(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    scan = datetime(2026, 6, 21, 21, 50, 0, tzinfo=UTC)
    assert db.volume_source(conn, "KFTG", scan) is None  # absent

    db.record_volume(
        conn, site="KFTG", scan_time=scan, s3_key="KFTG/100/", path=Path("partial"),
        size_bytes=700, downloaded_at=scan, source="live",
    )
    db.record_render(
        conn, site="KFTG", scan_time=scan, image_path="KFTG/x.png",
        elevation_deg=0.5, width=10, height=20, bounds=(0.0, 0.0, 1.0, 1.0),
        rendered_at=scan,
    )
    assert db.volume_source(conn, "KFTG", scan) == "live"
    assert [r["scan_time"] for r in db.live_rows_before(conn, before=scan)] == []
    later = datetime(2026, 6, 21, 22, 0, 0, tzinfo=UTC)
    assert [r["site"] for r in db.live_rows_before(conn, before=later)] == ["KFTG"]

    db.upgrade_to_assembled(
        conn, site="KFTG", scan_time=scan,
        s3_key="2026/06/21/KFTG/KFTG20260621_215000_V06",
        path=Path("complete"), size_bytes=5000,
    )
    row = db.latest_rendered_frame(conn, "KFTG")
    assert row is not None
    # Source/identity upgraded; render output left exactly as it was (no re-render).
    assert row["source"] == "assembled"
    assert row["path"] == "complete" and row["size_bytes"] == 5000
    assert row["render_status"] == "rendered" and row["image_path"] == "KFTG/x.png"
    assert db.live_rows_before(conn, before=later) == []  # no longer a live row


def test_latest_rendered_frame_no_table(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "empty.db")  # never init_db'd
    assert db.latest_rendered_frame(conn) is None


def test_rendered_frames_order_and_limit(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    times = [
        datetime(2026, 6, 20, h, 0, 0, tzinfo=UTC) for h in (20, 21, 22)
    ]
    for scan in times:
        db.record_volume(
            conn, site="KFTG", scan_time=scan, s3_key=f"k/{scan.isoformat()}",
            path=Path("v"), size_bytes=1, downloaded_at=scan,
        )
        db.record_render(
            conn, site="KFTG", scan_time=scan, image_path=f"KFTG/{scan:%H}.png",
            elevation_deg=0.5, width=1, height=1, bounds=(0.0, 0.0, 1.0, 1.0),
            rendered_at=scan,
        )
    rows = db.rendered_frames(conn, site="KFTG", start=None, end=None, limit=10)
    assert [r["scan_time"] for r in rows] == [t.isoformat() for t in times]
    # Cap keeps the most recent, still ascending.
    capped = db.rendered_frames(conn, site="KFTG", start=None, end=None, limit=2)
    assert [r["scan_time"] for r in capped] == [t.isoformat() for t in times[1:]]


def test_rendered_frames_no_table(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "empty.db")  # never init_db'd
    assert db.rendered_frames(conn, site="KFTG", start=None, end=None, limit=10) == []


def _seed_n_rendered(conn: sqlite3.Connection, n: int) -> list[datetime]:
    times = [datetime(2026, 6, 20, 12, 5 * i, 0, tzinfo=UTC) for i in range(n)]
    for scan in times:
        db.record_volume(
            conn, site="KFTG", scan_time=scan, s3_key=f"k/{scan.isoformat()}",
            path=Path("v"), size_bytes=1, downloaded_at=scan,
        )
        db.record_render(
            conn, site="KFTG", scan_time=scan, image_path=f"KFTG/{i_name(scan)}.png",
            elevation_deg=0.5, width=1, height=1, bounds=(0.0, 0.0, 1.0, 1.0),
            rendered_at=scan,
        )
    return times


def i_name(scan: datetime) -> str:
    return scan.strftime("%H%M")


def test_frames_window_cursor_paging_no_gaps_or_dupes(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    times = _seed_n_rendered(conn, 5)

    seen: list[str] = []
    after: datetime | None = None
    pages = 0
    while True:
        rows = db.frames_window(
            conn, site="KFTG", start=None, end=None, after=after, limit=2
        )
        if not rows:
            break
        pages += 1
        seen.extend(r["scan_time"] for r in rows)
        after = datetime.fromisoformat(rows[-1]["scan_time"])
    assert seen == [t.isoformat() for t in times]  # ordered, no gaps
    assert len(seen) == len(set(seen)) == 5  # no dupes
    assert pages == 3  # 2 + 2 + 1


def test_frames_window_no_table(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "empty.db")
    assert (
        db.frames_window(conn, site="KFTG", start=None, end=None, after=None, limit=5)
        == []
    )


def test_frames_extent(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    times = _seed_n_rendered(conn, 3)
    mn, mx, count = db.frames_extent(conn, site="KFTG")
    assert mn == times[0].isoformat()
    assert mx == times[-1].isoformat()
    assert count == 3
    assert db.frames_extent(conn, site="KPUX") == (None, None, 0)


def test_frames_extent_no_table(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "empty.db")
    assert db.frames_extent(conn, site="KFTG") == (None, None, 0)
