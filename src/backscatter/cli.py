"""Operator CLI for backscatter.

The full command surface from the roadmap is wired up; commands light up slice by
slice (see docs/ROADMAP.md). ``pull`` (Slice 1), ``site`` (Slice 2), ``render``
(Slice 3), ``serve`` (Slice 4), ``collect`` (Slice 5), ``prune`` (Slice 11), and
``backfill`` (Slice 12) are all implemented.
"""

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from backscatter import __version__
from backscatter.config import Config, load_config

if TYPE_CHECKING:
    from backscatter.prune.prune import PruneReport
from backscatter.ingest.pull import PullStatus, pull_latest
from backscatter.render.render import render_volume
from backscatter.sites.select import rank_sites
from backscatter.store import locations as locations_store

# How many ranked sites the `site` command prints.
_SITE_LIST_LEN = 5

# Subcommands without their own handler yet. Each grows real arguments and a
# handler as its roadmap slice is built.
_STUB_SUBCOMMANDS: tuple[tuple[str, str], ...] = ()


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="backscatter",
        description=(
            "Self-hosted NEXRAD radar viewer with an unlimited playback archive."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    pull_help = "Fetch the latest Level 2 volume for a site and index it."
    pull_parser = subparsers.add_parser("pull", help=pull_help, description=pull_help)
    pull_parser.add_argument(
        "site",
        nargs="?",
        default=None,
        help="Site code (e.g. KFTG). Defaults to the configured site.",
    )

    site_help = "Resolve the active radar site from a lat/lon."
    site_parser = subparsers.add_parser("site", help=site_help, description=site_help)
    site_parser.add_argument(
        "--near",
        metavar='"<lat>,<lon>"',
        default=None,
        help=(
            'Resolve for this location instead of the configured one, '
            'e.g. "39.4,-104.6".'
        ),
    )

    render_help = "Decode a stored volume and render a georeferenced image."
    render_parser = subparsers.add_parser(
        "render", help=render_help, description=render_help
    )
    render_parser.add_argument(
        "volume",
        help="Path to a stored _V06 volume file.",
    )

    serve_help = "Serve the map UI + frame API (FastAPI)."
    serve_parser = subparsers.add_parser(
        "serve", help=serve_help, description=serve_help
    )
    serve_parser.add_argument("--host", default="0.0.0.0", help="Bind host.")
    serve_parser.add_argument("--port", type=int, default=8000, help="Bind port.")

    collect_help = "Continuously pull, render, and index the latest frames."
    collect_parser = subparsers.add_parser(
        "collect", help=collect_help, description=collect_help
    )
    collect_parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="Stop after N cycles (default: run until interrupted).",
    )

    prune_help = "Delete archived frames that fall outside the retention policy."
    prune_parser = subparsers.add_parser(
        "prune", help=prune_help, description=prune_help
    )
    prune_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show what would be deleted without deleting anything.",
    )
    prune_parser.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="Skip the confirmation prompt (for scripts).",
    )

    backfill_help = "Fetch + render + index historical volumes over a past date range."
    backfill_parser = subparsers.add_parser(
        "backfill", help=backfill_help, description=backfill_help
    )
    backfill_parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Location name or site code (e.g. KFTG). Defaults to the configured site.",
    )
    backfill_parser.add_argument(
        "--start", required=True, metavar="<UTC>",
        help="Range start, UTC ISO-8601 (e.g. 2026-06-01T00:00:00Z).",
    )
    backfill_parser.add_argument(
        "--end", required=True, metavar="<UTC>",
        help="Range end, UTC ISO-8601 (inclusive).",
    )
    backfill_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show what would be fetched without downloading anything.",
    )
    backfill_parser.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="Skip the confirmation prompt (for scripts).",
    )

    for name, help_text in _STUB_SUBCOMMANDS:
        subparsers.add_parser(name, help=help_text, description=help_text)

    return parser


def _parse_latlon(text: str) -> tuple[float, float]:
    """Parse a ``"lat,lon"`` string into floats, raising ValueError on bad input."""
    parts = text.split(",")
    if len(parts) != 2:
        raise ValueError(f"expected 'lat,lon', got {text!r}")
    return float(parts[0]), float(parts[1])


def _cmd_pull(args: argparse.Namespace) -> int:
    config = load_config(site=args.site)
    result = pull_latest(config)
    if result.status is PullStatus.STORED:
        print(
            f"Stored {result.site} {result.scan_time:%Y-%m-%d %H:%M:%S}Z "
            f"-> {result.path}"
        )
    elif result.status is PullStatus.ALREADY_HAVE:
        print(
            f"Already have {result.site} "
            f"{result.scan_time:%Y-%m-%d %H:%M:%S}Z — nothing to do."
        )
    else:  # NO_VOLUME
        print(f"No volume found for {result.site}.")
    return 0


