"""Load the bundled NEXRAD site table.

The table is static package data (``nexrad_sites.csv``) — never fetched at runtime
(ADR-0005). See the file header for its source and retrieval date.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import cache
from importlib import resources

_CSV_NAME = "nexrad_sites.csv"


@dataclass(frozen=True)
class Site:
    """A single WSR-88D radar site."""

    icao: str
    name: str
    state: str
    lat: float
    lon: float


@cache
def load_sites() -> tuple[Site, ...]:
    """Return all bundled sites, read once and cached."""
    text = resources.files(__package__).joinpath(_CSV_NAME).read_text(encoding="utf-8")
    # Drop comment lines before handing the rest to the CSV reader.
    data_lines = [line for line in text.splitlines() if not line.startswith("#")]
    reader = csv.DictReader(data_lines)
    return tuple(
        Site(
            icao=row["icao"].strip().upper(),
            name=row["name"].strip(),
            state=row["state"].strip(),
            lat=float(row["lat"]),
            lon=float(row["lon"]),
        )
        for row in reader
    )
