# backscatter

Self-hosted, browser-based NEXRAD weather radar with an **unlimited playback archive**.

backscatter pulls free NEXRAD Level 2 radar data from NOAA's public archive,
renders it on a map in your browser, and — unlike subscription radar apps — keeps
every frame it collects. Scrub back across an entire storm, or an entire season,
instead of being capped to the most recent loop.

It renders single-site **Level 2 super-resolution** (0.5° × 250 m) — the highest
resolution physically available for a location — and works for **any location in
the continental US**: point it at a lat/lon and it picks the nearest covering
radar automatically.

> **Status:** early / work in progress. Built as a personal project.

## Why
Good radar apps are subscription-priced and cap how far back you can replay. The
underlying data is public and free. backscatter is a LAN-hosted alternative that
costs nothing to feed — no API keys, no paid data, no credit card — and builds a
long-running archive so playback isn't limited to recent data.

## Planned features
- Continuous collection of NEXRAD Level 2 volumes, automatic nearest-radar
  selection for any CONUS location
- Lowest-tilt reflectivity at native super-resolution, rendered on a MapLibre map
  (velocity / dual-pol products later)
- Unlimited timeline scrubbing / playback over the collected archive
- Failover to the next-nearest radar when the primary is offline
- Runs as a self-hosted service on a home server

## Data
Radar data comes from the NOAA Open Data Dissemination program's public S3 buckets
(NEXRAD Level 2), hosted by NSF Unidata. Access is anonymous and free.

This project uses unaltered NOAA NEXRAD data. NOAA makes the data openly
available; **this project is not endorsed by or affiliated with NOAA or the
National Weather Service.**

Nearest-radar selection uses a bundled static NEXRAD site table
([`src/backscatter/sites/nexrad_sites.csv`](src/backscatter/sites/nexrad_sites.csv)),
derived from the NOAA/NCEI HOMR station list (see that file's header for source URL
and retrieval date). It ships with the package and is never fetched at runtime.

## Not for life-safety
backscatter is an enthusiast tool. **Do not** use it for protection of life or
property. For warnings and official guidance, rely on the National Weather Service
and NOAA Weather Radio.

## Quickstart
Early build — a few commands work end to end:

```
uv run backscatter pull              # fetch the latest volume for the configured site
uv run backscatter render <volume>   # render one georeferenced frame (PNG + bounds)
uv run backscatter serve             # serve the map UI at http://<host>:8000
```

`serve` opens a MapLibre map (keyless OpenFreeMap basemap) centered on your
configured location, with a **timeline scrubber + play/pause** over every frame
`collect` has accumulated — scrub or loop across the whole archive. With multiple
configured locations, a **location selector** switches the active one (re-centering
the map and re-pointing the timeline at that location's radar). Locations are
**managed in the UI** (add / edit / delete / set-default) and persisted in the
SQLite store; `BACKSCATTER_LOCATIONS` only *seeds* an empty store (see ADR-0008). A
running `collect` picks up location changes within a cycle — no restart. Run
`backscatter collect` for a while first to build up frames (one every ~5 min).
See [`docs/ROADMAP.md`](docs/ROADMAP.md) for build status.

The timeline is driven by `GET /api/frames?site=&start=&end=&cursor=&limit=` —
rendered frames from the index, oldest-first, capped per request (default 500, max
2000). With no `start`/`cursor` it returns the most recent window; pass `start`/`end`
to load a historical window and follow `next_cursor` (an exclusive `scan_time`
cursor) to page forward through spans larger than one request — the UI does this
transparently while playing. `GET /api/frames/range?site=` reports the archive's
min/max scan time and count so the picker can bound itself to what exists.
(URL-encode timestamp params — `scan_time` contains `+`.)

## License
TBD.
