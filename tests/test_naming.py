"""Tests for volume-name parsing — the dedupe key, so exactness matters."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backscatter.ingest import naming


def test_parse_scan_time_from_basename() -> None:
    assert naming.parse_scan_time("KFTG20260620_001530_V06") == datetime(
        2026, 6, 20, 0, 15, 30, tzinfo=UTC
    )


def test_parse_scan_time_from_full_key() -> None:
    key = "2026/06/20/KFTG/KFTG20260620_235959_V06"
    assert naming.parse_scan_time(key) == datetime(
        2026, 6, 20, 23, 59, 59, tzinfo=UTC
    )


def test_parse_site() -> None:
    assert naming.parse_site("2026/06/20/KLOT/KLOT20260620_120000_V06") == "KLOT"


def test_is_volume_key() -> None:
    assert naming.is_volume_key("2026/06/20/KFTG/KFTG20260620_001530_V06")
    # Metadata sidecars and other suffixes are not assembled volumes.
    assert not naming.is_volume_key("2026/06/20/KFTG/KFTG20260620_001530_V06_MDM")
    assert not naming.is_volume_key("2026/06/20/KFTG/")


@pytest.mark.parametrize(
    "bad",
    [
        "KFTG20260620_001530_V08",  # wrong version suffix
        "KFT20260620_001530_V06",  # 3-letter site
        "KFTG2026062_001530_V06",  # short date
        "garbage",
    ],
)
def test_parse_rejects_malformed(bad: str) -> None:
    with pytest.raises(naming.InvalidVolumeName):
        naming.parse_scan_time(bad)
