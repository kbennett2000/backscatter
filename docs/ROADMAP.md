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

## Slice 14 — Docker packaging
Package the whole app as one self-hosted container so `docker compose up` is the entire
install. Net-new infra — no app behavior change, no features.
- **One container, both processes:** a lean bash entrypoint runs the FastAPI server and
  the collect loop; if either exits the container exits (compose `restart` brings it
  back — never half-up). `init: true` reaps/forwards signals; collect's existing
  SIGTERM handling shuts down cleanly. Server starts first and seeds the DB before
  collect starts (avoids the empty-DB seed race on a fresh volume).
- **Base image (the build risk):** `python:3.12-slim-bookworm` (glibc) — Py-ART's stack
  (numpy/scipy/netCDF4/cartopy/matplotlib) is manylinux-wheels-only, so **not Alpine**.
  An ldd of the wheels needs only `libstdc++6`/`libgomp1`/`libz`/`ca-certificates` from
  the system; everything heavy is bundled. Multi-stage with uv; no build tools in the
  runtime image.
- **Persistence:** raw volumes + renders + SQLite DB live on a host bind mount
  (`./data` → `/data`), so `down`/`up`/rebuild never lose the archive. Container runs as
  the host UID (`PUID`/`PGID`) so the mount is writable non-root.
- **Config via env** (maps to existing Config, no code change): `BACKSCATTER_LOCATIONS`
  seeds the store on first run, then the DB wins (Slice-10 flow); retention/poll/site
  pass through `.env`.
- **Deliverables:** `Dockerfile`, `docker-compose.yml`, `.env.example`, `.dockerignore`,
  README "Run with Docker".
- **Done when:** the image builds clean (pyart imports + reads a volume in-container),
  `docker compose up` serves the UI on the LAN with collect cycling, and the archive +
  seeded locations survive `down`/`up`/rebuild.

## Slice 15 — Banner image + repo polish
Public-facing polish ahead of the docs site — no app behavior change.
- **Banner** (`docs/assets/banner.svg` + rendered `banner.png`): on-theme radar-sweep
  motif in the real NWS dBZ palette (storm cells, range rings, amber sweep beam) +
  wordmark; PNG rendered from the SVG via headless Chrome.
- **Hero screenshot**: live reflectivity on the map + the playback timeline with gap
  markers (later superseded by the pinned `app-overview.png` in Slice 17).
- **LICENSE** (MIT, matching pyproject); README License section fixed from "TBD" so
  GitHub detects the license.
- **README top** rebuilt: banner → tagline → honest static badges (license / python /
  docker — no fake CI badge) → what-is-this → screenshot → quick links.
- **CONTRIBUTING.md** stub (dev setup + checks + conventions; expanded by Slice 16).
- **GitHub metadata**: repo description + topics set via `gh`.
- No cargo-cult community files (no CoC / issue templates / FUNDING / CI yet).

