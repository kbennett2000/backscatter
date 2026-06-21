"""Tests for the in-process backfill JobManager (Slice 19, ADR-0010).

Reuses the moto S3 + stubbed-render pattern from test_backfill. The manager runs the
work on a daemon thread; tests call ``manager.wait()`` to join it deterministically.
The two-writers test exercises the actual SQLite contention the slice introduces.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from backscatter.config import Config, SeedLocation
from backscatter.ingest import naming, s3
from backscatter.jobs.manager import JobConflict, JobManager, JobState
from backscatter.render.render import RenderResult
from backscatter.store import db

_RANGE = (datetime(2026, 6, 18, tzinfo=UTC), datetime(2026, 6, 19, tzinfo=UTC))


@pytest.fixture
def s3_client() -> Iterator[object]:
    with mock_aws():
        client = boto3.client("s3", region_name=s3.REGION)
        client.create_bucket(Bucket=s3.BUCKET)
        yield client


def _config(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "backscatter.db",
        poll_interval_s=0.0,
        site_override=None,
        seed_locations=(SeedLocation("Home", 39.3603, -104.5969, True),),
        retention_max_age_days=None,
    )


def _put(client: object, scan: datetime, *, site: str = "KFTG") -> None:
    key = f"{scan:%Y/%m/%d}/{site}/{site}{scan:%Y%m%d_%H%M%S}_V06"
    client.put_object(Bucket=s3.BUCKET, Key=key, Body=b"vol-bytes")  # type: ignore[attr-defined]


def _seed_three(client: object) -> list[datetime]:
    scans = [
        datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
        datetime(2026, 6, 18, 12, 30, tzinfo=UTC),
        datetime(2026, 6, 18, 13, 0, tzinfo=UTC),
    ]
    for scan in scans:
        _put(client, scan)
    return scans


def _stub_render(gate: threading.Event | None = None) -> Callable[..., RenderResult]:
    """A render stub; if ``gate`` is given, each call blocks until it's set."""

    def render(volume_path: Path, config: Config) -> RenderResult:
        if gate is not None:
            gate.wait(timeout=5)
        name = Path(volume_path).name
        site = naming.parse_site(name)
        scan = naming.parse_scan_time(name)
        png = config.data_dir / "renders" / site / f"{name}.png"
        return RenderResult(
            png_path=png, sidecar_path=png.with_suffix(".json"),
            site=site, scan_time=scan, elevation_deg=0.5, width=10, height=20,
            bounds_wgs84=(-107.0, 37.0, -101.0, 42.0), bounds_3857=(0.0, 0.0, 1.0, 1.0),
        )

    return render


def _manager(config: Config, client: object, **kw: object) -> JobManager:
    return JobManager(
        config, client_factory=lambda: client, render_fn=_stub_render(),  # type: ignore[arg-type]
        **kw,  # type: ignore[arg-type]
    )


def _rows(config: Config) -> int:
    conn = db.connect(config.db_path)
    db.init_db(conn)
    n = conn.execute("SELECT COUNT(*) AS n FROM volumes").fetchone()["n"]
    conn.close()
    return int(n)


# --- lifecycle ---------------------------------------------------------------


def test_job_runs_to_done_with_counts(tmp_path: Path, s3_client: object) -> None:
    config = _config(tmp_path)
    _seed_three(s3_client)
    manager = _manager(config, s3_client)

    started = manager.start(site="KFTG", start=_RANGE[0], end=_RANGE[1])
    assert started["state"] in (JobState.QUEUED.value, JobState.RUNNING.value)
    manager.wait(timeout=10)

    job = manager.get(str(started["id"]))
    assert job is not None
    assert job["state"] == JobState.DONE.value
    assert (job["total"], job["fetched"], job["rendered"]) == (3, 3, 3)
    assert job["render_failed"] == 0 and job["already_have"] == 0
    assert job["finished_at"] is not None
    assert _rows(config) == 3


