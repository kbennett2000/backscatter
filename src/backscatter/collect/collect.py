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

from backscatter.config import Config, Location
from backscatter.ingest import s3
from backscatter.ingest.pull import PullResult, PullStatus, fetch_volume
from backscatter.ingest.s3 import S3Client
from backscatter.render.render import RenderResult, render_volume
from backscatter.sites.select import RankedSite, rank_sites
from backscatter.sites.table import site_by_icao
from backscatter.store import db
from backscatter.store import locations as locations_store

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
    """What one location did in one cycle, for logging and tests."""

    status: CycleStatus
    site: str | None = None
    scan_time: datetime | None = None
    location: str | None = None


def _ts(dt: datetime | None) -> str:
    return f"{dt:%Y-%m-%d %H:%M:%S}Z" if dt else "?"


def _candidate_sites(location: Location) -> list[RankedSite]:
    """Failover candidates for a location, nearest first.

    With an explicit site override (default location only), rank from the **pinned**
    site's coordinates so it is the primary and failover walks its neighbors (SITE
    means the same thing in `pull` and `collect`). Otherwise rank from the location's
    lat/lon.
    """
    if location.site_override:
        pinned = site_by_icao(location.site)
        if pinned is not None:
            return rank_sites(pinned.lat, pinned.lon)[:FAILOVER_CANDIDATES]
        # Unknown override ICAO: fall back to geographic ranking rather than fail.
        log.warning(
            "override site %s not in table; ranking from lat/lon", location.site
        )
    return rank_sites(location.lat, location.lon)[:FAILOVER_CANDIDATES]


def collect_cycle(
    locations: list[Location],
    config: Config,
    conn: sqlite3.Connection,
    *,
    now: datetime,
    client: S3Client,
    render_fn: RenderFn = render_volume,
) -> list[CycleResult]:
    """Run one pull→render→index pass over **every** given location.

    ``locations`` is read fresh from the store each cycle (live-reload), not cached.
    Locations are processed sequentially, so two locations sharing a nearest radar
    converge on one frame: the first stores it, the second sees ``ALREADY_HAVE`` —
    no double pull/store/render. One location's unexpected error is caught so the
    others (and the loop) carry on.
    """
    results: list[CycleResult] = []
    for location in locations:
        try:
            results.append(
                _collect_location(
                    location, config, conn, now=now, client=client, render_fn=render_fn
                )
            )
        except Exception:
            log.exception("location %s errored this cycle; continuing", location.name)
            results.append(CycleResult(CycleStatus.NOTHING, location=location.name))
    return results


def _collect_location(
    location: Location,
    config: Config,
    conn: sqlite3.Connection,
    *,
    now: datetime,
    client: S3Client,
    render_fn: RenderFn,
) -> CycleResult:
    """Collect one location, with failover across its own ranked neighbors."""
    name = location.name
    for rank, ranked in enumerate(_candidate_sites(location)):
        site = ranked.site.icao
        result = fetch_volume(config, site, conn, now=now, client=client)

        if result.status is PullStatus.NO_VOLUME:
            log.warning("[%s] no recent volume for %s; failing over", name, site)
            continue

        if result.status is PullStatus.ALREADY_HAVE:
            log.info("[%s] %s %s already indexed", name, site, _ts(result.scan_time))
            return CycleResult(
                CycleStatus.ALREADY_HAVE, site, result.scan_time, location=name
            )

        # STORED — a genuinely new scan.
        if rank > 0:
            log.warning("[%s] failover: collected %s (nearer had no data)", name, site)
        return _render_and_record(config, conn, result, render_fn, now, location=name)

    log.warning("[%s] no candidate site produced a volume this cycle", name)
    return CycleResult(CycleStatus.NOTHING, location=name)


def _render_and_record(
    config: Config,
    conn: sqlite3.Connection,
    result: PullResult,
    render_fn: RenderFn,
    now: datetime,
    *,
    location: str,
) -> CycleResult:
    # STORED guarantees these are set; assert narrows the Optional for the type checker.
    assert result.path is not None and result.scan_time is not None
    site, scan_time = result.site, result.scan_time
    log.info("[%s] stored %s %s; rendering", location, site, _ts(scan_time))
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
        log.info(
            "[%s] rendered %s %s -> %s", location, site, _ts(scan_time), image_path
        )
        return CycleResult(
            CycleStatus.RENDERED, site, scan_time, location=location
        )
    except Exception:
        log.exception(
            "[%s] render failed for %s %s; volume kept", location, site, _ts(scan_time)
        )
        db.mark_render_failed(conn, site, scan_time)
        return CycleResult(
            CycleStatus.RENDER_FAILED, site, scan_time, location=location
        )


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

    conn = locations_store.connect_bootstrapped(config)
    try:
        cycles = 0
        while not stop_event.is_set() and (max_cycles is None or cycles < max_cycles):
            try:
                # Re-read locations each cycle so UI edits take effect without a
                # restart: a new location starts archiving next cycle, a deleted
                # one stops.
                locations = locations_store.current_locations(
                    conn, config.site_override
                )
                collect_cycle(
                    locations, config, conn,
                    now=now_fn(), client=client, render_fn=render_fn,
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
