"""Parse NEXRAD Level 2 archive object names.

Archive keys look like ``YYYY/MM/DD/SITE/<SITE><YYYYMMDD>_<HHMMSS>_V06`` and the
basename encodes the site and the volume's scan timestamp, e.g.
``KFTG20260620_001530_V06``. The parsed scan time is the dedupe key, so this must
be exact — it is covered by value-based tests.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

# Basename: 4-letter ICAO site, YYYYMMDD, '_', HHMMSS, '_V06'.
# Trailing suffixes (e.g. '_MDM' metadata sidecars) are intentionally not matched.
_VOLUME_RE = re.compile(
    r"^(?P<site>[A-Z]{4})"
    r"(?P<date>\d{8})_(?P<time>\d{6})"
    r"_V06$"
)


class InvalidVolumeName(ValueError):
    """Raised when a key/basename is not a recognized assembled-volume name."""


def _match(key: str) -> re.Match[str]:
    basename = key.rsplit("/", 1)[-1]
    match = _VOLUME_RE.match(basename)
    if match is None:
        raise InvalidVolumeName(key)
    return match


def is_volume_key(key: str) -> bool:
    """Return whether ``key`` names an assembled ``_V06`` volume."""
    return _VOLUME_RE.match(key.rsplit("/", 1)[-1]) is not None


def parse_site(key: str) -> str:
    """Return the 4-letter site code from a volume key/basename."""
    return _match(key).group("site")


def parse_scan_time(key: str) -> datetime:
    """Return the volume's scan timestamp as a UTC, tz-aware ``datetime``."""
    match = _match(key)
    return datetime.strptime(
        f"{match.group('date')}{match.group('time')}", "%Y%m%d%H%M%S"
    ).replace(tzinfo=UTC)