def test_idempotent_rerun_adds_nothing(tmp_path: Path, s3_client: object) -> None:
    config = _config(tmp_path)
    _seed_three(s3_client)
    manager = _manager(config, s3_client)

    first = manager.start(site="KFTG", start=_RANGE[0], end=_RANGE[1])
    manager.wait(timeout=10)
    assert manager.get(str(first["id"]))["fetched"] == 3  # type: ignore[index]

    second = manager.start(site="KFTG", start=_RANGE[0], end=_RANGE[1])
    manager.wait(timeout=10)
    done = manager.get(str(second["id"]))
    assert done is not None
    assert done["state"] == JobState.DONE.value
    assert done["fetched"] == 0 and done["already_have"] == 3
    assert _rows(config) == 3  # no duplicate rows


# --- one job at a time -------------------------------------------------------


def test_one_job_at_a_time_conflict(tmp_path: Path, s3_client: object) -> None:
    config = _config(tmp_path)
    _seed_three(s3_client)
    gate = threading.Event()  # holds the first job in render until released
    manager = JobManager(
        config, client_factory=lambda: s3_client, render_fn=_stub_render(gate),
    )

    running = manager.start(site="KFTG", start=_RANGE[0], end=_RANGE[1])
    with pytest.raises(JobConflict) as exc:
        manager.start(site="KFTG", start=_RANGE[0], end=_RANGE[1])
    assert exc.value.running.id == running["id"]

    gate.set()  # let the first job finish
    manager.wait(timeout=10)
    assert manager.get(str(running["id"]))["state"] == JobState.DONE.value  # type: ignore[index]

    # A fresh start is allowed once nothing is running.
    again = manager.start(site="KFTG", start=_RANGE[0], end=_RANGE[1])
    manager.wait(timeout=10)
    assert manager.get(str(again["id"]))["state"] == JobState.DONE.value  # type: ignore[index]


# --- failure path ------------------------------------------------------------


def test_failure_sets_state_failed_not_crash(tmp_path: Path) -> None:
    config = _config(tmp_path)

    def boom() -> object:
        raise RuntimeError("S3 unreachable")

    manager = JobManager(config, client_factory=boom)
    started = manager.start(site="KFTG", start=_RANGE[0], end=_RANGE[1])
    manager.wait(timeout=10)

    job = manager.get(str(started["id"]))
    assert job is not None
    assert job["state"] == JobState.FAILED.value
    assert job["error"] and "S3 unreachable" in str(job["error"])
    assert job["finished_at"] is not None  # the thread ended cleanly, no escape


def test_get_unknown_job_returns_none(tmp_path: Path) -> None:
    manager = JobManager(_config(tmp_path))
    assert manager.get("nope") is None
    assert manager.current() is None


# --- two writers, one DB (the concurrency proof) -----------------------------


