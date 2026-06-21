"use strict";
// Unit tests for the theme logic. Run: `node --test web/theme.test.js`.

const { test } = require("node:test");
const assert = require("node:assert/strict");

const {
  isValidTheme,
  resolveInitialTheme,
  nextTheme,
  basemapFor,
  BASEMAPS,
} = require("./theme.js");

test("isValidTheme accepts only light/dark", () => {
  assert.equal(isValidTheme("light"), true);
  assert.equal(isValidTheme("dark"), true);
  assert.equal(isValidTheme("blue"), false);
  assert.equal(isValidTheme(null), false);
  assert.equal(isValidTheme(undefined), false);
});

test("resolveInitialTheme: stored choice wins over the OS", () => {
  assert.equal(resolveInitialTheme("dark", false), "dark");
  assert.equal(resolveInitialTheme("light", true), "light");
});

test("resolveInitialTheme: no stored choice follows prefers-color-scheme, default light", () => {
  assert.equal(resolveInitialTheme(null, true), "dark");
  assert.equal(resolveInitialTheme(null, false), "light");
  assert.equal(resolveInitialTheme("garbage", true), "dark"); // invalid → fall through
  assert.equal(resolveInitialTheme("", false), "light");
});

test("nextTheme toggles", () => {
  assert.equal(nextTheme("light"), "dark");
  assert.equal(nextTheme("dark"), "light");
});

test("basemapFor returns the keyless OpenFreeMap URLs", () => {
  assert.equal(basemapFor("light"), BASEMAPS.light);
  assert.equal(basemapFor("dark"), BASEMAPS.dark);
  assert.equal(basemapFor("nonsense"), BASEMAPS.light); // unknown → light
  assert.match(basemapFor("light"), /tiles\.openfreemap\.org\/styles\/liberty/);
  assert.match(basemapFor("dark"), /tiles\.openfreemap\.org\/styles\/dark/);
});
