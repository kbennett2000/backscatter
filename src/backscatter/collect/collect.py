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
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from backscatter.config import Config, Location
from backscatter.decode.volume import Sweep
from backscatter.ingest import chunks, naming, pull, s3
from backscatter.ingest.chunks import LiveCursor
from backscatter.ingest.pull import PullResult, PullStatus, fetch_volume
from backscatter.ingest.s3 import S3Client
from backscatter.prune.prune import run_prune
from backscatter.render.render import RenderResult, render_sweep, render_volume
from backscatter.sites.select import RankedSite, rank_sites
from backscatter.sites.table import site_by_icao
from backscatter.store import db, settings
from backscatter.store import locations as locations_store
from backscatter.track.associate import Candidate, associate_candidates
from backscatter.track.detect import detect_cells

log = logging.getLogger("backscatter.collect")

# How many ranked sites to try in one cycle before giving up (failover depth).
FAILOVER_CANDIDATES = 3

# Live reconciliation (26b). A live row older than this should have its assembled
# volume in S3 (26a measured a ~5-min median S3 lag after volume start; 6 min adds
# margin), so the reconcile sweep tries to upgrade it. And a still-incomplete live
# volume older than the give-up age is abandoned to the assembled path (we will never
# get its 0.5 deg cut live), so the cursor never gets stuck on one bad volume.
_LIVE_RECONCILE_DELAY = timedelta(minutes=6)
_LIVE_GIVEUP_AGE = timedelta(minutes=8)

# Storm-cell tracking (Slice 28b): don't link cells across an archive gap larger
# than this — a >20 min jump (a few missed volumes) makes predicted positions
# meaningless, so the frame starts fresh tracks instead of guessing continuity.
_TRACK_MAX_GAP = timedelta(minutes=20)
# Coasting grace (Slice 28e): a track may miss up to this many frames and still resume
# its id when the cell returns (a brief dip under the detection floor), rather than
# restarting as a new track. Small so we never coast across a genuine dissipation.
_TRACK_COAST_FRAMES = 2