def test_two_writers_no_corruption_or_deadlock(tmp_path: Path) -> None:
    """Simulate collector + backfill writing the same DB from two threads.

    Each thread opens its OWN connection (WAL + busy_timeout, as in prod) and writes
    an overlapping set of (site, scan_time) keys via the real record_volume path —
    pre-check + IntegrityError backstop, exactly like fetch_key. We assert: no
    'database is locked'/deadlock, every distinct key lands once, and the overlap
    dedupes to a single row (no duplicates, index intact).
    """
    db_path = tmp_path / "data" / "backscatter.db"
    boot = db.connect(db_path)
    db.init_db(boot)
    boot.close()

    base = datetime(2026, 6, 18, 0, 0, tzinfo=UTC)
    site = "KFTG"
    # A: keys 0..199, B: keys 100..299 → 100 overlap, 300 distinct total.
    a_scans = [base + timedelta(minutes=i) for i in range(0, 200)]
    b_scans = [base + timedelta(minutes=i) for i in range(100, 300)]
    errors: list[Exception] = []

    def writer(scans: list[datetime]) -> None:
        conn = db.connect(db_path)
        try:
            for scan in scans:
                try:
                    if db.volume_exists(conn, site, scan):
                        continue
                    db.record_volume(
                        conn, site=site, scan_time=scan, s3_key="k",
                        path=Path("x"), size_bytes=1, downloaded_at=scan,
                    )
                except db.sqlite3.IntegrityError:
                    pass  # raced same key — the UNIQUE backstop, as in run_backfill
        except Exception as exc:  # noqa: BLE001 — capture for the assertion
            errors.append(exc)
        finally:
            conn.close()

    ta = threading.Thread(target=writer, args=(a_scans,))
    tb = threading.Thread(target=writer, args=(b_scans,))
    ta.start()
    tb.start()
    ta.join(timeout=30)
    tb.join(timeout=30)

    assert not ta.is_alive() and not tb.is_alive()  # no deadlock
    assert errors == []  # no 'database is locked', no unexpected error

    conn = db.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM volumes").fetchone()["n"]
        distinct = conn.execute(
            "SELECT COUNT(DISTINCT scan_time) AS n FROM volumes"
        ).fetchone()["n"]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()

    assert count == 300  # union of {0..199} and {100..299}
    assert distinct == 300  # every key exactly once — overlap deduped
    assert integrity == "ok"


def test_live_assembled_backfill_writers_coexist(tmp_path: Path) -> None:
    """The 26b live writer + reconcile upgrade + a backfill on overlapping keys.

    Three threads, own connections: a live writer (source='live'), a reconcile worker
    that upgrades live rows in place to assembled, and a backfill writer inserting
    assembled rows — with overlapping (site, scan_time). We assert no deadlock/lock
    error, every distinct scan lands exactly once (live then assembled is one row
    upgraded, never two), and the index stays intact.
    """
    db_path = tmp_path / "data" / "backscatter.db"
    boot = db.connect(db_path)
    db.init_db(boot)
    boot.close()

    base = datetime(2026, 6, 21, 0, 0, tzinfo=UTC)
    site = "KFTG"
    live_scans = [base + timedelta(minutes=i) for i in range(0, 200)]
    backfill_scans = [base + timedelta(minutes=i) for i in range(100, 300)]
    errors: list[Exception] = []

    def insert(scans: list[datetime], source: str) -> None:
        conn = db.connect(db_path)
        try:
            for scan in scans:
                try:
                    if db.volume_exists(conn, site, scan):
                        continue
                    db.record_volume(
                        conn, site=site, scan_time=scan, s3_key="k",
                        path=Path("partial"), size_bytes=1, downloaded_at=scan,
                        source=source,
                    )
                except db.sqlite3.IntegrityError:
                    pass  # raced the same key — UNIQUE backstop
        except Exception as exc:  # noqa: BLE001 — capture for the assertion
            errors.append(exc)
        finally:
            conn.close()

    def reconcile(scans: list[datetime]) -> None:
        conn = db.connect(db_path)
        try:
            for scan in scans:
                if db.volume_source(conn, site, scan) == "live":
                    db.upgrade_to_assembled(
                        conn, site=site, scan_time=scan, s3_key="full",
                        path=Path("complete"), size_bytes=99,
                    )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            conn.close()

    threads = [
        threading.Thread(target=insert, args=(live_scans, "live")),
        threading.Thread(target=reconcile, args=(live_scans,)),
        threading.Thread(target=insert, args=(backfill_scans, "assembled")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert all(not t.is_alive() for t in threads)  # no deadlock
    assert errors == []  # no 'database is locked', no unexpected error

    conn = db.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM volumes").fetchone()["n"]
        distinct = conn.execute(
            "SELECT COUNT(DISTINCT scan_time) AS n FROM volumes"
        ).fetchone()["n"]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()

    assert count == 300 == distinct  # one row per scan — overlap never duplicated
    assert integrity == "ok"
