"use strict";
// Unit tests for the backfill button/progress logic. Run: `node --test web/backfill.test.js`.

const { test } = require("node:test");
const assert = require("node:assert/strict");

const {
  progressText,
  progressPercent,
  isTerminal,
  pollDelayMs,
  BACKFILL_POLL_MS,
} = require("./backfill.js");

test("progressText speaks plain language per state", () => {
  assert.equal(progressText(null), "Starting…");
  assert.equal(progressText({ state: "queued" }), "Starting…");
  assert.equal(
    progressText({ state: "running", total: 0 }),
    "Looking for recent radar…", // total not known yet (still planning)
  );
  assert.equal(
    progressText({ state: "running", total: 48, fetched: 12 }),
    "Loading radar… 12 of 48 frames",
  );
  assert.equal(progressText({ state: "running", total: 48 }), "Loading radar… 0 of 48 frames");
  assert.equal(progressText({ state: "done", rendered: 1 }), "Loaded 1 frame.");
  assert.equal(progressText({ state: "done", rendered: 24 }), "Loaded 24 frames.");
  assert.equal(progressText({ state: "done", rendered: 0 }), "Loaded 0 frames.");
  assert.equal(
    progressText({ state: "failed" }),
    "Couldn't load history right now — try again.",
  );
});

test("progressPercent clamps and handles unknown total", () => {
  assert.equal(progressPercent(0, 0), 0); // total unknown
  assert.equal(progressPercent(5, 0), 0);
  assert.equal(progressPercent(12, 48), 25);
  assert.equal(progressPercent(48, 48), 100);
  assert.equal(progressPercent(60, 48), 100); // clamped
  assert.equal(progressPercent(-1, 48), 0); // clamped
});

test("isTerminal only for done/failed", () => {
  assert.equal(isTerminal("done"), true);
  assert.equal(isTerminal("failed"), true);
  assert.equal(isTerminal("running"), false);
  assert.equal(isTerminal("queued"), false);
});

test("pollDelayMs is the shared constant", () => {
  assert.equal(pollDelayMs(), BACKFILL_POLL_MS);
  assert.equal(pollDelayMs(), 2000);
});