# The render steps, injectable so tests can stub out Py-ART. ``RenderFn`` decodes a
# stored volume file; ``RenderSweepFn`` renders an already-decoded live Sweep.
RenderFn = Callable[..., RenderResult]
RenderSweepFn = Callable[..., RenderResult]


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
    live_cursors: dict[str, LiveCursor] | None = None,
    render_sweep_fn: RenderSweepFn = render_sweep,
) -> list[CycleResult]:
    """Run one pull→render→index pass over **every** given location.

    ``locations`` is read fresh from the store each cycle (live-reload), not cached.
    Locations are processed sequentially, so two locations sharing a nearest radar
    converge on one frame: the first stores it, the second sees ``ALREADY_HAVE`` —
    no double pull/store/render. One location's unexpected error is caught so the
    others (and the loop) carry on. ``live_cursors`` carries the per-site live-chunks
    state across cycles (the loop owns one dict); ``None`` means a fresh one (tests).
    """
    cursors = live_cursors if live_cursors is not None else {}
    results: list[CycleResult] = []
    for location in locations:
        try:
            results.append(
                _collect_location(
                    location,
                    config,
                    conn,
                    now=now,
                    client=client,
                    render_fn=render_fn,
                    live_cursors=cursors,
                    render_sweep_fn=render_sweep_fn,
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
    live_cursors: dict[str, LiveCursor],
    render_sweep_fn: RenderSweepFn,
) -> CycleResult:
    """Collect one location: the assembled archive frame, plus the live chunks frame.

    The assembled path (failover across ranked neighbors) is the source of truth and
    decides the returned ``CycleResult``. The live-chunks attempt + reconcile sweep are
    strictly additive (gated by ``config.live_chunks``) — they write/upgrade their own
    rows and never change the assembled outcome, and any live error is isolated so it
    can't mask an assembled success.
    """
    name = location.name
    candidates = _candidate_sites(location)
    cyc = _collect_assembled(name, candidates, config, conn, now, client, render_fn)

    primary = candidates[0].site.icao if candidates else None
    if config.live_chunks and primary is not None:
        try:
            cursor = live_cursors.get(primary, LiveCursor())
            live_cursors[primary] = _try_live_frame(
                config,
                conn,
                primary,
                cursor,
                now=now,
                client=client,
                render_sweep_fn=render_sweep_fn,
            )
            _reconcile_live_frames(config, conn, primary, now=now, client=client)
        except Exception:
            log.exception("[live] %s live/reconcile errored; continuing", primary)
    return cyc


def _collect_assembled(
    name: str,
    candidates: list[RankedSite],
    config: Config,
    conn: sqlite3.Connection,
    now: datetime,
    client: S3Client,
    render_fn: RenderFn,
) -> CycleResult:
    """The assembled-archive collect, with failover across ranked neighbors."""
    for rank, ranked in enumerate(candidates):
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


def _try_live_frame(
    config: Config,
    conn: sqlite3.Connection,
    site: str,
    cursor: LiveCursor,
    *,
    now: datetime,
    client: S3Client,
    render_sweep_fn: RenderSweepFn,
) -> LiveCursor:
    """Assemble + index every live 0.5 deg surveillance cut for ``site`` (Slice 27b).

    ``ride_volume`` rides the active dir (one LIST + a fetch of only new chunks) and
    returns each newly-frozen cut. The first cut of a volume is the base
    (``source='live'``, reconciled to the assembled volume later); each later SAILS/MRLE
    cut is ``source='live-sails'``: permanent, since the archive has no object at its
    timestamp to reconcile to (ADR-0012). A stuck volume (no end chunk) is abandoned
    past the give-up age so the cursor rolls onto the next.
    """
    cur, fresh = chunks.ride_volume(client, site, cursor)
    for sweep in fresh:
        scan_time = sweep.scan_time
        if db.volume_source(conn, site, scan_time) is not None:
            continue  # already indexed (assembled raced us, or re-seen)
        is_base = cur.volume_start is not None and scan_time == cur.volume_start
        source = "live" if is_base else "live-sails"
        dest = pull.destination_for(config, site, scan_time)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(cur.buf)
        try:
            db.record_volume(
                conn,
                site=site,
                scan_time=scan_time,
                s3_key=cur.volume_dir or "",
                path=dest,
                size_bytes=len(cur.buf),
                downloaded_at=now,
                source=source,
            )
        except sqlite3.IntegrityError:
            continue  # raced another writer; it owns the row now
        _render_live_and_record(
            config,
            conn,
            sweep,
            site=site,
            scan_time=scan_time,
            now=now,
            render_sweep_fn=render_sweep_fn,
            source=source,
        )
    # Abandon a stuck volume (no end chunk) so the cursor rolls onto the next one.
    if (
        not cur.done
        and cur.volume_start is not None
        and (cur.volume_start < now - _LIVE_GIVEUP_AGE)
    ):
        return replace(cur, done=True)
    return cur


def _render_live_and_record(
    config: Config,
    conn: sqlite3.Connection,
    sweep: Sweep,
    *,
    site: str,
    scan_time: datetime,
    now: datetime,
    render_sweep_fn: RenderSweepFn,
    source: str = "live",
) -> bool:
    """Render an already-decoded live sweep + record it; mirror of render_and_index."""
    try:
        render = render_sweep_fn(sweep, config, site_icao=site, scan_time=scan_time)
        image_path = render.png_path.relative_to(config.data_dir / "renders").as_posix()
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
            "[live] %s %s -> %s (source=%s)", site, _ts(scan_time), image_path, source
        )
        _track_cells_for_frame(
            config, conn, site=site, scan_time=scan_time, render=render
        )
        return True
    except Exception:
        log.exception(
            "[live] render failed for %s %s; partial kept", site, _ts(scan_time)
        )
        db.mark_render_failed(conn, site, scan_time)
        return False


