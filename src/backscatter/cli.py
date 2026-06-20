"""Operator CLI for backscatter.

The full command surface from the roadmap is wired up; commands light up slice by
slice (see docs/ROADMAP.md). ``pull`` (Slice 1), ``site`` (Slice 2), ``render``
(Slice 3), and ``serve`` (Slice 4) are implemented; ``collect`` is still a stub.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from backscatter import __version__
from backscatter.config import load_config
from backscatter.ingest.pull import PullStatus, pull_latest
from backscatter.render.render import render_volume
from backscatter.sites.select import rank_sites

# How many ranked sites the `site` command prints.
_SITE_LIST_LEN = 5

# Subcommands without their own handler yet. Each grows real arguments and a
# handler as its roadmap slice is built.
_STUB_SUBCOMMANDS: tuple[tuple[str, str], ...] = (
    ("collect", "Run the continuous collection loop."),
)


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
        lat, lon = config.lat, config.lon
        # If a site override is configured, it — not the nearest — is what `pull`
        # will use. Surface that so the output isn't misleading.
        nearest = rank_sites(lat, lon)[0].site.icao
        if config.site != nearest:
            override_site = config.site

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
    print(
        f"Serving backscatter on http://{args.host}:{args.port} "
        f"(center {config.lat:.4f},{config.lon:.4f}, site {config.site})"
    )
    uvicorn.run(create_app(config), host=args.host, port=args.port)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``backscatter`` console script."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "pull":
        return _cmd_pull(args)

    if args.command == "site":
        return _cmd_site(args)

    if args.command == "render":
        return _cmd_render(args)

    if args.command == "serve":
        return _cmd_serve(args)

    print(f"backscatter {args.command}: not implemented yet")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
