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

## Later (not scheduled yet)
- Velocity and dual-pol products; product switcher
- MRMS national composite at low zoom (wide-area context — the *right* way to use
  multiple radars; see ADR-0005)
- Selectable color palettes
- Higher-fidelity client-side / WebGL radial-sweep rendering (the "real RadarScope
  look")
- Retention / archive-management tooling