## Slice 16 — Documentation site
A full docs site for a zero-assumptions, weather-curious audience — plus a developer
branch. MkDocs Material in `docs/`, deployed to GitHub Pages by a build workflow.
- **For everyone:** a plain-language [Home](https://kbennett2000.github.io/backscatter/),
  three click-by-click **platform install guides** (Windows / macOS / Linux, Docker path,
  nothing assumed), a Configure guide (location + retention in plain words), a Using tour
  (radar colors, timeline, playback, gaps — with GIFs), and a Help/FAQ.
- **For developers:** the openness pitch, a fast `uv` local-run path, an architecture
  page (mermaid pipeline diagram + module map), testing, the CONTRIBUTING expansion, and
  the ADR index — with ROADMAP + ADRs surfaced in the nav.
- **Capture automation:** `scripts/capture_docs.py` drives the live app with Playwright
  and builds GIFs via ffmpeg → real, repeatable screenshots/GIFs (not hand-grabbed).
- **Tooling isolation:** docs deps are a uv `docs` group — never in the app runtime or
  Docker image. Win/Mac Docker-Desktop install is text + official link (can't authentically
  capture those installers); everything else is captured live.
- **Done when:** `mkdocs build --strict` is clean, the Pages workflow publishes the live
  site, and a non-technical reader can get from "what is this" to a running app.

## Slice 17 — Location pins + configurable port + README GIF
Three batched changes; no new product behavior beyond the pins.
- **Labelled location pins:** a marker for every configured location, drawn from the
  existing `/api/locations` (no backend change) as a MapLibre circle + text layer. The
  active location is amber/larger, the others muted white; labels collide-hide so close
  names stay readable; pins sit above the radar and are click-through. They update live
  on switch and on the location CRUD path. Pure GeoJSON builder (`web/markers.js`)
  unit-tested with `node --test`, like `gaps.js`.
- **Single configurable port:** `BACKSCATTER_PORT` (default **8085**) replaces hardcoded
  `8000` everywhere — one env value drives the in-container and published port and the
  healthcheck; the CLI `serve` default is 8085. Docs/README updated with a plain-language
  "changing the port" note.
- **README:** hero repointed to the re-captured (pinned) `app-overview.png`; playback GIF
  embedded; capture script re-run so all map imagery shows the pins.

## Maintenance fixes (post-Slice-17)
Docker robustness, no app behavior change:
- **Build perf:** removed a redundant `chmod -R a+rX /app` that recursed the ~36k-file
  scientific-stack venv (~150s on a home server) to set perms uv already produces; the
  entrypoint exec bit is set with `COPY --chmod` instead.
- **Fresh-deploy crash:** a missing `./data` was auto-created by Docker as root, so the
  non-root container couldn't write the DB ("unable to open database file" crash-loop).
  Fixed with a tracked empty `data/.gitkeep` (the dir exists owned by the cloning user)
  + an entrypoint writability check that prints an actionable `chown` message + a Help
  & FAQ entry.

## Slice 18 — First-run honesty + smart default view
Make a brand-new (empty) app and a returning (populated) one both immediately legible —
never a blank, baffling map. Frontend only; reuses the existing read-only frame APIs.
- **Three distinct states** (were one collapsed "no frames" dead end): **empty archive** →
  a friendly "collecting now, first frame in ~5 min, updates on its own" card + a
  load-history link; **wrong time-window** → a visibly different "no radar in this window;
  you have data from X to Y (local)" card + **Jump to latest**; **has data** → radar.
  Pure `chooseView` in `web/firstrun.js`, unit-tested like `gaps.js`/`markers.js`.
- **Default view = latest** (never a back-dated window that may be empty).
- **Live status cue** near the readout, freshness-honest: "Collecting · last frame 12:56 PM"
  when fresh, "Last frame … · checking for new radar…" when stale, "waiting for the first
  frame…" when empty.
- **Considerate auto-update:** polls `/api/frames/range` (~30s) only on the empty/latest
  views (and only while visible); the empty card auto-dismisses when the first frame
  lands; a newer frame on the live view shows a non-intrusive "New radar available" nudge.
- **Local time** in the readout/frame time/messages (the time *picker* stays UTC — that
  overhaul is a later clarity slice).

## Slice 19 — One-click "Load recent radar now" (web-triggered backfill)
Put the existing backfill pipeline (Slice 12) behind a one-click web button so a
first-run user gets recent history without the CLI — the load-bearing other half of
Slice 18's empty state. (ADR-0010.)
- **Async job, not a blocked request:** a backfill is minutes of download + render, so
  `POST /api/backfill` starts a daemon-thread job and returns immediately; the UI polls
  `GET /api/backfill/{id}`. An in-memory `JobManager` runs **one job at a time** (a second
  start → 409). No external queue/broker.
- **Two writers, one DB, two processes:** the job writes the index while the live
  collector also writes it. `serve` and `collect` are separate processes, so safety rests
  on SQLite **WAL + `busy_timeout` (raised to 15s) + `UNIQUE(site, scan_time)`**, *not* an
  app-level lock — proven by a concurrency test (two threads, overlapping keys → no lock
  error, no dupes, `integrity_check` ok).
- **Bounded:** a click loads the **last 6 hours**, hard-capped at 24h server-side (well
  inside retention, so no prune warning). Reuses `run_backfill` unchanged but for an
  additive `progress_cb`; same dedupe / skip-on-bad-volume / idempotency.
- **Frontend:** the empty card's docs link becomes a primary **"Load recent radar now"**
  button with a live progress bar ("Loading radar… 12 of 48 frames"); on success the
  timeline auto-populates, on failure a plain "try again." Also offered as a secondary
  action on the wrong-window card. Pure `web/backfill.js`, unit-tested like `firstrun.js`.

## Slice 20 — Bilinear interpolation in the radar render
Smooth the nearest-neighbour blockiness so single-site reflectivity approaches the
RadarScope look. Investigation first confirmed we already decode + paint full native
super-res (720×1832 @ 250 m, 1:1 in range) — the blockiness was purely sampling: NN
stamped each 0.5° ray (wider than a 250 m pixel past ~29 km) across many pixels → fan-
wedges + per-ray speckle.
- **Bilinear across the 4 nearest gates** (2 rays × 2 gates) in (azimuth, range), on dBZ
  before the palette. Pure helpers `_bracket_rays`/`_bracket_gates`/`_bilinear_sample` in
  `render/raster.py`, fully vectorized.
- **Conservative masked-edge rule (the correctness crux):** a pixel is blended **only when
  all 4 corners are valid**; otherwise it keeps the nearest sample (NaN where no-data). So
  the valid/no-data boundary is **pixel-identical to NN** — interpolation never invents
  data or moves a feature, it only smooths the interior of real returns. Verified: non-NaN
  coverage on a real KFTG render is identical to NN (only interior values changed); visual
  check vs the NN render confirmed cells/edges unmoved.
- **Cost:** ~1.22× render time (4.83s → 5.90s on a real volume) — fine for collect/backfill.
- Note: **existing cached PNGs stay NN until re-rendered**; new renders/backfills use bilinear.

## Slice 21 — Light/dark mode toggle
A ☀/☾ button in the control bar switches the app between light and dark. UI theming only —
the NWS dBZ radar palette is byte-identical in both (it's baked into the PNGs; the theme
path only touches the basemap, CSS chrome, and pin paint).
- **Basemap swap:** keyless OpenFreeMap `liberty` ↔ `dark` (no key/credit card, same
  attribution). `setStyle` wipes custom layers, so the radar (current frame) + location pins
  are re-added on the map's next `idle` (with `diff:false`) — robust where `style.load` /
  `isStyleLoaded()` fail headless when a basemap sprite 404s.
- **Chrome via CSS variables:** `:root` = light, `[data-theme="dark"]` = dark; `--accent`
  amber identical in both. Pins (white + dark halo, amber active) read on both basemaps.
- **Default + persistence:** follows the OS `prefers-color-scheme` on first load (default
  light), then the explicit choice persists (`localStorage`); a `<head>` shim sets the theme
  before first paint (no flash).
- Pure `web/theme.js` (resolveInitialTheme/nextTheme/basemapFor), node --test'd like
  firstrun.js. One dark-mode screenshot added to the docs.

## Slice 22 — Mobile UX overhaul
Make the app genuinely usable on a phone (portrait, 360–430px) without changing the desktop
layout. Pure responsive layout/CSS — no features, no rendering/data/backend change.
- **One breakpoint:** a single `@media (max-width: 600px)` block carries the whole mobile
  treatment; desktop (≥601px) is untouched.
- **Window-controls drawer:** the time-window controls (label, start/end, Load, 6h/24h,
  Latest, extent) move into a `#windowctl` group that is `display:contents` (inline) on
  desktop and a dropdown **drawer** on mobile behind a `🕘 Window` toggle. The top bar goes
  slim/full-width (Locations collapses to its ⚙ icon); readout + status drop below it.
- **Touch + fit:** finger-sized targets (≥40px), enlarged MapLibre zoom moved clear of the
  bar, full-width timeline with the gap-flag on its own row, first-run/Locations panels fit
  (the latter scrolls). Button clarity folded in (labeled, headed drawer).
- Pure `web/layout.js` (`isMobile`/`BREAKPOINT`) node --test'd; app.js wires the drawer
  toggle + resize auto-close. Verified on a 390×844 phone viewport and a real device.

## Later (not scheduled yet)
- **Storm track lines / motion vectors** — parked as a real computer-vision effort (cell
  identification + tracking across frames), not a quick slice.
- Velocity and dual-pol products; product switcher
- MRMS national composite at low zoom (wide-area context — the *right* way to use
  multiple radars; see ADR-0005)
- Selectable color palettes
- Higher-fidelity client-side / WebGL radial-sweep rendering (the "real RadarScope
  look")
- Retention / archive-management tooling
