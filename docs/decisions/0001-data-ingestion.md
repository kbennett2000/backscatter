# 1. Ingest assembled volumes from the archive bucket

## Status
Accepted

## Context
NOAA exposes NEXRAD Level 2 data in two free, anonymous S3 buckets:

- `unidata-nexrad-level2` — fully assembled volume scan files (`_V06`), one object
  per complete scan, organized by `YYYY/MM/DD/SITE/`.
- `unidata-nexrad-level2-chunks` — near-real-time chunks (≈100° of one tilt each)
  in rotating per-volume directories, bzip2-compressed. Lower latency, but each
  object is a partial sweep that must be reassembled into a volume before it's
  usefully renderable, and "all data may not be populated" mid-volume.

Both are accessible with no AWS account via unsigned requests. A full volume scan
appears in the archive bucket within seconds-to-minutes of completion.

## Decision
v1 ingests **assembled volumes from `unidata-nexrad-level2`**. To get the latest
frame for a site: list the site's prefix for the current UTC date, take the newest
key (fall back to the prior date near UTC midnight), download it. Dedupe on the
scan timestamp parsed from the filename.

## Consequences
- One downloaded object = one complete, immediately renderable frame. No
  chunk-reassembly logic in the pipeline.
- Frame latency is a few minutes behind real time — fine for an archive/playback
  tool, and within the radar's own scan cadence anyway.
- The "as fast as possible" ceiling is the radar's scan rate (~4–10 min), so a
  ~60s poll with timestamp dedupe captures every volume without hammering S3.

## Alternatives considered
- **Chunks bucket for lowest latency.** Rejected for v1: requires reassembling
  partial sweeps from rotating volume dirs and handling incomplete data — real
  complexity for ~1–2 min of latency we don't need yet. Revisit only if a
  low-latency live view becomes a goal.
