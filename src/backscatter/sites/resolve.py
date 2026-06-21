"""Resolve a user-supplied target (location name or ICAO) to a radar site code.

Shared by the ``backfill`` CLI and the web backfill endpoint (Slice 19) so both
accept the same target syntax. Lives here (not in ``cli``) so the API never imports
the CLI module.
"""

from __future__ import annotations

import sqlite3

from backscatter.config import Config
from backscatter.sites.table import site_by_icao
from backscatter.store import locations as locations_store


def resolve_target_site(
    conn: sqlite3.Connection, config: Config, target: str | None
) -> str:
    """Resolve a target (location name or ICAO) to a site code.

    ``None`` → the default location's site. Otherwise location names win over site
    codes (a named place is what the user usually means); an unmatched token is
    treated as an ICAO and validated against the site table. Raises ``ValueError``
    for an unknown location or site.
    """
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
