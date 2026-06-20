# 4. MapLibre GL JS frontend, no-token basemap, FastAPI serving

## Status
Accepted

## Context
We need a slippy map in the browser to overlay radar frames and drive playback.
The obvious option, Mapbox GL, now requires an account/credit card for a token —
which violates the no-paid-anything constraint. The frontend's job is narrow:
show a basemap, overlay our radar tiles, and provide a timeline scrubber.

## Decision
- **MapLibre GL JS** for the map (open-source Mapbox GL fork, no token), with a
  **no-token basemap** source.
- **FastAPI** serves the rendered radar tiles and a small JSON API for the frame
  timeline (available timestamps for a range).
- Keep the frontend **deliberately light** — vanilla JS or a thin setup, no heavy
  SPA framework for v1.

## Consequences
- Zero map cost, no credit card, consistent with the project's whole premise.
- Backend stays a single FastAPI app (tiles + API + static frontend).
- We pick a free basemap provider/style; if one changes terms, MapLibre lets us
  swap the source without touching app logic.
- No framework build complexity to fight early on.

## Alternatives considered
- **Mapbox GL.** Rejected: requires a credit-card-backed token. Hard no.
- **Leaflet.** Viable and lighter, but MapLibre's GL rendering and vector/raster
  flexibility fit the radar-overlay + future-WebGL direction better.
- **A full SPA framework now.** Deferred: unnecessary weight for v1's needs.

## Update — Slice 4 (concrete basemap pick)
The free basemap provider is **OpenFreeMap** (`https://tiles.openfreemap.org/styles/liberty`):
genuinely keyless — no account, token, or credit card — and its vector style
includes admin boundaries (state lines), which makes radar georeferencing easy to
eyeball. MapLibre GL JS is loaded from a pinned CDN. If OpenFreeMap ever changes
terms, only the style URL in `web/app.js` changes (e.g. to self-hosted tiles or OSM
raster). MapLibre itself is served from a CDN today; vendoring it locally is a
trivial later change if full offline/LAN operation is wanted.
