"use strict";
// Persisted display-preference helpers (Slice 23). Pure — `node --test`'d, loaded as a
// plain <script> exposing globals, like theme.js/layout.js.

const DEFAULT_OPACITY = 0.8; // radar raster-opacity default (matches the original)
const MIN_OPACITY = 0.1;

/** Clamp a stored/slider value to [0.1, 1]; null/empty/garbage → the default. */
function clampOpacity(v) {
  if (v === null || v === undefined || v === "") return DEFAULT_OPACITY;
  const n = Number(v);
  if (!Number.isFinite(n)) return DEFAULT_OPACITY;
  return Math.max(MIN_OPACITY, Math.min(1, n));
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { clampOpacity, DEFAULT_OPACITY, MIN_OPACITY };
}
