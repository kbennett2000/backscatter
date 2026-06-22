"use strict";
// Default-framing helper (Slice 24c). Pure — `node --test`'d, loaded as a plain <script>
// exposing globals, like prefs.js/theme.js. Frames the map on the active location's radar
// coverage instead of a hardcoded zoom, so the app opens on your area + nearby weather.

const DEFAULT_RANGE_KM = 150; // radius to frame around the active location

const KM_PER_DEG_LAT = 111; // ~111 km per degree of latitude (good enough for framing)

/**
 * Bounding box of a `rangeKm`-radius box centered on (lat, lon), as MapLibre fitBounds
 * input: `[[west, south], [east, north]]`. Longitude degrees widen toward the equator
 * (divided by cos(lat)), so the box stays ~`rangeKm` on the ground east–west.
 */
function coverageBounds(lat, lon, rangeKm = DEFAULT_RANGE_KM) {
  const dLat = rangeKm / KM_PER_DEG_LAT;
  const cos = Math.cos((lat * Math.PI) / 180);
  // Guard the poles (cos→0); clamp to a small floor so dLon stays finite.
  const dLon = rangeKm / (KM_PER_DEG_LAT * Math.max(Math.abs(cos), 1e-6));
  return [
    [lon - dLon, lat - dLat],
    [lon + dLon, lat + dLat],
  ];
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { coverageBounds, DEFAULT_RANGE_KM };
}
