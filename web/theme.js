"use strict";
// Light/dark theme logic (Slice 21). Pure — no DOM, no map — so it unit-tests under
// `node --test` and loads as a plain <script> (exposing globals), like gaps.js/firstrun.js.
// app.js does the DOM/map wiring (the data-theme attribute, the basemap setStyle, and
// localStorage persistence). The radar dBZ palette is NOT themed — it's baked into the
// PNGs — so nothing here touches radar colors; only the keyless basemap style swaps.

const THEMES = ["light", "dark"];

// Both styles are keyless OpenFreeMap (no API key / no credit card), same attribution.
const BASEMAPS = {
  light: "https://tiles.openfreemap.org/styles/liberty",
  dark: "https://tiles.openfreemap.org/styles/dark",
};

function isValidTheme(theme) {
  return theme === "light" || theme === "dark";
}

/**
 * The theme to use on load: an explicit stored choice wins; otherwise follow the OS
 * (prefers-color-scheme), defaulting to light when no preference is expressed.
 */
function resolveInitialTheme(stored, prefersDark) {
  if (isValidTheme(stored)) return stored;
  return prefersDark ? "dark" : "light";
}

function nextTheme(current) {
  return current === "dark" ? "light" : "dark";
}

/** The keyless basemap style URL for a theme (unknown → light). */
function basemapFor(theme) {
  return BASEMAPS[isValidTheme(theme) ? theme : "light"];
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    THEMES,
    BASEMAPS,
    isValidTheme,
    resolveInitialTheme,
    nextTheme,
    basemapFor,
  };
}
