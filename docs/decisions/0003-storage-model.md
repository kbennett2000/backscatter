# 3. Raw volumes as source of truth + SQLite index + cached tiles

## Status
Accepted

## Context
We collect frames continuously and forever. We need to store the radar data, know
what we have (for timeline/playback), and serve rendered images. A single Level 2
volume is roughly 5–20 MB; one site at one volume per ~5 min is a few GB/day —
trivial for a home server.

## Decision
- **Raw volumes on disk are the source of truth.** Keep every downloaded `_V06`
  file, laid out by site/date.
- **SQLite is the index.** One row per volume: site, scan timestamp, file path,
  render status, tile path(s). This drives the playback timeline and dedupe.
- **Rendered tiles are a cache on disk.** Re-derivable from raw volumes at any
  time; safe to evict/regenerate when color tables or render code change.

## Consequences
- Changing palettes/products = re-render from raw, no re-download.
- SQLite fits the appliance/LAN model — one file, no server, easy to back up.
- Storage grows linearly and predictably; retention tooling is a later concern,
  not an architectural one.
- Frame metadata queries (what's available in a time range) are simple SQL.

## Alternatives considered
- **Store only rendered tiles** (drop raw). Rejected: locks in today's render
  choices; any palette/product change would mean permanent loss of fidelity.
- **A real database** (Postgres, etc.). Rejected: over-engineered for single-node
  self-hosting; SQLite is enough and simpler to operate.
