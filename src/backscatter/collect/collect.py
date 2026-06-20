"""Continuous collection: resolve site → pull → render → index, on an interval.

A long-lived loop. Each cycle finds the latest volume for the nearest site (failing
over to the next-ranked site when the primary has no recent data), and — for a
genuinely new scan — downloads, renders, and records both the volume and its render
in the index. Most cycles find nothing new (the radar produces a volume only every
4–10 min); that is the normal state, not an error. One bad cycle never kills the
loop.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from backscatter.config import Config
from backscatter.ingest import s3
from backscatter.ingest.pull import PullResult, PullStatus, fetch_volume
from backscatter.ingest.s3 import S3Client
from backscatter.render.render import RenderResult, render_volume
from backscatter.sites.select import RankedSite, rank_sites
from backscatter.sites.table import site_by_icao
from backscatter.store import db

log = logging.getLogger("backscatter.collect")

# How many ranked sites to try in one cycle before giving up (failover depth).
FAILOVER_CANDIDATES = 3

# The render step, injectable so tests can stub out Py-ART.
RenderFn = Callable[..., RenderResult]


class CycleStatus(StrEnum):
    """Outcome of one collection cycle."""

    RENDERED = "rendered"
    ALREADY_HAVE = "already_have"
    RENDER_FAILED = "render_failed"
    NOTHING = "nothing"


@dataclass(frozen=True)
class CycleResult:
    """What one cycle did, for logging and tests."""

    status: CycleStatus
    site: str | None = None
    scan_time: datetime | None = None


def _ts(dt: datetime | None) -> str:
    return f"{dt:%Y-%m-%d %H:%M:%S}Z" if dt else "?"


def _candidate_sites(config: Config) -> list[RankedSite]:
    """Failover candidates, nearest first.

    With an explicit site override, rank from the **pinned** site's coordinates so it
    is the primary and failover walks its neighbors (SITE means the same thing in
    `pull` and `collect`). Otherwise rank from the configured lat/lon.
    """
    if config.site_override:
        pinned = site_by_icao(config.site)
        if pinned is not None:
            return rank_sites(pinned.lat, pinned.lon)[:FAILOVER_CANDIDATES]
        # Unknown override ICAO: fall back to geographic ranking rather than fail.
        log.warning("override site %s not in table; ranking from lat/lon", config.site)
    return rank_sites(config.lat, config.lon)[:FAILOVER_CANDIDATES]


def collect_cycle(
    config: Config,
    conn: sqlite3.Connection,
    *,
    now: datetime,
    client: S3Client,
    render_fn: RenderFn = render_volume,
) -> CycleResult:
    """Run one pull→render→index cycle, with failover across nearby sites."""
    candidates = _candidate_sites(config)
    for rank, ranked in enumerate(candidates):
        site = ranked.site.icao
        result = fetch_volume(config, site, conn, now=now, client=client)

        if result.status is PullStatus.NO_VOLUME:
            log.warning("no recent volume for %s; failing over", site)
            continue

        if result.status is PullStatus.ALREADY_HAVE:
            log.info("%s %s already indexed — nothing new", site, _ts(result.scan_time))
            return CycleResult(CycleStatus.ALREADY_HAVE, site, result.scan_time)

        # STORED — a genuinely new scan.
        if rank > 0:
            log.warning("failover: collected %s (nearer sites had no data)", site)
        return _render_and_record(config, conn, result, render_fn, now)

    log.warning("no candidate site produced a volume this cycle")
    return CycleResult(CycleStatus.NOTHING)


def _render_and_record(
    config: Config,
    conn: sqlite3.Connection,
    result: PullResult,
    render_fn: RenderFn,
    now: datetime,
) -> CycleResult:
    # STORED guarantees these are set; assert narrows the Optional for the type checker.
    assert result.path is not None and result.scan_time is not None
    site, scan_time = result.site, result.scan_time
    log.info("stored %s %s; rendering", site, _ts(scan_time))
    try:
        render = render_fn(result.path, config)
        image_path = (
            render.png_path.relative_to(config.data_dir / "renders").as_posix()
        )
        db.record_render(
            conn,
            site=site,
            scan_time=scan_time,
            image_path=image_path,
            elevation_deg=render.elevation_deg,
            width=render.width,
            height=render.height,
            bounds=render.bounds_wgs84,
            rendered_at=now,
        )
        log.info("rendered %s %s -> %s", site, _ts(scan_time), image_path)
        return CycleResult(CycleStatus.RENDERED, site, scan_time)
    except Exception:
        log.exception("render failed for %s %s; raw volume kept", site, _ts(scan_time))
        db.mark_render_failed(conn, site, scan_time)
        return CycleResult(CycleStatus.RENDER_FAILED, site, scan_time)


def run_collect(
    config: Config,
    *,
    stop_event: threading.Event | None = None,
    now_fn: Callable[[], datetime] | None = None,
    client: S3Client | None = None,
    render_fn: RenderFn = render_volume,
    max_cycles: int | None = None,
) -> None:
    """Run the collection loop until ``stop_event`` is set or ``max_cycles`` reached."""
    stop_event = stop_event or threading.Event()
    now_fn = now_fn or (lambda: datetime.now(UTC))
    client = s3.make_client(client)

    conn = db.connect(config.db_path)
    try:
        db.init_db(conn)
        cycles = 0
        while not stop_event.is_set() and (max_cycles is None or cycles < max_cycles):
            try:
                collect_cycle(
                    config, conn, now=now_fn(), client=client, render_fn=render_fn
                )
            except Exception:
                # One bad cycle (network/S3/decode) must not end collection.
                log.exception("collect cycle errored; backing off and continuing")
            cycles += 1
            if stop_event.is_set() or (max_cycles is not None and cycles >= max_cycles):
                break
            # Interruptible sleep: wakes immediately on shutdown.
            stop_event.wait(config.poll_interval_s)
    finally:
        conn.close()
