"use strict";
// Unit tests for the responsive-layout logic. Run: `node --test web/layout.test.js`.

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { BREAKPOINT, isMobile, nextOpen } = require("./layout.js");

test("isMobile is true at/below the breakpoint, false above", () => {
  assert.equal(BREAKPOINT, 600);
  assert.equal(isMobile(360), true); // small phone
  assert.equal(isMobile(430), true); // large phone
  assert.equal(isMobile(600), true); // boundary is mobile
  assert.equal(isMobile(601), false);
  assert.equal(isMobile(1280), false); // desktop
});

test("nextOpen toggles", () => {
  assert.equal(nextOpen(false), true);
  assert.equal(nextOpen(true), false);
});
