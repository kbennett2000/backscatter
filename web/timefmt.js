"use strict";
// Time display/entry helpers (Slice 23). Pure — loads as a plain <script> (exposing
// globals) and unit-tests under `node --test`, like gaps.js/theme.js. Everything the
// USER sees or enters is LOCAL; storage/queries stay UTC. "Local" means the browser's
// timezone, which is the active location's local time for the typical self-hosted user
// who's in that zone (true per-location tz would need a lat/lon→tz dataset — out of scope).

/** A UTC instant (ISO with Z/offset) → local wall-clock "YYYY-MM-DDTHH:mm" for a
 *  <input type="datetime-local">. */
function isoToLocalInput(iso) {
  const d = new Date(iso);
  const p = (n) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}` +
    `T${p(d.getHours())}:${p(d.getMinutes())}`
  );
}

/** A datetime-local value (local wall-clock) → UTC ISO string with a Z suffix. The API
 *  accepts the Z form (storage/queries stay UTC). Returns null for an empty value. */
function localInputToIso(value) {
  if (!value) return null;
  // `new Date("YYYY-MM-DDTHH:mm")` parses as LOCAL time; toISOString() yields UTC.
  return new Date(value).toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** "12:56 PM" in local time. */
function fmtLocalTime(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/** "Jun 21, 12:56 PM" in local time. */
function fmtLocalDateTime(iso) {
  return new Date(iso).toLocaleString([], {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

/**
 * Plain-language age of an instant relative to `now` (epoch ms): "just now",
 * "N min ago", "N hr ago", "N d ago". Floors each unit, so it never overstates
 * freshness (a 6½-min-old frame reads "6 min ago", never "7"). A future/clock-skewed
 * instant reads "just now".
 */
function relativeAge(iso, now) {
  const diff = now - Date.parse(iso);
  if (diff < 60000) return "just now"; // < 1 min (and any negative skew)
  if (diff < 3600000) return `${Math.floor(diff / 60000)} min ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} hr ago`;
  return `${Math.floor(diff / 86400000)} d ago`;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    isoToLocalInput,
    localInputToIso,
    fmtLocalTime,
    fmtLocalDateTime,
    relativeAge,
  };
}
