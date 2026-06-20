"""Operator CLI for backscatter.

This is the v1 skeleton: the command surface from the roadmap is wired up, but the
subcommands are stubs that report they are not implemented yet. Feature logic lands
slice by slice (see docs/ROADMAP.md).
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from backscatter import __version__

# (name, help) for each operator subcommand. Kept flat for now; each grows real
# arguments and a handler as its roadmap slice is built.
_SUBCOMMANDS: tuple[tuple[str, str], ...] = (
    ("pull", "Fetch the latest Level 2 volume for a site and index it."),
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
    for name, help_text in _SUBCOMMANDS:
        subparsers.add_parser(name, help=help_text, description=help_text)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``backscatter`` console script."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    print(f"backscatter {args.command}: not implemented yet")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