def _reconcile_live_frames(
    config: Config,
    conn: sqlite3.Connection,
    site: str,
    *,
    now: datetime,
    client: S3Client,
) -> None:
    """Upgrade live rows to the complete assembled volume once it has landed.

    For each ``source='live'`` row old enough that its assembled volume should exist
    (``_LIVE_RECONCILE_DELAY``): if the deterministic assembled key is present, replace
    the partial artifact with the complete volume and flip the row to ``assembled`` (no
    re-render — the PNG is identical). A missing object or any fetch error just leaves
    the row untouched to retry next cycle: the upgrade is the only mutation and it runs
    only after a successful download, so a failure never corrupts the row.
    """
    for row in db.live_rows_before(conn, before=now - _LIVE_RECONCILE_DELAY):
        if row["site"] != site:
            continue
        scan_time = datetime.fromisoformat(row["scan_time"])
        key = naming.archive_key(site, scan_time)
        try:
            if not s3.object_exists(client, key):
                continue  # assembled volume not landed yet — retry next cycle
            data = s3.download_volume(client, key)
        except Exception:
            log.exception(
                "[live] reconcile fetch failed for %s %s; will retry",
                site,
                _ts(scan_time),
            )
            continue
        dest = pull.destination_for(config, site, scan_time)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)  # overwrite the partial with the complete volume
        db.upgrade_to_assembled(
            conn,
            site=site,
            scan_time=scan_time,
            s3_key=key,
            path=dest,
            size_bytes=len(data),
        )
        log.info("[live] reconciled %s %s -> source=assembled", site, _ts(scan_time))


def _track_cells_for_frame(
    config: Config,
    conn: sqlite3.Connection,
    *,
    site: str,
    scan_time: datetime,
    render: RenderResult,
) -> None:
    """Identify storm cells for a just-rendered frame and store them (Slice 28a).

    Gated by ``config.track_cells`` and best-effort: it runs off the render's in-memory
    raster (no re-projection) and swallows any error, so tracking never fails a frame
    whose imagery rendered fine. Cross-frame association + motion is Slice 28b; this is
    per-frame identification only.
    """
    if not config.track_cells or render.raster is None:
        return
    try:
        cells = detect_cells(render.raster.dbz, render.raster.bounds_3857)
        # Associate against recently-active tracks — including ones that missed the
        # last frame or two (coasting, Slice 28e) — so a cell that briefly dips under
        # the detection floor resumes its track id + motion instead of restarting. Each
        # candidate is aged by its time since last seen; the 20-min gap is a hard cap.
        raw = db.active_tracks_for_coast(
            conn, site=site, scan_time=scan_time, max_frames=_TRACK_COAST_FRAMES
        )
        candidates = [
            Candidate(tc, (scan_time - seen).total_seconds())
            for tc, seen in raw
            if scan_time - seen <= _TRACK_MAX_GAP
        ]
        tracked = associate_candidates(
            candidates,
            cells,
            allocate_id=lambda: db.allocate_track_id(
                conn, site=site, created_at=scan_time
            ),
        )
        db.record_cells(conn, site=site, scan_time=scan_time, cells=tracked)
        prev_ids = {c.track.track_id for c in candidates}
        continued = sum(1 for t in tracked if t.track_id in prev_ids)
        log.info(
            "[track] %s %s -> %d cells (%d continued)",
            site,
            _ts(scan_time),
            len(tracked),
            continued,
        )
    except Exception:
        log.exception(
            "[track] cell detection failed for %s %s; skipping", site, _ts(scan_time)
        )


