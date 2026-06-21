"use strict";
// Unit tests for the first-run / view-state logic. Run: `node --test web/firstrun.test.js`.

const { test } = require("node:test");
const assert = require("node:assert/strict");

const {
  chooseView,
  shouldPoll,
  hasNewerFrame,
  shouldAutoAdvance,
  isLiveView,
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

test("shouldAutoAdvance: only when on latest AND parked on the newest frame", () => {
  assert.equal(shouldAutoAdvance(true, true), true); // latest + on last → jump to new frame
  assert.equal(shouldAutoAdvance(true, false), false); // scrubbed back → keep position
  assert.equal(shouldAutoAdvance(false, true), false); // viewing a history window
  assert.equal(shouldAutoAdvance(false, false), false);
});

test("isLiveView: tracking newest = has-data, no explicit window, on last frame", () => {
  assert.equal(isLiveView("has-data", false, true), true);
  assert.equal(isLiveView("has-data", false, false), false); // scrubbed back
  assert.equal(isLiveView("has-data", true, true), false); // explicit history window
  assert.equal(isLiveView("empty", false, true), false);
});

test("statusText shows honest age + a Live badge only when tracking newest", () => {
  const age = () => "6 min ago";
  const max = "2026-06-21T18:56:00Z";
  const t = Date.parse(max);

  assert.equal(
    statusText(null, t, true, age),
    "Collecting — waiting for the first frame…",
  );
  // fresh + live → Live badge
  assert.equal(statusText(max, t + 6 * 60000, true, age), "● Live · last frame 6 min ago");
  // fresh but scrubbed back → no badge, just the age
  assert.equal(statusText(max, t + 6 * 60000, false, age), "Last frame 6 min ago");
  assert.equal(statusText(max, t + FRESH_MS, true, age), "● Live · last frame 6 min ago");
  // stale (> 15 min) → never claims Live
  assert.equal(
    statusText(max, t + 20 * 60000, true, age),
    "Last frame 6 min ago · checking for new radar…",
  );
});
