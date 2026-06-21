# 8. Mutable locations persisted in SQLite (env becomes seed)

## Status
Accepted

## Context
Through Slice 9, locations came from a read-only env var (`BACKSCATTER_LOCATIONS`,
ADR-0006). Managing them (add / edit / delete / set-default) from the UI needs a
**mutable, persisted** store, and the running collector must see changes without a
restart. ADR-0006 parked the question of where writable config lives — TOML file vs
the SQLite DB.

## Decision
Persist locations in a new `locations` table in the existing SQLite DB.

- **Env becomes seed/bootstrap only.** On startup, if the table is empty it is
  seeded from `BACKSCATTER_LOCATIONS` (or the single `BACKSCATTER_LAT`/`LON`
  fallback). Once it has rows, the **DB is the source of truth** and the env seed is
  ignored. Existing env-only setups keep working (they seed the DB on first run).
- **Site is derived, never stored** — resolved from lat/lon via `nearest_site`
  (ADR-0005), so editing coordinates re-resolves the active radar automatically.
- **Infra config stays in env** (data dir, DB path, poll interval, the global `SITE`
  override). Only the location *list* — user-managed runtime content — moves to the
  DB. Clean split: infra config vs user content.
- **Invariants enforced on every write** (≥1 location, exactly one default, unique
  names), in a transaction, plus a partial unique index (`WHERE is_default = 1`) as a
  DB-level backstop. Deleting the **last** location or the **current default** is
  rejected (the user sets another default first) — a hard, predictable invariant.
- **Collector live-reload:** `run_collect` re-reads the location list from the store
  **each cycle** rather than caching it; an added location archives on the next
  cycle, a deleted one stops. WAL + a busy timeout handle the serve-writer /
  collect-reader (and writer/writer) concurrency the DB already supported.
- **No auth** on the write endpoints, consistent with every existing read endpoint
  and the LAN-first, not-life-safety framing (CLAUDE.md).

## Consequences
- No new file format, atomic-write, file-watch, or cross-process write-race
  machinery — the DB already provides transactions + WAL concurrency.
- Config is split: a frozen `Config` carries infra + the env seed; locations are read
  live from `store/locations` (resolved, with ids). Consumers that need the default
  or the list query the store rather than a static `config.locations`.
- A restart with no env preserves DB-stored locations (they don't revert to the
  seed) — the persistence guarantee.

## Alternatives considered
- **TOML file.** Rejected: needs a writer + atomic rename + a re-read/watch story for
  live-reload + its own cross-process concurrency model — real machinery for data
  that is runtime content, not hand-edited code config. (Hand-editability/version
  control is the only real pull; it loses to the DB's transactional simplicity here.)
- **Keep locations in env, restart to change.** Rejected: that's exactly the
  limitation this slice removes.
