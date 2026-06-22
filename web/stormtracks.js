"use strict";
// Storm-cell tracks overlay (Slice 28c). Pure logic over the /api/cells track list —
// no DOM, no fetch — so it unit-tests cleanly under `node --test` and loads as a plain
// <script> in the browser (exposing a global). app.js feeds the result to a MapLibre
// GeoJSON source styled as cell markers + estimated-motion vectors.
//
// Tracking is ESTIMATION, not a provably-correct render: these are estimated cell
// positions and motion, framed in-UI as such (not a nowcast, not for life safety).

/**
 * Build a GeoJSON FeatureCollection for the storm-tracks overlay.
 * @param {{track_id:number, lon:number, lat:number, max_dbz:number,
 *   speed_kmh:number, bearing_deg:(number|null),
 *   proj_lon:(number|null), proj_lat:(number|null)}[]} tracks - from /api/cells.
 * @returns {{type:"FeatureCollection", features:object[]}} one Point per cell, plus
 *   one LineString (cell → projected position) for each cell that is moving (i.e. has
 *   a non-null projected endpoint). Coordinates are [lon, lat] (GeoJSON order).
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
    if (t.proj_lon != null && t.proj_lat != null) {
      features.push({
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates: [
            [t.lon, t.lat],
            [t.proj_lon, t.proj_lat],
          ],
        },
        properties: { track_id: t.track_id, speed_kmh: t.speed_kmh },
      });
    }
  }
  return { type: "FeatureCollection", features };
}

// Browser: global. Node (`node --test`): module.exports. The guard is false in the
// browser (no `module`), so this file is a plain classic script there.
if (typeof module !== "undefined" && module.exports) {
  module.exports = { trackFeatures };
}
