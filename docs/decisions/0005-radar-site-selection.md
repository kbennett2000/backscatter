# 5. Nearest single radar for max resolution; ranked list for failover; no blending

## Status
Accepted

## Context
The tool must work for any CONUS location and show the **highest-resolution** data
for it. Level 2 super-resolution (0.5° × 250 m) is the resolution ceiling, and a
radar's data quality at a point degrades with distance: earth curvature puts the
beam progressively higher above ground farther out, so a closer radar sees nearer
the surface and resolves low-level detail better.

The question raised: should we combine multiple nearby radars — e.g. a
proximity-weighted blend of every station that covers the location?

## Decision
- Derive the active radar from a configured lat/lon against a **bundled static
  NEXRAD site table** (~160 sites, ICAO + lat/lon — fixed public data, no API).
- Use the **single nearest covering radar** for rendering. That gives the best
  data for the location.
- Compute and keep the **full ranked list** by distance, but use it only for
  **failover** — if the nearest site has no recent data (maintenance/outage), drop
  to the next.
- **Do not blend multiple radars** into a single rendered frame.
- If wide-area context is ever wanted, use **MRMS** (the national mosaic) at low
  zoom rather than rolling our own multi-radar composite. (Tracked in ROADMAP
  "Later".)

## Consequences
- Highest available resolution at any location, with no compositing artifacts.
- Resilience to single-radar outages via the ranked list, cheaply.
- Works anywhere in CONUS from config alone.
- Wide-area views (when we add them) come from a purpose-built, already-reconciled
  product instead of a homegrown blend.

## Alternatives considered
- **Proximity-weighted multi-radar blend.** Rejected: would most likely *reduce*
  quality at a point — beam-height mismatches, time skew between non-synchronized
  scans, and seams — and it destroys the crisp native polar geometry we render
  from. Correct multi-radar reconciliation is exactly the hard problem NOAA
  already solved and distributes for free as MRMS; re-implementing it badly is
  strictly worse than using it.
