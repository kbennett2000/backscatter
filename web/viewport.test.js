"use strict";
// Unit tests for default-framing bounds. Run: `node --test web/viewport.test.js`.

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { coverageBounds, DEFAULT_RANGE_KM } = require("./viewport.js");

test("coverageBounds frames a range-km box centered on the location", () => {
  const [[w, s], [e, n]] = coverageBounds(40, -105, 150);
  const dLat = 150 / 111;
  assert.ok(Math.abs(n - (40 + dLat)) < 1e-9);
  assert.ok(Math.abs(s - (40 - dLat)) < 1e-9);
  // east/west symmetric about the center longitude
  assert.ok(Math.abs((e + w) / 2 - -105) < 1e-9);
  // at 40°N the lon span is wider than the lat span (divided by cos 40° ≈ 0.766)
  const dLon = (e - w) / 2;
  assert.ok(dLon > dLat);
  assert.ok(Math.abs(dLon - dLat / Math.cos((40 * Math.PI) / 180)) < 1e-9);
});

test("lower latitude widens the longitude span (cos larger near equator)", () => {
  const lonSpan = (lat) => {
    const [[w], [e]] = coverageBounds(lat, 0, 150);
    return e - w;
  };
  assert.ok(lonSpan(10) < lonSpan(50)); // higher lat → narrower cos → wider lon span
});

test("defaults to DEFAULT_RANGE_KM when range omitted", () => {
  const a = coverageBounds(40, -105);
  const b = coverageBounds(40, -105, DEFAULT_RANGE_KM);
  assert.deepEqual(a, b);
});
