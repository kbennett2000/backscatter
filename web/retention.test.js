"use strict";
// Unit tests for the retention form helpers. Run: `node --test web/retention.test.js`.

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { parseLimit, retentionBody } = require("./retention.js");

test("parseLimit: blank/whitespace/null → null (off)", () => {
  assert.equal(parseLimit(""), null);
  assert.equal(parseLimit("   "), null);
  assert.equal(parseLimit(null), null);
  assert.equal(parseLimit(undefined), null);
});

test("parseLimit: valid numbers pass (including 0)", () => {
  assert.equal(parseLimit("30"), 30);
  assert.equal(parseLimit("0"), 0); // server treats 0 days as off
  assert.equal(parseLimit("1.5"), 1.5);
  assert.equal(parseLimit(50), 50);
});

test("parseLimit: garbage and negatives throw", () => {
  assert.throws(() => parseLimit("abc"), /number/);
  assert.throws(() => parseLimit("-1"), /0 or more/);
});

test("retentionBody: builds both fields, blanks → null", () => {
  assert.deepEqual(retentionBody("30", "50"), {
    max_age_days: 30,
    max_size_gb: 50,
  });
  assert.deepEqual(retentionBody("", ""), {
    max_age_days: null,
    max_size_gb: null,
  });
  assert.deepEqual(retentionBody("7", ""), {
    max_age_days: 7,
    max_size_gb: null,
  });
});

test("retentionBody: propagates a bad field as a throw", () => {
  assert.throws(() => retentionBody("-5", ""), /0 or more/);
});
