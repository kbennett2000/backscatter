"use strict";
// Unit tests for the first-run / view-state logic. Run: `node --test web/firstrun.test.js`.

const { test } = require("node:test");
const assert = require("node:assert/strict");

const {
  chooseView,
  shouldPoll,
  hasNewerFrame,
  statusText,
  FRESH_MS,
} = require("./firstrun.js");

test("chooseView distinguishes the three states", () => {
  assert.equal(chooseView(33, 12), "has-data"); // loaded frames → show radar
  assert.equal(chooseView(33, 0), "wrong-window"); // archive has data, window empty
  assert.equal(chooseView(0, 0), "empty"); // truly empty archive
  assert.equal(chooseView(0, 5), "has-data"); // loaded wins even if extent stale at 0
});

test("shouldPoll: empty or latest-view while visible", () => {
  assert.equal(shouldPoll("empty", false, false), true);
  assert.equal(shouldPoll("empty", false, true), false); // tab hidden
  assert.equal(shouldPoll("has-data", false, false), true); // latest view
  assert.equal(shouldPoll("has-data", true, false), false); // explicit window → stop
  assert.equal(shouldPoll("has-data", false, true), false); // hidden
  assert.equal(shouldPoll("wrong-window", true, false), false); // viewing a past window
});

test("hasNewerFrame: strictly newer newest-frame", () => {
  assert.equal(hasNewerFrame(null, "2026-06-21T12:00:00Z"), true); // first frame
  assert.equal(hasNewerFrame("2026-06-21T12:00:00Z", "2026-06-21T12:05:00Z"), true);
  assert.equal(hasNewerFrame("2026-06-21T12:05:00Z", "2026-06-21T12:05:00Z"), false);
  assert.equal(hasNewerFrame("2026-06-21T12:05:00Z", "2026-06-21T12:00:00Z"), false);
  assert.equal(hasNewerFrame("2026-06-21T12:00:00Z", null), false);
});

test("statusText is honest about freshness", () => {
  const fmt = () => "12:56 PM";
  const max = "2026-06-21T18:56:00Z";
  const t = Date.parse(max);

  assert.equal(statusText(null, t, fmt), "Collecting — waiting for the first frame…");
  assert.equal(
    statusText(max, t + 5 * 60 * 1000, fmt),
    "Collecting · last frame 12:56 PM", // 5 min old → fresh
  );
  assert.equal(statusText(max, t + FRESH_MS, fmt), "Collecting · last frame 12:56 PM");
  assert.equal(
    statusText(max, t + 20 * 60 * 1000, fmt),
    "Last frame 12:56 PM · checking for new radar…", // 20 min old → stale
  );
});
