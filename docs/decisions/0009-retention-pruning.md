# 9. Retention / pruning — bound the archive by age and/or size

## Status
Accepted. Amended by [ADR-0013](0013-runtime-retention.md): the policy is now
DB-backed runtime state edited in the UI; the env vars below seed it on first run only.

## Context
Through Slice 10 there was **no retention**: `collect` kept every raw volume and
rendered frame forever, and `collect-all` across multiple radars makes the archive
grow unbounded (multiple TB/year). The product still wants an *unlimited playback
archive* in spirit, but a self-hosted box has finite disk — we need a configurable,
automatic bound that the operator controls, without a separate cron.

Raw volumes are the source of truth (ADR-0003) and the real disk reclaim; a rendered
frame (PNG + JSON sidecar) and an index row hang off each one. The index is what the
timeline reads, and the API serves PNGs as static files — so a row whose PNG was
deleted is a **dangling row that 404s the scrubber**. Pruning therefore has to remove
the raw volume, the render artifacts, and the row **together**.

## Decision
Two independent, configurable limits, enforced by one archive-wide prune.

- **Age limit** — delete frames whose `scan_time` is older than N days. **Default 30
  days, ON.** Set `BACKSCATTER_RETENTION_DAYS=0` to disable.
- **Size cap** — when the archive's real on-disk footprint exceeds N GB, delete
  **oldest-first** (globally by `scan_time`) until back under. **Default OFF /
  unlimited.** We deliberately ship *no* GB default: an arbitrary cap could
  surprise-delete a user's data on first upgrade. Size retention is strictly opt-in
  (`BACKSCATTER_RETENTION_MAX_GB`).
- **Both active ⇒ a frame is pruned if it violates EITHER** — older than the age
  limit, *or* in the oldest-first overflow above the size cap (the union of the two
  candidate sets). So an old frame goes even if the archive is under cap, and the
  oldest frames go when over cap even if they're within the age window.
- **Global over the whole archive** (all radars/locations), not per-location. Frames
  are one row per `(site, scan_time)`, so co-located locations already share a frame;
  pruning a row reclaims it exactly once. Prune works off the `volumes` table and does
  not consult the location list.
- **Age is measured by `scan_time`** (the data's own timestamp), not `downloaded_at` —
  "older than 30 days" means the *weather* is >30 days old, the natural meaning for an
  archive. Live collection fetches a scan moments after it happens, so the two are
  effectively identical today.
- **Size accounting is real bytes** — we `stat` the actual files (raw + PNG + sidecar;
  a missing file counts 0), so the cap reflects true footprint, not row count. Only
  computed when the size cap is set.
- **Delete ordering: files first, then the row.** A missing file is treated as success
  (idempotent — self-heals a half-pruned frame on the next pass). A genuine file error
  (permission/locked) **skips that frame with its row left intact**, logged, and the
  loop/pass continues. The row is removed only after every file is gone, so the index
  never points at a deleted file. (Failure-mode tradeoff: in the rare case a crash
  lands between deleting a frame's files and its row, the next prune re-deletes the
  now-missing files as a no-op and removes the row — we prefer that self-healing path
  over ever exposing a dangling 404 row.)
- **Where it runs:** a throttled pass inside the collect loop (first cycle, then at
  most once per `BACKSCATTER_PRUNE_INTERVAL`, default 1h) so a long-running collector
  self-bounds without a cron — and a manual `backscatter prune` for on-demand reclaim.
  `--dry-run` previews (count, oldest/newest affected, bytes, by-reason) and deletes
  nothing; the live command prints the same summary then prompts `[y/N]` on a TTY
  unless `--yes`. Selection is one pure function shared by preview and live, so the
  dry-run is an exact forecast.
- **No retention-settings UI this slice** — config/env-driven only. A settings panel
  could come later; flagged, not built.

## Consequences
- The archive is bounded and the operator controls how. Defaults are safe: time-based
  trimming on, destructive size-capping off until explicitly enabled.
- Prune is decoupled from poll cadence (wall-clock interval), so changing the poll
  interval doesn't change how often we prune.
- A prune failure can never end collection (own try/except, mirroring per-cycle
  resilience); a single un-deletable file is skipped, not fatal.
- New env knobs live only in `config.py` (ADR-0006 single-source rule): a frozen
  `Config` gains `retention_max_age_days`, `retention_max_size_bytes`,
  `prune_interval_s`, plus a `retention_active` convenience.

## Known interaction — backfill (resolved in Slice 12)
Because age is by `scan_time`, **backfilled old volumes are immediately age-eligible** —
pulled and then pruned on the next pass. Slice 12's `backfill` command resolves this by
**warning, not exempting**: when a requested range falls (wholly or partly) older than
the active age window, `backfill` prints a clear warning (count of affected volumes +
the cutoff date) telling the user to raise or disable `BACKSCATTER_RETENTION_DAYS` to
keep them. We deliberately did *not* special-case backfilled data (no per-row "keep"
flag, no aging by `downloaded_at`): retention stays a single, predictable
scan_time-based rule, and the user decides whether a one-time historical look is worth
widening the window. The warning fires in both `--dry-run` and the live run, before any
fetch.

## Alternatives considered
- **Ship a default size cap.** Rejected: any concrete GB number risks deleting a
  user's archive the first time they run the new version. Age (a *time* bound) is a far
  safer default; size stays opt-in.
- **Separate prune cron / systemd timer.** Rejected for v1: the collector is already
  the long-lived process; a throttled in-loop pass needs no extra moving parts. The
  manual command covers on-demand/one-shot use.
- **Soft-delete (mark rows, sweep files later).** Rejected: adds index state and a
  second sweeper for no benefit here; the row *is* the timeline, so removing it is the
  cleanest "frame is gone."
- **Age by `downloaded_at`.** Rejected as the default (see above); revisit only if
  backfill needs it.
