"""Tests for volume selection + the end-to-end pull, against a mocked S3 (moto)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from backscatter.config import Config
from backscatter.ingest import s3
from backscatter.ingest.pull import PullStatus, find_latest, pull_latest
from backscatter.store import db


@pytest.fixture
def s3_client() -> Iterator[object]:
    """A moto-backed S3 client with the archive bucket created."""
    with mock_aws():
        client = boto3.client("s3", region_name=s3.REGION)
        client.create_bucket(Bucket=s3.BUCKET)
        yield client


def _put_volume(client: object, scan: datetime, *, site: str = "KFTG") -> str:
    """Put a fake volume object whose name encodes ``scan``; return its key."""
    basename = f"{site}{scan:%Y%m%d_%H%M%S}_V06"
    key = f"{scan:%Y/%m/%d}/{site}/{basename}"
    client.put_object(Bucket=s3.BUCKET, Key=key, Body=b"fake-volume-bytes")  # type: ignore[attr-defined]
    return key


def _put_raw(client: object, key: str) -> None:
    """Put an arbitrary object (not a parseable volume) under the bucket."""
    client.put_object(Bucket=s3.BUCKET, Key=key, Body=b"x")  # type: ignore[attr-defined]


def _config(tmp_path: Path, site: str = "KFTG") -> Config:
    return Config(
        lat=39.3603,
        lon=-104.5969,
        site=site,
        site_override=False,
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "backscatter.db",
        poll_interval_s=60.0,
    )


def test_find_latest_picks_newest_same_day(s3_client: object) -> None:
    now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
    _put_volume(s3_client, datetime(2026, 6, 20, 0, 15, 30, tzinfo=UTC))
    newest = datetime(2026, 6, 20, 11, 50, 0, tzinfo=UTC)
    _put_volume(s3_client, newest)
    _put_volume(s3_client, datetime(2026, 6, 20, 6, 0, 0, tzinfo=UTC))

    found = find_latest(s3_client, "KFTG", now)
    assert found is not None
    _key, scan_time = found
    assert scan_time == newest


def test_find_latest_midnight_fallback_to_yesterday(s3_client: object) -> None:
    # Just past UTC midnight: today's prefix is empty, yesterday has volumes.
    now = datetime(2026, 6, 20, 0, 3, 0, tzinfo=UTC)
    _put_volume(s3_client, datetime(2026, 6, 19, 23, 50, 0, tzinfo=UTC))
    yesterdays_newest = datetime(2026, 6, 19, 23, 56, 0, tzinfo=UTC)
    _put_volume(s3_client, yesterdays_newest)

    found = find_latest(s3_client, "KFTG", now)
    assert found is not None
    _key, scan_time = found
    assert scan_time == yesterdays_newest


def test_find_latest_ignores_non_volume_objects(s3_client: object) -> None:
    # Non-_V06 objects share the prefix in the real bucket (MDM sidecars, tar-style
    # keys). They carry *later* clock times than the real volume, so if the
    # is_volume_key filter in list_volume_keys regressed, find_latest would pick one
    # of them (or blow up parsing it) instead of the correct _V06.
    now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
    real = datetime(2026, 6, 20, 11, 50, 0, tzinfo=UTC)
    _put_volume(s3_client, real)
    _put_raw(s3_client, "2026/06/20/KFTG/KFTG20260620_115500_V06_MDM")
    _put_raw(s3_client, "2026/06/20/KFTG/KFTG20260620_120000.tar")

    found = find_latest(s3_client, "KFTG", now)
    assert found is not None
    key, scan_time = found
    assert scan_time == real
    assert key.endswith("_V06")


def test_find_latest_returns_none_when_empty(s3_client: object) -> None:
    now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
    assert find_latest(s3_client, "KFTG", now) is None


def test_pull_latest_stores_and_indexes(tmp_path: Path, s3_client: object) -> None:
    now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
    scan = datetime(2026, 6, 20, 11, 50, 0, tzinfo=UTC)
    _put_volume(s3_client, scan)
    config = _config(tmp_path)

    result = pull_latest(config, now=now, client=s3_client)

    assert result.status is PullStatus.STORED
    assert result.scan_time == scan
    assert result.path is not None and result.path.is_file()
    assert result.path.read_bytes() == b"fake-volume-bytes"
    # File laid out by site/date.
    assert result.path.parent == config.data_dir / "KFTG" / "20260620"

    conn = db.connect(config.db_path)
    rows = conn.execute("SELECT site, scan_time FROM volumes").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["site"] == "KFTG"


def test_pull_latest_dedupes_on_rerun(tmp_path: Path, s3_client: object) -> None:
    now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
    _put_volume(s3_client, datetime(2026, 6, 20, 11, 50, 0, tzinfo=UTC))
    config = _config(tmp_path)

    first = pull_latest(config, now=now, client=s3_client)
    second = pull_latest(config, now=now, client=s3_client)

    assert first.status is PullStatus.STORED
    assert second.status is PullStatus.ALREADY_HAVE

    conn = db.connect(config.db_path)
    count = conn.execute("SELECT COUNT(*) AS n FROM volumes").fetchone()["n"]
    conn.close()
    assert count == 1

    # Exactly one file on disk for the site/date.
    stored = list((config.data_dir / "KFTG" / "20260620").iterdir())
    assert len(stored) == 1


def test_pull_latest_no_volume(tmp_path: Path, s3_client: object) -> None:
    now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
    result = pull_latest(_config(tmp_path), now=now, client=s3_client)
    assert result.status is PullStatus.NO_VOLUME
