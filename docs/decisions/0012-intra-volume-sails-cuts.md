# 12. Intra-volume 0.5° cuts (SAILS) as their own frames

## Status
Accepted

## Context
backscatter renders exactly **one frame per volume** end to end (ADR-0001/0011): the
lowest 0.5° reflectivity *surveillance* cut, the first sweep of the volume. During
precipitation, a WSR-88D runs **SAILS** (Supplemental Adaptive Intra-Volume Low-Level
Scan) or **MRLE**, inserting extra 0.5° surveillance cuts *mid-volume*, so the lowest
tilt is re-scanned every ~1.5–2.5 min instead of once per ~5 min volume. RadarScope and
peers render each of these as a frame; backscatter dropped all but the first, so during
storms its newest frame aged to ~5 min between volumes even though the live-chunks path
(ADR-0011) delivers that one cut ~20 s after the volume starts.

Confirmed live (2026-06-22): backscatter's newest frame was the correctly-served live
base cut, while RadarScope showed a fresher mid-volume cut that backscatter had decoded
but discarded. A real KFTG precip volume (`KFTG20260622_002420_V06`) has four sweeps at
0.5° — indices `[0, 1, 9, 10]`: a base **split cut** (surveillance sweep 0, then Doppler
sweep 1) and a SAILS **split cut** (surveillance 9, Doppler 10), the surveillance halves
~144 s apart. Both halves carry reflectivity in super-res, so reflectivity presence does
**not** distinguish surveillance from Doppler.

## Decision
Surface **every completed 0.5° surveillance cut** (base + each SAILS/MRLE re-scan) as
its own frame, **live-only and permanent**.

- **Surveillance discrimination — "first of each visit".** A 0.5° surveillance cut is
  the *first* sweep of each visit to the minimum elevation (`i == 0` or sweep `i-1` is
  not at the min); its Doppler twin (the immediately-following same-tilt sweep) is
  dropped. This selects the longer-range reflectivity scan, matches the proven `argmin`
  base selection exactly (the base cut stays byte-identical — max abs diff 0.0 dBZ vs
  the prior `sweep_from_radar`), and handles legacy single sweeps and SAILS/MRLE
  re-visits uniformly. Reflectivity-presence and gate-count heuristics were rejected as
  fragile; "first of visit" is structural.
- **Per-cut timestamp.** Each cut is stamped with its own sweep start time
  (`time['units']` epoch + the sweep's first-ray offset). The base cut's time equals the
  volume start (== the assembled `_V06` name), so it still reconciles; SAILS cuts land
  minutes later at distinct times.
- **Freeze rule, generalized.** A cut is renderable once a later sweep has begun
  (`index < nsweeps - 1`) — the same "next sweep started ⇒ this cut is fully swept" rule
  ADR-0011 used for the base (`nsweeps >= 2`), applied per cut.
- **Live-only / permanent SAILS.** The base cut keeps `source='live'` and reconciles to
  `'assembled'` when the volume lands. SAILS cuts get **`source='live-sails'`** and stay
  live forever: the assembled bucket has one object per volume named at the volume start,
  so there is no assembled object at a SAILS cut's timestamp to reconcile to. The
  reconcile worklist stays `WHERE source='live'`, so it never touches SAILS rows (no
  perpetual 404 HEADs). No schema migration: distinct per-cut `scan_time`s satisfy the
  existing `UNIQUE(site, scan_time)`; `'live-sails'` is just a new value in the existing
  `source` column, and serving never filters on `source`, so SAILS frames appear in the
  timeline like any other.
- **Assembled/backfill unchanged.** The archive path stays one-frame-per-volume;
  extracting SAILS cuts from assembled `_V06` files (for historical/backfilled data) is
  explicitly out of scope. SAILS frames are a live-collection-only enhancement.

Split across two slices: **27a** is the decode (`surveillance_sweeps` /
`try_decode_all_lowest`, hermetic, not wired); **27b** wires it into the live chunks
assembler + collect loop.

## Consequences
- During precip the displayed cadence matches RadarScope (~1.5–2.5 min); in clear-air
  (no SAILS) behavior is unchanged — one cut per volume, already at the radar's floor.
- The live assembler must fetch more of each volume's chunks (through the SAILS cut,
  ~60% of the volume) rather than stopping at the base cut (~8 chunks) — more S3 GETs
  (free/anonymous), bounded to one in-flight volume.
- SAILS frames are not recoverable from the archive: if live collection misses the
  window (server down), that mid-volume cut is gone — acceptable, since no assembled
  object encodes it at that timestamp anyway.
- Rendering correctness is enforced by value-based tests (selection + per-cut time vs a
  real captured layout and synthetic split-cut/SAILS layouts; base cut proven
  byte-identical) plus a required visual check against RadarScope on a live SAILS event
  (per CLAUDE.md — a rendering change does not merge on "it produced frames").

## Alternatives considered
- **Reflectivity-presence / gate-count to pick surveillance.** Rejected: both split-cut
  halves carry reflectivity in super-res; gate-count differences are real but fragile.
  "First of visit" is exact and matches the proven base selection.
- **One row per volume with multiple images.** Rejected: breaks the one-row-per-frame
  model the timeline/serve API depends on; distinct `scan_time`s give distinct frames
  for free.
- **Extract SAILS from assembled volumes too (full pipeline).** Deferred: larger, costs
  a full-volume decode per archived scan, and the live path already delivers the win
  where it matters (real-time freshness during storms).
- **Reconcile SAILS cuts by decoding the assembled volume's sub-sweeps.** Rejected:
  couples reconcile to full-volume decoding for no user-visible gain (the live PNG is
  already correct); `'live-sails'` + permanent is simpler.
