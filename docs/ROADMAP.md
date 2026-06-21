# Roadmap

Built as a sequence of vertical slices. Each slice is independently reviewable,
testable, and useful on its own. We complete and merge one before starting the
next. (Flexible — if collapsing two makes sense, we'll discuss it.)

## Slice 1 — Ingestion
Pull the latest assembled Level 2 volume for a given site code (default **KFTG**)
from `unidata-nexrad-level2`, store the raw file, record it in a SQLite index.
Dedupe on the volume's scan timestamp.
- **CLI:** `backscatter pull KFTG`
- **Done when:** running it lands a real `_V06` file on disk + a matching index
  row; re-running doesn't duplicate; tests cover newest-key selection, dedupe, and
  the UTC-midnight fallback (mock S3). Plus one real end-to-end pull.
- No decoding, no rendering yet.

## Slice 2 — Site selection (any US location)
Bundle the static NEXRAD site table (~160 sites, ICAO + lat/lon). Resolve the
active radar from a configured lat/lon by great-circle distance; expose the full
ranked list for later failover.
- **CLI:** `backscatter site --near "<lat>,<lon>"` (and config-driven default)
- **Done when:** any CONUS lat/lon resolves to the correct nearest site, with a
  sane ranked list behind it; tested against a handful of known points. Ingestion
  now feeds on the resolved site code.

## Slice 3 — Decode + single-frame render
Read a stored volume with Py-ART, extract **lowest-tilt (0.5°) reflectivity at
super-res**, reproject to web mercator, render one georeferenced PNG with a real
color table.
- **CLI:** `backscatter render <volume>`
- **Done when:** a stored volume produces a correctly georeferenced image whose
  bounds and orientation are verified against a reference for the same timestamp.
  Geometry + color mapping have value-based tests.
- Still no web server.

## Slice 4 — Serve + map
FastAPI serves the rendered frame(s); a minimal MapLibre page overlays the latest
frame on a no-token basemap, centered on the configured location.
- **Done when:** you can open a browser on the LAN and see the latest radar frame
  correctly placed on the map.

## Slice 5 — Continuous collection
A scheduler loop: resolve site → pull → decode → render → index, on a sane
interval (~60s poll, deduped). Runs as a long-lived service. Fails over to the
next-nearest site when the primary has no recent data.
- **CLI:** `backscatter collect`
- **Done when:** left running, it steadily accumulates frames with no dupes,
  survives transient S3/network errors, and fails over cleanly.

## Slice 6 — Playback
Timeline API returns available frame timestamps for a range; the frontend gets a
scrubber / play control that cycles cached frames.
- **Done when:** you can scrub and play back across the full collected archive in
  the browser. This is the feature subscription apps don't give us.

## Slice 7 — Archive navigation
Slice 6's timeline only reaches the most recent ~500 frames. Make the *whole*
archive navigable: a UTC date/time range picker bounded to what exists, plus
cursor pagination so a window deeper than the per-request cap is reachable without
silently truncating history.
- **API:** `/api/frames` gains a `cursor` (exclusive `scan_time` lower bound) and
  returns `next_cursor` for forward paging; `/api/frames/range` reports a site's
  min/max/count.
- **Frontend:** range picker + presets driving the timeline; playback pages through
  a long window transparently (fetch the next page near the end) so it never
  dead-ends. Default load stays the recent rolling window.
- **Done when:** you can pick any historical window the archive covers and scrub /
  play across it, paging through spans larger than one request without gaps or dupes.

## Slice 8 — Multiple locations + collect-all
Generalize the single configured location into a named list (one flagged
Home/default) and make `collect` archive all of them continuously.
- **Config:** `BACKSCATTER_LOCATIONS` JSON list (back-compat: the old single
  lat/lon = a one-entry "Home"); validate ≥1, exactly-one-default, unique names; an
  explicit SITE override pins Home only. Frames stay per-radar (no index change).
- **Collector:** each cycle iterates every location, resolving its nearest site and
  pull→render→indexing — deduped on `(site, scan_time)`, so co-located locations
  converge on one frame. Per-location failover + resilience.
- **API:** `/api/locations`; `/api/frames`, `/api/frames/range`, `/api/latest` take
  an optional `location` (defaults to Home).
- Data model + collector + API only; UI (active-location switching) is Slice 9.
- **Done when:** collect archives several locations at once, two co-located ones
  produce a single shared frame (no double pull/store), and the API resolves any
  configured location to its site.

## Slice 9 — Location switcher UI
The runtime piece deferred from Slice 8: an in-UI location selector (frontend only —
no API/collector changes).
- A dropdown populated from `/api/locations` (name + resolved site), Home preselected.
- Selecting a location re-fetches its frames via the `location` param, re-points the
  timeline/scrubber at that location's archive, and re-centers the map on its lat/lon;
  per-frame bounds keep a different radar's frames correctly placed. The active
  location persists across reload (localStorage). Single-location hides the selector.
- **Done when:** you can switch from Home (KFTG, Elizabeth) to e.g. OKC (KTLX) and the
  map re-centers and shows that radar's frames, correctly georeferenced.

## Slice 10 — Location management (CRUD, persisted, live)
Turn locations from read-only env config into mutable, persisted state managed from
the UI (ADR-0008).
- **Persistence:** locations move to a SQLite `locations` table; env JSON only seeds
  an empty store, then the DB wins. Site stays derived (re-resolves on edit).
- **Write API:** `POST/PUT/DELETE /api/locations` with the invariants enforced on
  every write (≥1, exactly one default, unique names); deleting the last or the
  default is rejected.
- **Collector live-reload:** `collect` re-reads the store each cycle — an added
  location archives next cycle, a deleted one stops, no restart.
- **UI:** a manage-locations panel (add / edit / delete / set-default, click-map to
  set coords) that refreshes the Slice-9 switcher; backend validation shown inline.
- **Done when:** you can add/edit/delete locations in the browser, the running
  collector picks them up within a cycle, and a restart preserves them.

## Slice 11 — Retention / pruning
There is no retention: `collect` keeps every volume + frame forever and collect-all
makes the archive grow unbounded. Bound it with configurable retention (ADR-0009).
- **Policy:** an **age limit** (default 30 days, ON) and a **size cap** (default
  OFF/unlimited — opt-in; no surprise GB default). Both can be active; a frame is
  pruned if it violates **either** (older than the age limit, or in the oldest-first
  overflow above the size cap). Global across the whole archive; size accounting is
  real on-disk bytes.
- **Prune:** removes the raw volume, its rendered PNG + sidecar, and the index row
  **together** (files-first, then row; idempotent on missing files; a file error skips
  that frame, never orphaning the index). A pruned frame disappears from the timeline
  cleanly — no dangling row, no 404.
- **Where:** a throttled pass in the collect loop (first cycle, then ≤ once per
  `BACKSCATTER_PRUNE_INTERVAL`, default 1h) — self-bounds with no cron — plus a manual
  `backscatter prune`. `--dry-run` previews (count / oldest-newest / bytes / by-reason)
  and deletes nothing; the live command prompts `[y/N]` unless `--yes`.
- **Config-driven, no UI this slice.** New env: `BACKSCATTER_RETENTION_DAYS` (0 = off),
  `BACKSCATTER_RETENTION_MAX_GB`, `BACKSCATTER_PRUNE_INTERVAL`.
- **Done when:** an over-age / over-cap archive prunes to within policy (oldest-first,
  exact bytes), a pruned frame is gone from `/api/frames` with its files removed, and
  `--dry-run` reports the same set without deleting anything.

## Slice 12 — First-class backfill command
Replace the throwaway demo script with a real command that fills the archive with
historical data on demand — the same per-volume pipeline as collect, over a past
range instead of "latest".
- **Command:** `backfill [target] --start <UTC> --end <UTC> [--dry-run] [--yes]`.
  `target` is a location name or site code (defaults to the configured site). Lists
  assembled volumes for the site across the range, then per volume:
  dedupe → download → render → index, reusing the existing pipeline
  (`pull.fetch_key` + `collect.render_and_index`). No failover (one site).
- **Dedupe / idempotent:** skips `(site, scan_time)` already indexed; re-running a
  range adds nothing. Composes with the archive — fills holes, leaves frames alone.
- **Dry-run:** reports volume count / span / exact listed bytes and fetches nothing.
  The live run prints the same plan, then prompts `[y/N]` on a TTY unless `--yes`.
- **Retention interaction (ADR-0009):** if the range falls (partly) older than the
  active age window, it WARNS that those frames will be pruned on the next prune pass
  (raise/disable `BACKSCATTER_RETENTION_DAYS` to keep them) — does not refuse.
- **Resilience:** a bad/un-decodable volume is marked render-failed (kept); a fetch
  error is skipped — a long backfill never aborts on one volume. End-of-run summary.
- **Done when:** a dry-run reports the range without fetching, a real backfill of a
  past range lands frames that scrub in the timeline, a re-run adds nothing, and the
  retention warning fires on a range older than the configured window.

## Slice 13 — Timeline gap indicator
The scrubber is index-spaced, so a large *time* hole between consecutive frames
(collect was down, backfill hasn't filled it) looks continuous. Make holes visible —
frontend-only, derived from the `scan_time`s `/api/frames` already returns (no backend
change, no new endpoint).
- **Detection (the testable core):** a gap is any consecutive interval longer than
  `GAP_FACTOR × median(interval)` over the loaded window (median so the gaps don't
  inflate the baseline; factor 3). Derived, not a hardcoded 5 min, so normal clear-air
  spacing isn't flagged but a real ≥30-min hole is. Pure `detectGaps` in `web/gaps.js`,
  unit-tested with `node --test web/gaps.test.js`.
- **Display:** amber hatched segments on the scrubber track at each gap step; a
  "⚠ gap before · 1h 32m" trailing-edge indicator when the current frame sits just
  after a gap. Markers recompute as paginated windows fill in.
- **Playback:** marker-only — playback runs **across** gaps, never auto-pauses or skips.
- **Done when:** a real gap is visibly marked and normal cadence isn't (screenshot
  gate), and the detection rule passes its unit tests.

## Later (not scheduled yet)
- Velocity and dual-pol products; product switcher
- MRMS national composite at low zoom (wide-area context — the *right* way to use
  multiple radars; see ADR-0005)
- Selectable color palettes
- Higher-fidelity client-side / WebGL radial-sweep rendering (the "real RadarScope
  look")
- Retention / archive-management tooling
