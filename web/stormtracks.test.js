"use strict";
// Unit tests for the storm-tracks GeoJSON builder. Run: `node --test web/stormtracks.test.js`.

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { trackFeatures } = require("./stormtracks.js");

const MOVING = {
  track_id: 12,
  lon: -104.5,
  lat: 39.8,
  max_dbz: 58.0,
  area_km2: 240.0,
  speed_kmh: 83.0,
  bearing_deg: 41.0,
  proj_lon: -104.0,
  proj_lat: 40.1,
};
const STATIONARY = {
  track_id: 13,
  lon: -103.9,
  lat: 39.6,
  max_dbz: 44.0,
  area_km2: 30.0,
  speed_kmh: 0.0,
  bearing_deg: null,
  proj_lon: null,
  proj_lat: null,
};

test("moving cell → a Point and a LineString, both [lon,lat] order", () => {
  const fc = trackFeatures([MOVING]);
  assert.equal(fc.type, "FeatureCollection");
  assert.equal(fc.features.length, 2);
  const pt = fc.features[0];
  const line = fc.features[1];
  assert.equal(pt.geometry.type, "Point");
  assert.deepEqual(pt.geometry.coordinates, [-104.5, 39.8]);
  assert.equal(line.geometry.type, "LineString");
  assert.deepEqual(line.geometry.coordinates, [
    [-104.5, 39.8],
    [-104.0, 40.1],
  ]);
});

test("cell properties carried onto the marker", () => {
  const pt = trackFeatures([MOVING]).features[0];
  assert.equal(pt.properties.track_id, 12);
  assert.equal(pt.properties.max_dbz, 58.0);
  assert.equal(pt.properties.speed_kmh, 83.0);
  assert.equal(pt.properties.bearing_deg, 41.0);
});

test("stationary cell → marker only, no vector", () => {
  const fc = trackFeatures([STATIONARY]);
  assert.equal(fc.features.length, 1);
  assert.equal(fc.features[0].geometry.type, "Point");
});

test("mixed list → one Point each + a LineString only for the mover", () => {
  const fc = trackFeatures([MOVING, STATIONARY]);
  const points = fc.features.filter((f) => f.geometry.type === "Point");
  const lines = fc.features.filter((f) => f.geometry.type === "LineString");
  assert.equal(points.length, 2);
  assert.equal(lines.length, 1);
  assert.equal(lines[0].properties.track_id, 12);
});

test("empty / missing input → empty collection", () => {
  assert.deepEqual(trackFeatures([]).features, []);
  assert.deepEqual(trackFeatures(undefined).features, []);
  assert.deepEqual(trackFeatures(null).features, []);
});