def _cmd_site(args: argparse.Namespace) -> int:
    override_site: str | None = None
    if args.near is not None:
        try:
            lat, lon = _parse_latlon(args.near)
        except ValueError as exc:
            print(f"Invalid --near value: {exc}")
            return 2
    else:
        config = load_config()
        conn = locations_store.connect_bootstrapped(config)
        try:
            default = locations_store.default_location(conn, config.site_override)
        finally:
            conn.close()
        lat, lon = default.lat, default.lon
        # If a site override is configured, it — not the nearest — is what `pull`
        # will use. Surface that so the output isn't misleading.
        nearest = rank_sites(lat, lon)[0].site.icao
        if default.site != nearest:
            override_site = default.site

    ranked = rank_sites(lat, lon)
    active = override_site or ranked[0].site.icao
    print(f"Location {lat:.4f}, {lon:.4f} — active site: {active}")
    if override_site is not None:
        print(f"  (overridden via config; nearest is {ranked[0].site.icao})")
    for entry in ranked[:_SITE_LIST_LEN]:
        site = entry.site
        marker = "covers" if entry.covers else "      "
        print(
            f"  {site.icao}  {entry.distance_km:6.0f} km  {marker}  "
            f"{site.name}, {site.state}"
        )
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    volume = Path(args.volume)
    if not volume.is_file():
        print(f"No such volume file: {volume}")
        return 2
    config = load_config()
    result = render_volume(volume, config)
    west, south, east, north = result.bounds_wgs84
    print(
        f"Rendered {result.site} {result.scan_time:%Y-%m-%d %H:%M:%S}Z "
        f"({result.width}x{result.height})"
    )
    print(f"  image:   {result.png_path}")
    print(f"  sidecar: {result.sidecar_path}")
    print(
        f"  bounds (W,S,E,N): {west:.4f}, {south:.4f}, {east:.4f}, {north:.4f}"
    )
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from backscatter.api.app import create_app

    config = load_config()
    conn = locations_store.connect_bootstrapped(config)
    try:
        d = locations_store.default_location(conn, config.site_override)
    finally:
        conn.close()
    print(
        f"Serving backscatter on http://{args.host}:{args.port} "
        f"(default {d.name} {d.lat:.4f},{d.lon:.4f} → {d.site})"
    )
    uvicorn.run(create_app(config), host=args.host, port=args.port)
    return 0


def _cmd_collect(args: argparse.Namespace) -> int:
    import logging
    import signal
    import threading

    from backscatter.collect.collect import run_collect

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config()
    stop = threading.Event()

    def _handle(signum: int, _frame: object) -> None:
        print(f"\nReceived signal {signum}; shutting down…")
        stop.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    conn = locations_store.connect_bootstrapped(config)
    try:
        loc_list = locations_store.current_locations(conn, config.site_override)
    finally:
        conn.close()
    locs = ", ".join(
        f"{loc.name}→{loc.site}{'*' if loc.is_default else ''}" for loc in loc_list
    )
    print(
        f"Collecting {len(loc_list)} location(s) [{locs}] "
        f"every {config.poll_interval_s:.0f}s — Ctrl-C to stop. (*=default)"
    )
    run_collect(config, stop_event=stop, max_cycles=args.max_cycles)
    return 0


def _policy_line(config: Config) -> str:
    """One-line summary of the active retention policy."""
    from backscatter.prune.prune import human_bytes

    days = config.retention_max_age_days
    size = config.retention_max_size_bytes
    age = f"{days:g} days" if days is not None else "off"
    cap = human_bytes(size) if size is not None else "unlimited"
    return f"Retention policy: age limit {age}, size cap {cap}"


def _print_prune_report(report: PruneReport, *, planned: bool) -> None:
    from backscatter.prune.prune import human_bytes

    verb = "Would prune" if planned else "Pruned"
    if report.deleted == 0:
        print(f"{verb} nothing — the archive is within policy.")
        return
    print(f"{verb} {report.deleted} frame(s), {human_bytes(report.bytes_reclaimed)}.")
    print(f"  oldest affected: {report.oldest}")
    print(f"  newest affected: {report.newest}")
    reasons = ", ".join(f"{k}={v}" for k, v in sorted(report.by_reason.items()))
    if reasons:
        print(f"  by reason: {reasons}")


