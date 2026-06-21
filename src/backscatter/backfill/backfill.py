"""Backfill assembled Level 2 volumes for a site over a past date range.

Same per-volume pipeline as the collect loop — dedupe → download → render → index —
but driven over a historical range instead of "latest". It composes with the existing
archive: already-indexed scans are skipped (idempotent), holes are filled, existing
frames are left alone. One bad/un-decodable volume or a transient fetch error skips
that volume and the run continues.

Selection (`plan_backfill`) is separated from execution (`run_backfill`) so the
``--dry-run`` preview reports exactly what a real run would fetch.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from backscatter.collect.collect import RenderFn, render_and_index
from backscatter.config import Config
from backscatter.ingest import naming, pull, s3
from backscatter.ingest.pull import PullStatus
from backscatter.ingest.s3 import S3Client
from backscatter.render.render import render_volume
from backscatter.store import db

log = logging.getLogger("backscatter.backfill")


@dataclass(frozen=True)
class BackfillPlan:
    """What a backfill over ``[start, end]`` for ``site`` would do (no downloads)."""

    site: str
    start: datetime
    end: datetime
    total: int  # assembled volumes listed in the range
    already_have: int  # already indexed → will be skipped
    to_fetch: int  # == len(fetch_keys)
    bytes_estimate: int  # listed size of the to-fetch set
    older_than_retention: int  # to-fetch volumes predating the active age window
    retention_cutoff: datetime | None  # None when the age limit is off
    fetch_keys: tuple[str, ...]  # the worklist (drives run_backfill)


@dataclass(frozen=True)
class BackfillReport:
    """Outcome of a real backfill run."""

    site: str
    fetched: int  # volumes downloaded + stored this run
    rendered: int  # of fetched, rendered OK
    render_failed: int  # of fetched, decode/render failed (volume kept)
    skipped: int  # fetch/list errors — volume not stored
    already_have: int  # pre-existing, skipped without fetching
    oldest: datetime | None  # span actually fetched
    newest: datetime | None


# Called per progress tick with (processed_so_far, to_fetch_total, live_report).
# Lets a long-running caller (the web backfill job, Slice 19) surface live progress
# without reimplementing the loop; the report is a snapshot of the counts so far.
ProgressCb = Callable[[int, int, "BackfillReport"], None]


def list_range(
    client: S3Client, site: str, start: datetime, end: datetime
) -> list[tuple[str, int]]:
    """``(key, size)`` for assembled volumes in ``[start, end]``, oldest first.

    Walks each UTC day-prefix from ``start`` to ``end`` inclusive (the bucket is laid
    out per day) and keeps keys whose scan time falls in the range. One day's listing
    error is logged and skipped, never fatal to the whole range."""
    found: list[tuple[str, int, datetime]] = []
    day = datetime(start.year, start.month, start.day, tzinfo=UTC)
    last_day = datetime(end.year, end.month, end.day, tzinfo=UTC)
    while day <= last_day:
        try:
            for key, size in s3.list_volume_objects(client, site, day):
                scan = naming.parse_scan_time(key)
                if start <= scan <= end:
                    found.append((key, size, scan))
        except Exception:
            log.exception("backfill: listing %s for %s failed; skipping that day",
                          site, f"{day:%Y-%m-%d}")
        day += timedelta(days=1)
    found.sort(key=lambda item: item[2])
    return [(key, size) for key, size, _ in found]


def plan_backfill(
    config: Config,
    conn: sqlite3.Connection,
    site: str,
    start: datetime,
    end: datetime,
    *,
    now: datetime,
    client: S3Client,
) -> BackfillPlan:
    """List the range, dedupe against the index, and tally the worklist."""
    objects = list_range(client, site, start, end)
    cutoff: datetime | None = None
    if config.retention_max_age_days is not None:
        cutoff = now - timedelta(days=config.retention_max_age_days)

    fetch_keys: list[str] = []
    bytes_estimate = 0
    already_have = 0
    older = 0
    for key, size in objects:
        scan = naming.parse_scan_time(key)
        if db.volume_exists(conn, site, scan):
            already_have += 1
            continue
        fetch_keys.append(key)
        bytes_estimate += size
        if cutoff is not None and scan < cutoff:
            older += 1

    return BackfillPlan(
        site=site,
        start=start,
        end=end,
        total=len(objects),
        already_have=already_have,
        to_fetch=len(fetch_keys),
        bytes_estimate=bytes_estimate,
        older_than_retention=older,
        retention_cutoff=cutoff,
        fetch_keys=tuple(fetch_keys),
    )


def run_backfill(
    config: Config,
    conn: sqlite3.Connection,
    site: str,
    start: datetime,
    end: datetime,
    *,
    now: datetime,
    client: S3Client,
    render_fn: RenderFn = render_volume,
    progress_every: int = 25,
    progress_cb: ProgressCb | None = None,
) -> BackfillReport:
    """Fetch + render + index every not-yet-indexed volume in the range.

    ``progress_cb`` (optional) is invoked after *every* volume with
    ``(processed, to_fetch, report_snapshot)`` — the web job uses it for a smooth
    live progress bar (it's cheap, just a status update). ``progress_every`` only
    throttles the log line. Neither affects what gets fetched.
    """
    plan = plan_backfill(config, conn, site, start, end, now=now, client=client)
    fetched = rendered = render_failed = skipped = 0
    oldest: datetime | None = None
    newest: datetime | None = None

    def _snapshot() -> BackfillReport:
        return BackfillReport(
            site=site, fetched=fetched, rendered=rendered,
            render_failed=render_failed, skipped=skipped,
            already_have=plan.already_have, oldest=oldest, newest=newest,
        )

    for i, key in enumerate(plan.fetch_keys, start=1):
        try:
            result = pull.fetch_key(config, site, conn, key=key, client=client)
        except Exception:
            # Transient fetch/network error on one volume — skip, keep going.
            log.exception("backfill: fetch failed for %s; skipping", key)
            skipped += 1
            result = None

        if result is not None and result.status is PullStatus.STORED:
            assert result.path is not None and result.scan_time is not None
            fetched += 1
            scan = result.scan_time
            oldest = scan if oldest is None or scan < oldest else oldest
            newest = scan if newest is None or scan > newest else newest
            if render_and_index(
                config, conn, volume_path=result.path, site=site,
                scan_time=scan, now=now, render_fn=render_fn, label="backfill",
            ):
                rendered += 1
            else:
                render_failed += 1
        # else: ALREADY_HAVE race (not STORED) or a skipped fetch — counted above.

        if progress_cb is not None:
            progress_cb(i, plan.to_fetch, _snapshot())
        if progress_every and i % progress_every == 0:
            log.info("backfill %s: %d/%d processed", site, i, plan.to_fetch)

    return _snapshot()
