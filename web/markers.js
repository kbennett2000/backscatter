"use strict";
// Location pins (Slice 17). Pure logic over the /api/locations list — no DOM, no fetch —
// so it unit-tests cleanly under `node --test` and loads as a plain <script> in the
// browser (exposing a global). app.js feeds the result to a MapLibre GeoJSON source.

/**
 * Build a GeoJSON FeatureCollection of pins, one per configured location.
 * @param {{name:string, lat:number, lon:number}[]} locations - from /api/locations.
 * @param {string|null} activeName - the currently-viewed location's name.
 * @returns {{type:"FeatureCollection", features:object[]}} one point feature per
 *   location, with `properties.active` true for the active one (drives styling).
 */
function locationFeatures(locations, activeName) {
  const list = Array.isArray(locations) ? locations : [];
  return {
    type: "FeatureCollection",
    features: list.map((loc) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [loc.lon, loc.lat] },
      properties: { name: loc.name, active: loc.name === activeName },
    })),
  };
}

// Browser: global. Node (`node --test`): module.exports. The guard is false in the
// browser (no `module`), so this file is a plain classic script there.
if (typeof module !== "undefined" && module.exports) {
  module.exports = { locationFeatures };
}
