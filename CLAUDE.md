# CLAUDE.md — backscatter

## What this is
backscatter is a self-hosted, browser-based NEXRAD weather radar viewer with an
**unlimited playback archive**. It's a free replacement for the core RadarScope
loop: pull NEXRAD Level 2 data, render it on a map, and keep every frame so
playback isn't capped to "the last hour or so."

Two things define it:
- **Highest resolution for a location.** We render single-site Level 2
  super-resolution (0.5° azimuth × 250 m gates, lowest tilt) — the best data
  physically available for a point. No down-res compositing when zoomed in.
- **Unlimited archive.** We collect continuously and never throw frames away, so
  you can scrub back across a whole storm event — or a whole season — not just
  recent data. This is the part subscription apps don't give you.

It works for **any CONUS location**. The active radar is derived from a
configured lat/lon against a bundled site table (see ADR-0005), not hardcoded.
The default config points at the operator's location (KFTG / Denver coverage),
but that's just a default.

## Hard constraints (do not violate)
- **No paid anything.** No API keys, no paid data feeds, nothing that needs a
  credit card. This rules out Mapbox — use MapLibre. All radar data comes from
  NOAA's free public S3 buckets. If a task looks like it needs a paid service,
  stop and flag it; there is almost always a free path.
- **Self-hosted / LAN-first.** Runs on a home server (Ubuntu), not a cloud
  platform. No AWS account — S3 access is anonymous (`--no-sign-request` /
  unsigned boto3).
- **Not life-safety.** This is a hobby/enthusiast tool. Nothing here is for
  protection of life or property — that's what NWS and a RadarScope subscription
  are for. Keep that framing in the README/UI and don't add features that imply
  official warning capability.

## How we work together
- **Small, reviewable slices.** Default to the smallest load-bearing unit that
  can be reviewed and merged on its own — one vertical slice per branch/PR. This
  is a flexible guiding principle, not a hard rule: if a bigger monolithic change
  is genuinely the better call, say so and explain why. Don't sprawl, but don't
  fragment past the point of sense either.
- **Plan before non-trivial work.** Use plan mode for anything beyond a small,
  obvious change. Propose the plan, get sign-off, then build.
- **I read the diffs.** I review the actual diff before merging — don't rely on
  me trusting your summary. Keep diffs small, coherent, well-described. If a
  change is large or subtle, call out exactly what to look at and why.
- **Decisions get ADRs.** Any real architectural decision goes in
  `docs/decisions/NNNN-title.md` (context / decision / consequences /
  alternatives). Don't make a load-bearing choice silently.
- **Be frank.** Plain, direct, older-brother honesty. No flattery, no "great
  question," no hedging. If something I'm asking for is a bad idea, say so and
  why.

## The one thing that will bite us: rendering correctness
Radar rendering has a nasty failure mode — a wrong projection, a flipped axis, an
off-by-one in the gate geometry, or a bad color-table mapping produces an image
that *looks completely plausible* but is wrong. You cannot eyeball your way to
correctness here.

So: anything touching geometry (range/azimuth → lat/lon), reprojection, or color
mapping gets **tests against known values** plus a **visual sanity check** against
a reference (RadarScope, Supercell Wx, or the NWS site for the same timestamp).
Never merge a rendering change on "it produced an image." Produce the *right*
image and prove it.

## Data source (facts)
- **Archive bucket (assembled volumes) — primary source:**
  `s3://unidata-nexrad-level2/<YYYY>/<MM>/<DD>/<SITE>/<SITE><YYYYMMDD>_<HHMMSS>_V06`
  One object = one complete volume scan = one renderable frame. Files after
  ~2016-06 have no `.gz` suffix. (See ADR-0001.)
- **Real-time chunks bucket:**
  `s3://unidata-nexrad-level2-chunks/<SITE>/<VOLUME 1-999>/<YYYYMMDD-HHMMSS-CHUNKNUM-CHUNKTYPE>`,
  bzip2-compressed, partial sweeps in rotating volume dirs. Lower latency but must
  be reassembled — a deferred optimization, not v1.
- Access is **anonymous**: `aws s3 ls --no-sign-request s3://unidata-nexrad-level2/`.
  In Python, boto3 with `Config(signature_version=UNSIGNED)`.
- **Resolution:** Level 2 super-res is 0.5° × 250 m on the lowest tilts. For best
  low-level detail render the **lowest elevation (0.5°) reflectivity** at native
  super-res. This is the resolution ceiling — don't down-sample it.
- **Cadence ceiling:** a WSR-88D completes a volume scan roughly every 4–6 min
  (precip) to ~10 min (clear-air). You cannot get frames faster than the radar
  produces them. Poll on a sane interval (~60s) and dedupe on the volume
  timestamp — do not build a tight sub-minute loop.
- **Site selection:** derive the active radar from a configured lat/lon against a
  bundled NEXRAD site table. Nearest covering site = best data; keep a ranked
  list for failover. No multi-radar blending. (See ADR-0005.)
- Reading volumes: **Py-ART** (`pyart.io.read_nexrad_archive`) is the reference
  reader; MetPy is the alternative.
- **Attribution:** NOAA requires attribution and forbids implying NOAA
  endorsement. Keep the credit line in the README and UI footer.

## Stack & conventions
- **Python 3.12+**, managed with **uv**. Lint/format with **ruff**. Tests with
  **pytest**. Type hints everywhere; keep mypy-clean where practical.
- **Backend:** FastAPI — serves rendered tiles + a small JSON API for the frame
  timeline.
- **Radar processing:** Py-ART (`arm-pyart` on PyPI, imported as `pyart`) + numpy.
- **Storage:** raw volumes on disk (source of truth), SQLite for the frame
  index/metadata, rendered tiles cached on disk. (See ADR-0003.)
- **Frontend:** MapLibre GL JS, no-token basemap, kept deliberately light (vanilla
  or a thin setup — no heavy SPA framework for v1). (See ADR-0004.)
- Config (location/site, paths, intervals) lives in one config file / env — never
  hardcoded.

## Layout
```
backscatter/
  src/backscatter/
    ingest/      # S3 client, volume fetch + dedupe
    sites/       # bundled NEXRAD site table + nearest-site selection
    decode/      # Py-ART reading, product extraction
    render/      # reprojection, color tables, tile/image generation
    store/       # SQLite index + file layout
    api/         # FastAPI app: tiles + timeline endpoints
    cli.py       # operator commands (pull, render, serve, collect)
  web/           # MapLibre frontend
  tests/
  docs/
    ROADMAP.md
    decisions/
  CLAUDE.md
```

## Definition of done (per slice)
- Does the one thing the slice is for, end to end.
- Tests pass; rendering/geometry changes have value-based tests + a visual check.
- `ruff` clean.
- Diff is small and reviewable; PR description says what to look at.
- If a decision was made, the ADR is written.
- No paid dependency or credit-card service snuck in.
