"use strict";
// Retention form helpers (Slice 29b). Pure — `node --test`'d, loaded as a plain
// <script> exposing globals, like prefs.js/recolor.js. Builds the /api/retention PUT
// body from the two form inputs; a blank field means "that limit off" (matches the
// .env "unset = off" mental model). Server validation stays authoritative — this is a
// light client-side build + guard so obvious mistakes don't round-trip.

/** Parse one limit field: blank/whitespace → null (off); else a finite, >= 0 number. */
function parseLimit(str) {
  if (str === null || str === undefined) return null;
  const trimmed = String(str).trim();
  if (trimmed === "") return null;
  const n = Number(trimmed);
  if (!Number.isFinite(n)) throw new Error("must be a number");
  if (n < 0) throw new Error("must be 0 or more");
  return n;
}

/** Build the PUT body from the days + GB input strings (blank → null = off). */
function retentionBody(daysStr, gbStr) {
  return {
    max_age_days: parseLimit(daysStr),
    max_size_gb: parseLimit(gbStr),
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { parseLimit, retentionBody };
}