def render_and_index(
    config: Config,
    conn: sqlite3.Connection,
    *,
    volume_path: Path,
    site: str,
    scan_time: datetime,
    now: datetime,
    render_fn: RenderFn = render_volume,
    label: str | None = None,
) -> bool:
    """Render one stored volume and record it; return whether it rendered.

    The decode→render→``record_render`` seam shared by the collect loop and backfill.
    On any render error the raw volume is kept and the row is marked ``failed``.
    """
    tag = label or site
    try:
        render = render_fn(volume_path, config)
        image_path = render.png_path.relative_to(config.data_dir / "renders").as_posix()
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
        log.info("[%s] rendered %s %s -> %s", tag, site, _ts(scan_time), image_path)
        _track_cells_for_frame(
            config, conn, site=site, scan_time=scan_time, render=render
        )
        return True
    except Exception:
        log.exception(
            "[%s] render failed for %s %s; volume kept", tag, site, _ts(scan_time)
        )
        db.mark_render_failed(conn, site, scan_time)
        return False


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
    rendered = render_and_index(
        config,
        conn,
        volume_path=result.path,
        site=site,
        scan_time=scan_time,
        now=now,
        render_fn=render_fn,
        label=location,
    )
    status = CycleStatus.RENDERED if rendered else CycleStatus.RENDER_FAILED
    return CycleResult(status, site, scan_time, location=location)


def run_collect(
    config: Config,
    *,
    stop_event: threading.Event | None = None,
    now_fn: Callable[[], datetime] | None = None,
    client: S3Client | None = None,
    render_fn: RenderFn = render_volume,
    render_sweep_fn: RenderSweepFn = render_sweep,
    max_cycles: int | None = None,
) -> None:
    """Run the collection loop until ``stop_event`` is set or ``max_cycles`` reached."""
    stop_event = stop_event or threading.Event()
    now_fn = now_fn or (lambda: datetime.now(UTC))
    client = s3.make_client(client)

    conn = locations_store.connect_bootstrapped(config)
    # Per-site live-chunks state, owned by the loop so the active-dir scan is amortized
    # across polls (cold-start scan once, then ride the active dir cheaply).
    live_cursors: dict[str, LiveCursor] = {}
    try:
        cycles = 0
        last_prune_at: datetime | None = None
        while not stop_event.is_set() and (max_cycles is None or cycles < max_cycles):
            now = now_fn()
            try:
                # Re-read locations each cycle so UI edits take effect without a
                # restart: a new location starts archiving next cycle, a deleted
                # one stops.
                locations = locations_store.current_locations(
                    conn, config.site_override
                )
                collect_cycle(
                    locations,
                    config,
                    conn,
                    now=now,
                    client=client,
                    render_fn=render_fn,
                    live_cursors=live_cursors,
                    render_sweep_fn=render_sweep_fn,
                )
            except Exception:
                # One bad cycle (network/S3/decode) must not end collection.
                log.exception("collect cycle errored; backing off and continuing")
            # Throttled retention pass: self-bounds the archive without a separate
            # cron. Runs the first cycle, then at most once per prune_interval_s. A
            # prune failure must not end collection either. The policy is read LIVE from
            # the DB each pass (ADR-0013), so a UI edit takes effect with no restart.
            policy = settings.get_retention(conn)
            if policy.active and _prune_due(
                now, last_prune_at, config.prune_interval_s
            ):
                last_prune_at = now
                try:
                    run_prune(conn, config, policy, now=now, dry_run=False)
                except Exception:
                    log.exception("prune pass errored; continuing")
            cycles += 1
            if stop_event.is_set() or (max_cycles is not None and cycles >= max_cycles):
                break
            # Interruptible sleep: wakes immediately on shutdown.
            stop_event.wait(config.poll_interval_s)
    finally:
        conn.close()


def _prune_due(
    now: datetime, last_prune_at: datetime | None, interval_s: float
) -> bool:
    """Whether a throttled prune should run now (always on the first cycle)."""
    if last_prune_at is None:
        return True
    return (now - last_prune_at).total_seconds() >= interval_s
