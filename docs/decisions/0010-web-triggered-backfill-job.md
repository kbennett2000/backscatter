# 10. Web-triggered backfill — an in-process async job

## Status
Accepted

## Context
A brand-new install opens to an empty archive: collection has just started, so there
are no frames yet (Slice 18 makes that state honest with a "collecting now…" card).
The fix for "I don't want to wait ~5 minutes for the first frame" is **backfill** —
pull recent assembled volumes from S3 and render them into the archive. That capability
already exists as the `backscatter backfill` CLI (Slice 12: `plan_backfill` /
`run_backfill`, idempotent, dedupes on `(site, scan_time)`). This decision is about
exposing it behind a **one-click web button** safely.

Two hard problems:

1. **It can't run in the request.** A backfill over hours of data is dozens of
   sequential download+decode+render steps — minutes of blocking work. It must not run
   inside the HTTP request/response or block the event loop.

2. **Two writers, one SQLite DB, two processes.** `serve` (FastAPI) and `collect` (the
   live loop) run as **separate OS processes** sharing `/data/backscatter.db`
   (docker-entrypoint launches both; in dev they're two terminals). A backfill started
   inside the serve process is a **third writer in a different process from the
   collector**. An in-process lock cannot coordinate across processes — so it's the
   wrong tool here.

## Decision

**Run the job on a daemon thread inside the serve process, tracked by an in-memory
`JobManager` singleton. No external queue/broker.**

- `POST /api/backfill` (body: optional `location`, `hours`) starts a job and returns
  **202** with the job's id + initial status. `GET /api/backfill/{id}` polls status
  (state, total, fetched/rendered/skipped/already_have, error). `GET /api/backfill`
  returns the current/last job so the UI can restore progress on reload.
- The job runs on **one `threading.Thread(daemon=True)`** per start. The worker owns
  its **own** SQLite connection (sqlite3 connections are bound to their creating thread
  via `check_same_thread`) and its **own** unsigned S3 client (boto3 clients aren't
  shared safely), both built inside the worker and closed when it ends.
- **One job at a time.** A concurrent start (double-click, two tabs) is rejected with
  **409** carrying the running job's status. The manager keeps a single slot holding
  the running — then last-finished — job.
- The job **reuses** `plan_backfill` / `run_backfill` unchanged except for one additive
  `progress_cb` kwarg that feeds live counts to the manager. No pipeline is
  reimplemented; the same dedupe, skip-on-bad-volume, and per-volume render apply.
- **Range is bounded:** a click backfills the **last 6 hours**; the request is
  hard-capped at **24 hours** server-side (400 over cap). 24h sits well inside the
  default 30-day retention window, so no prune warning is needed (the CLI, which allows
  arbitrary ranges, keeps its `older_than_retention` warning).

**Concurrent writes are made safe by SQLite itself, not an app-level lock.** WAL +
`busy_timeout` + the `UNIQUE(site, scan_time)` dedupe — the exact mechanism the
collector already relies on:

- **WAL:** readers (API frame queries) never block writers, and vice-versa. Only
  writer-vs-writer contends.
- **Writes are millisecond-scale, spaced seconds apart.** In both collector and
  backfill the slow work (download + Py-ART render) happens *before* any DB touch; the
  actual writes are single-statement INSERT/UPDATE commits. No multi-statement
  transaction is ever held open across the slow work, so the contended critical section
  is sub-millisecond. `busy_timeout` (raised 5s → 15s for three-writer headroom) cannot
  realistically be exhausted.
- **Same-key race** (backfill "last 6h" overlapping what collect just pulled): a
  `volume_exists` pre-check plus the `UNIQUE` backstop; the losing INSERT raises
  `IntegrityError`, which `run_backfill`'s per-volume `try/except` already absorbs.

Job state lives only in memory and is **lost on process restart**. That's acceptable:
a backfill is idempotent, so an interrupted run is simply re-run and re-skips whatever
already landed.

## Consequences
- No new runtime dependency, no broker, no extra process — fits the LAN-first,
  single-container model.
- A restart mid-backfill loses the job's *status* (and stops the run), never *data*.
  The UI treats a 404 on a previously-known job id as "lost on restart → just re-run."
- The "one job at a time" guarantee is **per serve process**. backscatter serves
  single-process (`uvicorn.run`, no `--workers`); if anyone runs multiple workers, each
  gets its own manager and the guard weakens to one-per-worker. The DB-level safety
  still holds (it's the same as collector + backfill). **Keep serve single-process**, or
  move the guard to a DB-backed lock.
- The progress bar updates every `progress_every` (25) volumes plus once at the end —
  coarse but fine for a ≤24h range.

## Alternatives considered
- **FastAPI `BackgroundTasks`** — fire-and-forget tied to a request, with no pollable
  handle and no one-at-a-time guard. Wrong shape for a start + status-poll job.
- **`asyncio.create_task` + `run_in_executor`** — viable but pulls the job's lifecycle
  into the event loop and default threadpool, entangling it with request handling for
  no benefit over a plain daemon thread.
- **Subprocess (spawn `backscatter backfill`)** — cleanest isolation (a 4th process, no
  `check_same_thread` concern), and the two-writer DB safety makes it unnecessary; kept
  as the escape hatch if render ever needs hard memory isolation. No in-process status
  to poll without parsing stdout or a status file.
- **External task queue (Celery/RQ/Redis)** — overkill for one-at-a-time work on a home
  server; violates the no-extra-infra spirit.
- **App-level write lock shared between the backfill thread and request handlers** —
  ineffective: it can't coordinate against the *collector* (a different process) and
  risks holding a Python lock across a multi-second render if mis-scoped. Rejected in
  favor of SQLite's own file locking.
