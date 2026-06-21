"use strict";
// Unit tests for the basemap/chrome logic. Run: `node --test web/theme.test.js`.

const { test } = require("node:test");
const assert = require("node:assert/strict");

const {
  STYLES,
  isValidBasemap,
  migrateBasemap,
  resolveInitialBasemap,
  chromeFor,
  basemapUrl,
  nextChromeToggle,
} = require("./theme.js");

test("STYLES are all keyless OpenFreeMap with a chrome tag", () => {
  for (const [key, s] of Object.entries(STYLES)) {
    assert.match(s.url, /^https:\/\/tiles\.openfreemap\.org\/styles\//, key);
    assert.ok(s.url.indexOf("?") === -1 && s.url.indexOf("key=") === -1, `${key} keyless`);
    assert.ok(s.chrome === "light" || s.chrome === "dark", `${key} chrome`);
    assert.ok(s.label, `${key} label`);
  }
  assert.deepEqual(Object.keys(STYLES), ["liberty", "bright", "positron", "dark", "fiord"]);
});

test("isValidBasemap accepts only known keys", () => {
  assert.equal(isValidBasemap("liberty"), true);
  assert.equal(isValidBasemap("fiord"), true);
  assert.equal(isValidBasemap("satellite"), false); // not offered (needs a key)
  assert.equal(isValidBasemap(null), false);
});

test("migrateBasemap maps the old light/dark theme pref to style keys", () => {
  assert.equal(migrateBasemap("light"), "liberty");
  assert.equal(migrateBasemap("dark"), "dark");
  assert.equal(migrateBasemap("positron"), "positron"); // already a key → unchanged
});

test("resolveInitialBasemap: stored (incl. migrated) wins; else OS default", () => {
  assert.equal(resolveInitialBasemap("fiord", false), "fiord");
  assert.equal(resolveInitialBasemap("light", true), "liberty"); // migrated old pref wins
  assert.equal(resolveInitialBasemap(null, true), "dark"); // OS dark → dark style
  assert.equal(resolveInitialBasemap(null, false), "liberty"); // OS light → liberty
  assert.equal(resolveInitialBasemap("garbage", false), "liberty");
});

test("chromeFor derives the UI theme from the style", () => {
  assert.equal(chromeFor("liberty"), "light");
  assert.equal(chromeFor("bright"), "light");
  assert.equal(chromeFor("positron"), "light");
  assert.equal(chromeFor("dark"), "dark");
  assert.equal(chromeFor("fiord"), "dark");
  assert.equal(chromeFor("nonsense"), "light"); // unknown → light
});

test("basemapUrl + nextChromeToggle (the ☀/☾ shortcut)", () => {
  assert.equal(basemapUrl("dark"), STYLES.dark.url);
  assert.equal(basemapUrl("nonsense"), STYLES.liberty.url);
  assert.equal(nextChromeToggle("liberty"), "dark"); // light style → dark
  assert.equal(nextChromeToggle("bright"), "dark"); // any light style → dark
  assert.equal(nextChromeToggle("fiord"), "liberty"); // any dark style → light
});
