"use strict";
// Unit tests for display prefs. Run: `node --test web/prefs.test.js`.

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { clampOpacity, DEFAULT_OPACITY } = require("./prefs.js");

test("clampOpacity clamps to [0.1, 1] and defaults on bad input", () => {
  assert.equal(clampOpacity("0.4"), 0.4);
  assert.equal(clampOpacity(0.9), 0.9);
  assert.equal(clampOpacity(0), 0.1); // below min → clamped up
  assert.equal(clampOpacity(2), 1); // above max → clamped down
  assert.equal(clampOpacity(null), DEFAULT_OPACITY); // missing pref → default
  assert.equal(clampOpacity(""), DEFAULT_OPACITY);
  assert.equal(clampOpacity("abc"), DEFAULT_OPACITY); // garbage → default
  assert.equal(DEFAULT_OPACITY, 0.8);
});
