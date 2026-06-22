# 13. Runtime-editable retention (DB-backed, env becomes seed)

## Status
Accepted

## Context
Retention (ADR-0009) was immutable env config: `load_config()` read
`BACKSCATTER_RETENTION_DAYS` / `_MAX_GB` once at startup into a frozen `Config`, and
the prune loop held that value for the process lifetime. Changing a limit meant editing
`.env` **and** recreating the container — and because `serve` and `collect` run as
**separate processes** (one container, two children), an in-memory change in one would
never reach the other.

We want both limits editable from the Settings menu, live, with the same either / or /
both semantics the env already supports (each limit independently on or off). ADR-0008
already solved this exact shape for locations: mutable state in SQLite, env seeds an
empty store, then the DB wins, and the collect loop re-reads it each cycle. The shared
DB is how the two processes already coordinate location edits.

## Decision
Move retention to DB-backed runtime state, mirroring ADR-0008.

- **Singleton `retention_settings` row** (`id = 1`, `max_age_days REAL`,
  `max_size_bytes INTEGER`, `updated_at`) in the existing SQLite DB. `NULL` for either
  column = that limit off, matching the env semantics. Table lives in `db.init_db`'s
  base schema alongside the other core tables.
- **Env becomes seed/bootstrap only.** On bootstrap, if the row is absent it is seeded
  from the env-derived `Config` values (`store/settings.ensure_retention_seeded`). Once
  it exists the **DB is the source of truth** and the env is ignored — exactly like
  locations. Existing deploys seed from their current `.env` on upgrade, so there is no
  behavior change.
- **Prune reads the live policy.** `select_candidates` / `run_prune` take an explicit
  `RetentionPolicy` instead of reading `config.retention_*` (`config` still supplies
  on-disk paths). The collect loop fetches `settings.get_retention(conn)` each prune
  pass and gates on `policy.active`; the CLI `prune` and the backfill age-window warning
  read it the same way. A UI edit therefore takes effect on the next prune pass, across
  both processes, with no restart.
- **Validation shared by the writer and the API**: age `>= 0` (0 or blank → off), size
  `> 0` (blank → off). The API speaks **GB** and converts to **bytes** at its boundary;
  storage is always bytes. Both limits off is allowed (an explicitly unbounded archive).
- **GET / PUT `/api/retention`** mirror the location endpoints (Pydantic body,
  `ValueError → HTTP 400`, per-request connection). PUT is a full replace; a null field
  turns that limit off. No auth, consistent with the other endpoints and the LAN-first,
  not-life-safety framing (CLAUDE.md).

## Consequences
- `Config.retention_*` is now seed-only input; the live policy is read from the store.
  `Config.retention_active` is superseded by `RetentionPolicy.active`.
- A change made in the UI is picked up within one `prune_interval` (default 1h) by the
  running collector — `prune_interval` itself stays env config (operational, not a
  per-user setting).
- An un-bootstrapped connection (e.g. a backfill worker on a brand-new DB before serve
  seeds it) reads as "no limits" rather than raising — a safe default (prune no-op).
- No new file format / cross-process IPC: the shared DB already provides transactions +
  WAL concurrency, the same machinery locations rely on.

## Alternatives considered
- **Live env re-read.** Rejected: process env isn't reloadable without a restart and
  can't cross the serve/collect process boundary — the limitation this removes.
- **Signal-based config reload.** Rejected: needless cross-process signaling machinery
  when both processes already share the DB.
- **Env stays authoritative, DB only fills gaps.** Rejected: a three-state-per-field
  model (env-set vs db-set vs default) where the UI silently can't change an env-pinned
  value — diverges from the locations model for no real gain.
