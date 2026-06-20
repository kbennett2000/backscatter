"""Operator CLI for backscatter.

The full command surface from the roadmap is wired up; commands light up slice by
slice (see docs/ROADMAP.md). ``pull`` is implemented (Slice 1); the rest are still
stubs that report they are not implemented yet.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from backscatter import __version__
from backscatter.config import load_config
from backscatter.ingest.pull import PullStatus, pull_latest

# Subcommands without their own handler yet. Each grows real arguments and a
# handler as its roadmap slice is built.
_STUB_SUBCOMMANDS: tuple[tuple[str, str], ...] = (
    ("site", "Resolve the active radar site from a lat/lon."),
    ("render", "Decode a stored volume and render a georeferenced image."),
    ("serve", "Run the FastAPI server (tiles + timeline API)."),
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

    for name, help_text in _STUB_SUBCOMMANDS:
        subparsers.add_parser(name, help=help_text, description=help_text)

    return parser


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


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``backscatter`` console script."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "pull":
        return _cmd_pull(args)

    print(f"backscatter {args.command}: not implemented yet")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
