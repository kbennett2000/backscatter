# 7. Single-frame render: geometry, projection, color, and sidecar

## Status
Accepted

## Context
Slice 3 turns a stored Level 2 volume into one georeferenced PNG. Radar data is
polar (reflectivity by range gate × azimuth, per tilt); getting it onto a web map
means placing each gate on the globe, reprojecting to the map CRS, and mapping dBZ
through a color table. Per CLAUDE.md this is the highest-risk area: a flipped axis,
off-by-one gate, wrong azimuth convention, or bad color mapping yields an image
that looks plausible but is wrong. Decisions here are load-bearing for Slice 4
(placing the image) and every rendered frame thereafter.

## Decision
- **Lowest-tilt reflectivity, native super-res.** Decode with Py-ART
  (`read_nexrad_archive`); take the first sweep at the minimum elevation (the 0.5°
  reflectivity surveillance cut), 0.5° az × 250 m gates, no resampling.
- **Radar origin from the bundled site table** (ADR-0005), not the file's metadata,
  so placement is consistent with site selection. (They agree in practice.)
- **Gate placement by geodesic.** Slant range → ground range via the standard
  4/3-earth beam model (Doviak & Zrnić), then `pyproj.Geod` (WGS84) forward from the
  site. Azimuth is **0° = north, increasing clockwise** — the geodesic convention is
  the radar convention. Cross-checked against `pyart.core.antenna_to_cartesian`.
- **Project to Web Mercator (EPSG:3857)** via pyproj.
- **Inverse-mapping rasterization.** For each output pixel, map back
  (mercator → lon/lat → azimuth/ground-range from the site) and sample the nearest
  gate. Exact, no scatter-gridding seams, and directly testable. Output **row 0 is
  north**. Azimuth lookup handles the unsorted rays (a sweep starts mid-rotation).
- **Default extent 230 km** (reuses `COVERAGE_RANGE_KM`); raw data keeps full range.
- **NWS reflectivity color table**, discrete 5-dBZ steps; sub-threshold and
  masked/NaN gates are transparent. Discrete so each breakpoint's RGBA is exact.
- **Output = PNG + JSON sidecar.** The sidecar carries explicit bounds in **both**
  EPSG:3857 and WGS84, plus size, site, scan time, elevation, and max range, so the
  map layer needs no guessing.

## Consequences
- Geometry and color live in small, isolated, value-tested modules
  (`render/geometry.py`, `render/raster.py`, `render/colormap.py`); a synthetic
  single-gate test catches axis flips (north→top, east→right).
- Re-rendering on a palette/extent change is cheap (raw volumes retained, ADR-0003).
- Inverse mapping costs one geodesic-inverse per pixel (~a few M points), vectorized
  through pyproj — fast enough for single frames; revisit if it bottlenecks Slice 5.
- Green tests are necessary but not sufficient: each rendering change also gets a
  visual check against a reference (RadarScope / NWS) for the same timestamp.

## Alternatives considered
- **Forward scatter-gridding** (project gate centers, splat to raster). Rejected:
  seams/holes between radials and harder to test exactly; inverse mapping is cleaner.
- **Reproject with Py-ART/`pyart.map` gridding.** Rejected for v1: heavier and aimed
  at Cartesian/multi-radar gridding; we want a thin, testable single-sweep path.
- **World file (`.pgw`) only.** Rejected as the primary sidecar: a Mercator world
  file doesn't give the lon/lat corners a MapLibre image source wants. JSON with both
  CRSs is unambiguous; a world file can be added later if GIS tooling needs it.