def _cmd_prune(args: argparse.Namespace) -> int:
    import sys

    from backscatter.prune.prune import human_bytes, run_prune

    config = load_config()
    print(_policy_line(config))
    if not config.retention_active:
        print("No retention limits configured — nothing to prune.")
        return 0

    conn = locations_store.connect_bootstrapped(config)
    try:
        now = datetime.now(UTC)
        # Preview first — the same selection a live prune would make.
        preview = run_prune(conn, config, now=now, dry_run=True)
        _print_prune_report(preview, planned=True)
        if args.dry_run or preview.deleted == 0:
            return 0

        if not args.yes and sys.stdin.isatty():
            prompt = (
                f"Delete {preview.deleted} frame(s) / "
                f"{human_bytes(preview.bytes_reclaimed)}? [y/N] "
            )
            if input(prompt).strip().lower() not in ("y", "yes"):
                print("Aborted — nothing deleted.")
                return 0

        result = run_prune(conn, config, now=now, dry_run=False)
        _print_prune_report(result, planned=False)
        if result.skipped:
            print(
                f"  skipped {result.skipped} frame(s) on a file error (left intact)."
            )
        return 0
    finally:
        conn.close()


def _parse_utc(text: str, *, field: str) -> datetime:
    """Parse a UTC ISO-8601 timestamp (a trailing ``Z`` is accepted)."""
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(
            f"--{field} must be UTC ISO-8601 (e.g. 2026-06-01T00:00:00Z), got {text!r}"
        ) from exc
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _resolve_backfill_site(
    conn: sqlite3.Connection, config: Config, target: str | None
) -> str:
    """Resolve a target (location name or ICAO) to a site code.

    Location names win over site codes (a named place is what the user usually
    means); an unmatched token is treated as an ICAO and validated against the table.
    """
    from backscatter.sites.table import site_by_icao

    if target is None:
        return locations_store.default_location(conn, config.site_override).site
    lowered = target.lower()
    for loc in locations_store.current_locations(conn, config.site_override):
        if loc.name.lower() == lowered:
            return loc.site
    icao = target.upper()
    if site_by_icao(icao) is not None:
        return icao
    raise ValueError(f"unknown location or site: {target!r}")


def _cmd_backfill(args: argparse.Namespace) -> int:
    import sys

    from backscatter.backfill.backfill import plan_backfill, run_backfill
    from backscatter.ingest import s3
    from backscatter.prune.prune import human_bytes

    config = load_config()
    start = _parse_utc(args.start, field="start")
    end = _parse_utc(args.end, field="end")
    if start >= end:
        raise ValueError("--start must be before --end")

    now = datetime.now(UTC)
    client = s3.make_client()
    conn = locations_store.connect_bootstrapped(config)
    try:
        site = _resolve_backfill_site(conn, config, args.target)
        plan = plan_backfill(config, conn, site, start, end, now=now, client=client)
        print(
            f"Backfill {site}  {start:%Y-%m-%d %H:%M}Z … {end:%Y-%m-%d %H:%M}Z: "
            f"{plan.total} volume(s) in range, {plan.already_have} already indexed, "
            f"{plan.to_fetch} to fetch (~{human_bytes(plan.bytes_estimate)})."
        )
        if plan.older_than_retention and plan.retention_cutoff is not None:
            print(
                f"  WARNING: {plan.older_than_retention} volume(s) predate your "
                f"{config.retention_max_age_days:g}-day retention window (older than "
                f"{plan.retention_cutoff:%Y-%m-%d}); they'll be pruned on the next "
                "prune pass. Raise BACKSCATTER_RETENTION_DAYS or set it to 0 to keep."
            )
        if args.dry_run or plan.to_fetch == 0:
            return 0

        if not args.yes and sys.stdin.isatty():
            prompt = (
                f"Fetch {plan.to_fetch} volume(s) "
                f"(~{human_bytes(plan.bytes_estimate)}) for {site}? [y/N] "
            )
            if input(prompt).strip().lower() not in ("y", "yes"):
                print("Aborted — nothing fetched.")
                return 0

        report = run_backfill(config, conn, site, start, end, now=now, client=client)
        span = (
            f"{report.oldest:%Y-%m-%d %H:%M}Z … {report.newest:%Y-%m-%d %H:%M}Z"
            if report.oldest and report.newest
            else "—"
        )
        print(
            f"Backfilled {site}: fetched {report.fetched} "
            f"(rendered {report.rendered}, render-failed {report.render_failed}), "
            f"skipped {report.skipped}, already had {report.already_have}.\n"
            f"  Span: {span}."
        )
        return 0
    finally:
        conn.close()


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "pull":
        return _cmd_pull(args)
    if args.command == "site":
        return _cmd_site(args)
    if args.command == "render":
        return _cmd_render(args)
    if args.command == "serve":
        return _cmd_serve(args)
    if args.command == "collect":
        return _cmd_collect(args)
    if args.command == "prune":
        return _cmd_prune(args)
    if args.command == "backfill":
        return _cmd_backfill(args)
    print(f"backscatter {args.command}: not implemented yet")
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``backscatter`` console script."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    try:
        return _dispatch(args)
    except ValueError as exc:
        # Surface config errors (bad BACKSCATTER_LOCATIONS, etc.) cleanly.
        print(f"Configuration error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
