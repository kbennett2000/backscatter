# 11. Near-real-time live frame from the chunks bucket

## Status
Accepted

## Context
The displayed radar ran ~5 min behind reality. A read-only lag investigation traced
~90% of that to a *by-design* choice (ADR-0001): we read the **assembled** bucket
(`unidata-nexrad-level2`), where one object is one complete volume scan. A volume must
finish scanning, assemble, and upload before that object appears — measured at a median
~309 s (≈5 min) after the volume's start, even though we only ever use the lowest
0.5° reflectivity cut, which is scanned *first*. No data/correctness bug was found; the
floor is structural. The only way under it is the real-time **chunks** bucket
(`unidata-nexrad-level2-chunks`), which RadarScope-class viewers read per-sweep.

Slice 26a proved the decode path: concatenating a volume's chunk objects in order
yields a partial AR2V stream that `pyart.io.read_nexrad_archive(BytesIO)` reads directly
(no MetPy), and at `radar.nsweeps >= 2` the 0.5° cut is frozen and **byte-identical to
the eventual assembled volume's tilt** (max abs diff 0.0 dBZ; the live PNG is md5-equal
to the assembled render). 26b wires that assembler into collection and serving.

## Decision
Add the live frame as an **additive** path; the assembled bucket stays the archive
source of truth (backfill, retention, byte-for-byte storage).

- **One frame, one row, a `source` flag.** A `source` column (`'assembled'` default |
  `'live'`) is added to `volumes` by an idempotent `ALTER TABLE` migration (the Slice-5
  pattern). `UNIQUE(site, scan_time)` is unchanged — a scan is one row regardless of how
  it arrived. The live frame is a first-class rendered frame; the serve/timeline API and
  the frontend are untouched (they `SELECT *` and never read `source`).
- **Live attempt in the collect loop, on the existing cadence.** After the assembled
  attempt each cycle, a gated live attempt finds the active volume dir, assembles the
  0.5° cut, renders it via `render_sweep`, and indexes it `source='live'`. The 0.5° cut
  completes ~73 s after volume start; the existing 30 s poll catches it within ~30 s →
  ~1–2 min displayed latency. No separate timer/thread.
- **Reconciliation as a dedicated per-cycle sweep, not inline in the fetch.** The
  assembled path only ever fetches the *single latest* volume per cycle, so upgrading a
  live row only when that fetch happens to hit its scan would strand any scan polling
  skipped as a permanent partial. Instead, each cycle a sweep takes every `source='live'`
  row older than `LIVE_RECONCILE_DELAY` (6 min — past the measured ~5 min S3 lag),
  checks the *deterministic* assembled key (`naming.archive_key`), and if present
  downloads the complete volume, **overwrites the partial artifact in place**, and
  `UPDATE`s the row to `source='assembled'` (path/s3_key/size only). **No re-render** —
  the PNG is provably identical (26a), so `render_status`/`image_path` are untouched and
  the displayed frame never visibly changes. A missing object or any fetch error leaves
  the row untouched to retry next cycle; the `UPDATE` is the only mutation and runs only
  after a successful download, so a failure never corrupts the index.
- **Bounded cost.** A per-site cursor rides the active dir across polls (one cheap LIST),
  and a decode is attempted only when chunks advanced *and* the scan isn't already
  indexed — a completed/indexed volume is never re-decoded. The active-dir scan (O(dirs),
  ~45 s serial for ~500 rotating dirs) is **parallelized to ~2 s** and run only at cold
  start or volume rollover. A still-incomplete volume older than a give-up age is
  abandoned to the assembled path so the cursor can't stick.
- **Off switch.** `BACKSCATTER_LIVE_CHUNKS` (default on). Off = exactly the prior
  assembled-only behavior — a clean fallback.

## Consequences
- Displayed live latency drops from ~5–6 min to ~1–2 min. Measured live run: a live
  frame 0.6 min old while the freshest assembled volume was 6.0 min old (5.4 min closed).
- The archive still ends as the complete assembled volume for **every** scan_time — live
  rows are upgraded in place, never left as permanent partials, and never duplicated. A
  live partial is written to the *canonical* path so the upgrade overwrites it (no orphan).
- The live write is a fourth concurrent writer across the same `(site, scan_time)` rows
  (collect-assembled, backfill, API edits, now live). WAL + `busy_timeout` + the UNIQUE
  backstop already cover it; proven by a three-writer (live + reconcile + backfill)
  concurrency test — no duplicate rows, no lock errors, index intact.
- Still keyless/anonymous, no new dependency (pyart only).

## Alternatives considered
- **Inline upgrade in `fetch_key`** (the originally-sketched approach): smaller change,
  but only upgrades the latest scan the assembled fetch happens to hit — intermediate
  live scans skipped by polling would stay partial forever. Rejected: it can't deliver
  the "every scan ends complete" guarantee. The dedicated sweep does, for the same cost.
- **A separate faster live poll loop/thread.** Lower latency floor, but a second cadence
  to reason about and another thread against the same DB for marginal gain over the 30 s
  cycle. Deferred — revisit only if ~1–2 min proves not fresh enough.
- **Serve the live frame from a side channel** (not a `volumes` row). Would duplicate the
  timeline/serve logic and special-case the UI. Rejected: making it a normal row with a
  flag reuses everything and keeps one code path.
