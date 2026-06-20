# 2. Render on the backend (Py-ART → tiles), defer client-side WebGL

## Status
Accepted

## Context
A Level 2 volume is polar data — reflectivity values indexed by range gate and
azimuth, per elevation tilt. Getting it onto a web map means projecting that polar
geometry to map coordinates and mapping values through a color table. Two broad
approaches:

- **Backend render:** read with Py-ART, reproject, rasterize to PNG/tiles on the
  server; the browser just displays images on a map.
- **Client-side render:** ship parsed radials to the browser and draw them in
  WebGL (the approach AtticRadar/QuadWeather take — gives the smooth radial-sweep
  look and interactivity).

Our headline feature is a playback archive: render each frame once, store it,
replay cheaply.

## Decision
v1 renders **on the backend**: Py-ART reads the volume, we extract lowest-tilt
super-res reflectivity, reproject to web mercator, and produce georeferenced
raster tiles/overlays. The frontend (MapLibre) overlays them. Client-side WebGL
rendering is **deferred**.

## Consequences
- Plays to a FastAPI/Python strength and gets us to a visible map fastest.
- Playback is trivial: cached frames are just images cycled on the map.
- Raw volumes are kept (ADR-0003), so we can re-render everything when color
  tables or products change without re-downloading.
- We give up, for now, the crisp interactive radial-sweep look and smooth zoom of
  native polar rendering. That's the planned WebGL upgrade later.

## Alternatives considered
- **Client-side WebGL first.** Rejected for v1: substantially harder (porting
  parsing + dealiasing to JS, polar-to-screen in shaders) and it doesn't advance
  the archive/playback goal. It's the right *later* move for fidelity.
