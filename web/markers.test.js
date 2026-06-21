"use strict";
// Unit tests for the location-pin GeoJSON builder. Run: `node --test web/markers.test.js`.

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { locationFeatures } = require("./markers.js");

const LOCS = [
  { name: "Home", lat: 39.3603, lon: -104.5969, default: true, site: "KFTG" },
  { name: "OKC", lat: 35.4676, lon: -97.5164, default: false, site: "KTLX" },
  { name: "Parker", lat: 39.5186, lon: -104.7614, default: false, site: "KFTG" },
];

test("one feature per location, coords as [lon, lat]", () => {
  const fc = locationFeatures(LOCS, "Home");
  assert.equal(fc.type, "FeatureCollection");
  assert.equal(fc.features.length, 3);
  assert.deepEqual(fc.features[0].geometry.coordinates, [-104.5969, 39.3603]);
  assert.equal(fc.features[1].geometry.type, "Point");
});

test("names preserved", () => {
  const names = locationFeatures(LOCS, "Home").features.map((f) => f.properties.name);
  assert.deepEqual(names, ["Home", "OKC", "Parker"]);
});

test("exactly the active location is flagged", () => {
  const fc = locationFeatures(LOCS, "OKC");
  const active = fc.features.filter((f) => f.properties.active).map((f) => f.properties.name);
  assert.deepEqual(active, ["OKC"]);
});

test("active name not in the list → none active (no crash)", () => {
  const fc = locationFeatures(LOCS, "Nowhere");
  assert.equal(fc.features.filter((f) => f.properties.active).length, 0);
  assert.equal(fc.features.length, 3);
});

test("single location", () => {
  const fc = locationFeatures([LOCS[0]], "Home");
  assert.equal(fc.features.length, 1);
  assert.equal(fc.features[0].properties.active, true);
});

test("empty / missing input → empty collection", () => {
  assert.deepEqual(locationFeatures([], "Home").features, []);
  assert.deepEqual(locationFeatures(undefined, "Home").features, []);
  assert.deepEqual(locationFeatures(null, null).features, []);
});
