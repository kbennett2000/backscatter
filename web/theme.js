"use strict";
// Basemap + chrome-theme logic (Slice 21, generalized in Slice 23). Pure — no DOM, no
// map — so it unit-tests under `node --test` and loads as a plain <script> (exposing
// globals). app.js does the DOM/map wiring (data-theme, setStyle, persistence). The radar
// dBZ palette is NOT themed — it's baked into the PNGs — so nothing here touches radar
// colors; only the keyless basemap style swaps, and the chrome (light/dark) follows it.

// Every style is keyless OpenFreeMap (no API key / no credit card, same attribution).
// `chrome` is the UI theme that reads well over that map. There is NO keyless satellite
// or terrain style (those need a keyed provider), so we deliberately don't offer them.
const STYLES = {
  liberty: { url: "https://tiles.openfreemap.org/styles/liberty", chrome: "light", label: "Liberty (light)" },
  bright: { url: "https://tiles.openfreemap.org/styles/bright", chrome: "light", label: "Bright" },
  positron: { url: "https://tiles.openfreemap.org/styles/positron", chrome: "light", label: "Positron (minimal)" },
  dark: { url: "https://tiles.openfreemap.org/styles/dark", chrome: "dark", label: "Dark" },
  fiord: { url: "https://tiles.openfreemap.org/styles/fiord", chrome: "dark", label: "Fiord (dark blue)" },
};
const DEFAULT_LIGHT = "liberty";
const DEFAULT_DARK = "dark";

function isValidBasemap(key) {
  return Object.prototype.hasOwnProperty.call(STYLES, key);
}

/** Migrate the Slice-21 theme pref ("light"/"dark") to a style key. */
function migrateBasemap(stored) {
  if (stored === "light") return DEFAULT_LIGHT;
  if (stored === "dark") return DEFAULT_DARK;
  return stored;
}

/**
 * The basemap to use on load: a stored choice (migrated from the old light/dark pref)
 * wins; otherwise follow the OS (prefers-color-scheme), defaulting to the light style.
 */
function resolveInitialBasemap(stored, prefersDark) {
  const migrated = migrateBasemap(stored);
  if (isValidBasemap(migrated)) return migrated;
  return prefersDark ? DEFAULT_DARK : DEFAULT_LIGHT;
}

/** The UI chrome theme ("light"/"dark") that goes with a basemap (unknown → light). */
function chromeFor(key) {
  return (STYLES[key] || STYLES[DEFAULT_LIGHT]).chrome;
}

/** The keyless style URL for a basemap key (unknown → the light default). */
function basemapUrl(key) {
  return (STYLES[key] || STYLES[DEFAULT_LIGHT]).url;
}

/** The ☀/☾ quick toggle: flip to the canonical light or dark style. */
function nextChromeToggle(currentKey) {
  return chromeFor(currentKey) === "dark" ? DEFAULT_LIGHT : DEFAULT_DARK;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    STYLES,
    DEFAULT_LIGHT,
    DEFAULT_DARK,
    isValidBasemap,
    migrateBasemap,
    resolveInitialBasemap,
    chromeFor,
    basemapUrl,
    nextChromeToggle,
  };
}
