"use strict";
// Storm-cell tracks overlay (Slice 28c + 28d ticks). Pure logic over the /api/cells
// track list — no DOM, no fetch — so it unit-tests cleanly under `node --test` and loads
// as a plain <script> in the browser (exposing a global). app.js feeds the result to a
// MapLibre GeoJSON source styled as cell markers + estimated-motion vectors.
//
// 28d draws the vector RadarScope-style: perpendicular cross-line ticks at fixed TIME
// intervals (so the track shows WHERE the cell will be at each forecast time — faster
// storm → ticks farther apart) plus an arrowhead at the tip. Geometry is recomputed here
// from the cell's speed+heading (28b's motion, already in /api/cells); no backend change.
//
// Tracking is ESTIMATION, not a provably-correct render: estimated cell positions/motion,
// framed in-UI as such (not a nowcast, not for life safety). Ticks assume the current
// velocity holds (steady-motion projection).

// --- tunable overlay constants (ground distances in metres) -----------------
const TICK_INTERVAL_MIN = 15; // a tick every 15 min along the track
const TICK_COUNT = 4; // 4 ticks → 15/30/45/60 min; the 60-min point is the tip
const TICK_HALF_LEN_M = 3500; // each cross-line is ~7 km wide
const ARROW_LEN_M = 6000; // arrowhead barb length
const ARROW_ANGLE_DEG = 28; // barb half-angle off the reversed heading
const _M_PER_DEG_LAT = 111320; // metres per degree latitude (sphere approx)

/**
 * Small-distance equirectangular forward step: advance (lon,lat) by `distM` along
 * `bearingDeg` (degrees clockwise from north). Accurate to well under a km at the
 * ≤100 km scale of these tracks — negligible against the steady-velocity assumption.
 * @returns {[number, number]} [lon, lat]
 */
function forward(lon, lat, bearingDeg, distM) {
  const br = (bearingDeg * Math.PI) / 180;
  const dEast = distM * Math.sin(br);
  const dNorth = distM * Math.cos(br);
  const dLat = dNorth / _M_PER_DEG_LAT;
  const dLon = dEast / (_M_PER_DEG_LAT * Math.cos((lat * Math.PI) / 180));
  return [lon + dLon, lat + dLat];
}

function _line(coords, props) {
  return { type: "Feature", geometry: { type: "LineString", coordinates: coords }, properties: props };
}

/**
 * Build a GeoJSON FeatureCollection for the storm-tracks overlay.
 * @param {{track_id:number, lon:number, lat:number, max_dbz:number,
 *   speed_kmh:number, bearing_deg:(number|null)}[]} tracks - from /api/cells.
 * @returns {{type:"FeatureCollection", features:object[]}} per moving cell: a Point
 *   marker, the main vector (cell → 60-min point), one perpendicular tick cross-line
 *   per interval, and an arrowhead at the tip. A stationary cell (no bearing) is just
 *   the Point. Coordinates are [lon, lat] (GeoJSON order).
 */
function trackFeatures(tracks) {
  const list = Array.isArray(tracks) ? tracks : [];
  const features = [];
  for (const t of list) {
    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [t.lon, t.lat] },
      properties: {
        track_id: t.track_id,
        max_dbz: t.max_dbz,
        speed_kmh: t.speed_kmh,
        bearing_deg: t.bearing_deg,
      },
    });
    // A cell is moving iff the server gave it a heading (it nulls bearing for
    // near-stationary cells). No heading → marker only, no vector/ticks.
    if (t.bearing_deg == null || !(t.speed_kmh > 0)) continue;

    const bearing = t.bearing_deg;
    const speedMs = t.speed_kmh / 3.6;
    const props = { track_id: t.track_id, speed_kmh: t.speed_kmh };

    // Main vector: cell → the last tick (TICK_COUNT × interval) ahead.
    const horizonM = speedMs * TICK_COUNT * TICK_INTERVAL_MIN * 60;
    const tip = forward(t.lon, t.lat, bearing, horizonM);
    features.push(_line([[t.lon, t.lat], tip], props));

    // Perpendicular time ticks at each interval along the heading.
    for (let i = 1; i <= TICK_COUNT; i++) {
      const distM = speedMs * i * TICK_INTERVAL_MIN * 60;
      const [clon, clat] = forward(t.lon, t.lat, bearing, distM);
      const left = forward(clon, clat, bearing + 90, TICK_HALF_LEN_M);
      const right = forward(clon, clat, bearing - 90, TICK_HALF_LEN_M);
      features.push(_line([left, right], { ...props, tick_min: i * TICK_INTERVAL_MIN }));
    }

    // Arrowhead at the tip: two barbs back along the reversed heading.
    const barbL = forward(tip[0], tip[1], bearing + 180 - ARROW_ANGLE_DEG, ARROW_LEN_M);
    const barbR = forward(tip[0], tip[1], bearing + 180 + ARROW_ANGLE_DEG, ARROW_LEN_M);
    features.push(_line([barbL, tip, barbR], props));
  }
  return { type: "FeatureCollection", features };
}

// Browser: global. Node (`node --test`): module.exports. The guard is false in the
// browser (no `module`), so this file is a plain classic script there.
if (typeof module !== "undefined" && module.exports) {
  module.exports = { trackFeatures, forward };
}
