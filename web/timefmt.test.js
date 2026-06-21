"use strict";
// Unit tests for time conversion. Run: `node --test web/timefmt.test.js`.
// These are timezone-independent (they assert round-trip + instant-equality through Date),
// so they prove the local↔UTC offset is handled correctly whatever TZ the runner uses.

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { isoToLocalInput, localInputToIso } = require("./timefmt.js");

test("round-trip: UTC instant → local input → UTC is lossless to the minute", () => {
  for (const z of [
    "2026-06-21T19:00:00Z",
    "2026-01-01T00:00:00Z", // tz boundary / DST off
    "2026-07-04T23:59:00Z",
  ]) {
    assert.equal(localInputToIso(isoToLocalInput(z)), z);
  }
});

test("a picked local time maps to the correct UTC instant", () => {
  // Whatever the runner's tz, the entered wall-clock and the produced UTC are the same
  // instant — this is the property the API round-trip depends on.
  const value = "2026-06-21T13:30";
  const iso = localInputToIso(value);
  assert.match(iso, /Z$/);
  assert.equal(Date.parse(iso), new Date(value).getTime());
  // and it inverts back to the same input
  assert.equal(isoToLocalInput(iso), value);
});

test("localInputToIso returns null for empty (the 'no bound' case)", () => {
  assert.equal(localInputToIso(""), null);
  assert.equal(localInputToIso(null), null);
});
