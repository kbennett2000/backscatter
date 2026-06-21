"""In-process background backfill jobs for the serve process (ADR-0010).

A web request can't run a multi-minute backfill inside the request/response, so the
endpoint hands the work to a :class:`JobManager`: it spins one daemon thread, returns
a job id immediately, and the UI polls status. Design choices (see ADR-0010):

- **Thread, not external queue.** A single-container home tool shouldn't need
  Redis/Celery. One ``threading.Thread(daemon=True)`` per job is enough.
- **One job at a time.** A second concurrent start is rejected (``JobConflict``) so a
  double-click can't launch two backfills that fight over the same DB.
- **The worker owns its own connection + S3 client.** sqlite3 connections are bound to
  the creating thread (``check_same_thread``) and boto3 clients aren't shared safely,
  so both are built *inside* the worker and closed when it ends.
- **No app-level write lock.** ``serve`` and ``collect`` are separate processes; an
  in-process lock can't coordinate them. Concurrent writes are made safe by SQLite WAL
  + ``busy_timeout`` + the ``UNIQUE(site, scan_time)`` dedupe — the same mechanism the
  collector already relies on.

Job state lives in memory and is lost on restart; that's fine because a backfill is
idempotent (already-indexed scans are skipped), so an interrupted run is just re-run.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from backscatter.backfill.backfill import BackfillReport, plan_backfill, run_backfill
from backscatter.collect.collect import RenderFn
from backscatter.config import Config
from backscatter.ingest import s3
from backscatter.ingest.s3 import S3Client
from backscatter.render.render import render_volume
from backscatter.store import db

log = logging.getLogger("backscatter.jobs")

# A factory that returns an S3 client (real or a moto-backed test double).
ClientFactoryT = Callable[[], S3Client]


class JobState(StrEnum):
    """Lifecycle of a backfill job."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class BackfillJob:
    """A single backfill job's identity, request, and live progress."""

    id: str
    site: str
    start: datetime
    end: datetime
    state: JobState = JobState.QUEUED
    total: int = 0  # plan.to_fetch — volumes this run will actually fetch
    fetched: int = 0
    rendered: int = 0
    render_failed: int = 0
    skipped: int = 0
    already_have: int = 0
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "site": self.site,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "state": self.state.value,
            "total": self.total,
            "fetched": self.fetched,
            "rendered": self.rendered,
            "render_failed": self.render_failed,
            "skipped": self.skipped,
            "already_have": self.already_have,
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class JobConflict(Exception):
    """A backfill is already running; carries it so the caller can report it (409)."""

    def __init__(self, running: BackfillJob) -> None:
        super().__init__("a backfill job is already running")
        self.running = running


class JobManager:
    """Runs at most one backfill job at a time, in this process.

    The ``client_factory`` / ``render_fn`` seams let tests inject a fake S3 client and
    a stub renderer (mirroring ``run_backfill``'s injectable ``client``/``render_fn``).
    """

    def __init__(
        self,
        config: Config,
        *,
        client_factory: ClientFactoryT = s3.make_client,
        render_fn: RenderFn = render_volume,
    ) -> None:
        self._config = config
        self._client_factory = client_factory
        self._render_fn = render_fn
        self._lock = threading.Lock()
        self._current: BackfillJob | None = None  # running OR last-finished
        self._thread: threading.Thread | None = None

    def start(self, *, site: str, start: datetime, end: datetime) -> dict[str, object]:
        """Start a backfill job; return its initial status.

        Raises :class:`JobConflict` if a job is already queued/running.
        """
        with self._lock:
            if self._current is not None and self._current.state in (
                JobState.QUEUED,
                JobState.RUNNING,
            ):
                raise JobConflict(self._current)
            job = BackfillJob(id=uuid.uuid4().hex, site=site, start=start, end=end)
            self._current = job
            thread = threading.Thread(
                target=self._run, args=(job,), name=f"backfill-{job.id[:8]}",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            return job.to_json()

    def get(self, job_id: str) -> dict[str, object] | None:
        """Return a serialized snapshot of the job, or None if it's not the current."""
        with self._lock:
            if self._current is not None and self._current.id == job_id:
                return self._current.to_json()
            return None

    def current(self) -> dict[str, object] | None:
        """Return the current/last job (for restoring the UI on reload), or None."""
        with self._lock:
            return self._current.to_json() if self._current is not None else None

    def wait(self, timeout: float | None = None) -> None:
        """Join the worker thread (test affordance; harmless in prod)."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _set(self, job: BackfillJob, **fields: object) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(job, key, value)

    def _run(self, job: BackfillJob) -> None:
        """Worker body: owns its own DB connection + S3 client for its lifetime.

        Everything (including opening the connection and building the client) runs
        under the try, so any failure becomes ``state=failed`` rather than an
        unobserved exception that kills the thread.
        """
        now = datetime.now(UTC)
        conn: sqlite3.Connection | None = None
        try:
            conn = db.connect(self._config.db_path)  # per-thread connection
            client = self._client_factory()
            db.init_db(conn)  # idempotent; serve already bootstrapped, cheap insurance
            plan = plan_backfill(
                self._config, conn, job.site, job.start, job.end, now=now, client=client
            )
            self._set(
                job,
                state=JobState.RUNNING,
                total=plan.to_fetch,
                already_have=plan.already_have,
                started_at=datetime.now(UTC),
            )

            def _progress(_processed: int, _total: int, report: BackfillReport) -> None:
                self._set(
                    job,
                    fetched=report.fetched,
                    rendered=report.rendered,
                    render_failed=report.render_failed,
                    skipped=report.skipped,
                )

            report = run_backfill(
                self._config, conn, job.site, job.start, job.end,
                now=now, client=client, render_fn=self._render_fn,
                progress_cb=_progress,
            )
            self._set(
                job,
                state=JobState.DONE,
                fetched=report.fetched,
                rendered=report.rendered,
                render_failed=report.render_failed,
                skipped=report.skipped,
                already_have=report.already_have,
                finished_at=datetime.now(UTC),
            )
        except Exception as exc:  # noqa: BLE001 — a job must never crash the thread silently
            log.exception("backfill job %s failed", job.id)
            self._set(
                job, state=JobState.FAILED, error=str(exc),
                finished_at=datetime.now(UTC),
            )
        finally:
            if conn is not None:
                conn.close()
