"""Pull the latest assembled volume for a site and index it.

Strategy (ADR-0001): list the site's prefix for the current UTC date and take the
newest scan; if that prefix is empty (we're just past UTC midnight), fall back to
the prior date. Dedupe on the scan timestamp *before* downloading so re-running is
cheap and never duplicates.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from backscatter.config import Config
from backscatter.ingest import naming, s3
from backscatter.ingest.s3 import S3Client
from backscatter.store import db
from backscatter.store import locations as locations_store


class PullStatus(StrEnum):
    """Outcome of a pull attempt."""

    STORED = "stored"
    ALREADY_HAVE = "already_have"
    NO_VOLUME = "no_volume"


@dataclass(frozen=True)
class PullResult:
    """Result of :func:`pull_latest`, for the CLI to report and tests to assert."""

    status: PullStatus
    site: str
    scan_time: datetime | None = None
    s3_key: str | None = None
    path: Path | None = None


def find_latest(
    client: S3Client, site: str, now: datetime
) -> tuple[str, datetime] | None:
    """Return ``(key, scan_time)`` for the newest volume, or ``None`` if none.

    Lists today's UTC prefix; if empty, falls back to yesterday's. Selection is by
    max parsed scan time (not lexicographic) so it stays correct regardless of key
    ordering.
    """
    for date in (now, now - timedelta(days=1)):
        keys = s3.list_volume_keys(client, site, date)
        if keys:
            latest = max(keys, key=naming.parse_scan_time)
            return latest, naming.parse_scan_time(latest)
    return None


def _destination(config: Config, site: str, key: str, scan_time: datetime) -> Path:
    """Local path for a volume: ``data_dir/<SITE>/<YYYYMMDD>/<basename>``."""
    basename = key.rsplit("/", 1)[-1]
    return config.data_dir / site / f"{scan_time:%Y%m%d}" / basename


def fetch_key(
    config: Config,
    site: str,
    conn: sqlite3.Connection,
    *,
    key: str,
    client: S3Client,
) -> PullResult:
    """Dedupe→download→index one **known** volume key for ``site``.

    The ingest half shared by ``fetch_volume`` (latest) and ``backfill`` (historical):
    the scan time comes from the key, so the caller decides *which* volume to fetch.
    Idempotent — an already-indexed scan returns ``ALREADY_HAVE`` without downloading.
    """
    scan_time = naming.parse_scan_time(key)
    if db.volume_exists(conn, site, scan_time):
        return PullResult(
            status=PullStatus.ALREADY_HAVE,
            site=site,
            scan_time=scan_time,
            s3_key=key,
        )

    data = s3.download_volume(client, key)
    dest = _destination(config, site, key, scan_time)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)

    db.record_volume(
        conn,
        site=site,
        scan_time=scan_time,
        s3_key=key,
        path=dest,
        size_bytes=len(data),
        downloaded_at=datetime.now(UTC),
    )
    return PullResult(
        status=PullStatus.STORED,
        site=site,
        scan_time=scan_time,
        s3_key=key,
        path=dest,
    )


def fetch_volume(
    config: Config,
    site: str,
    conn: sqlite3.Connection,
    *,
    now: datetime,
    client: S3Client,
) -> PullResult:
    """Find→dedupe→download→index the latest volume for ``site``.

    Shared core used by the ``pull`` CLI and the collect loop. Operates on an
    already-open connection (the caller owns the lifecycle) and an explicit site,
    so the collect loop can fail over across sites.
    """
    found = find_latest(client, site, now)
    if found is None:
        return PullResult(status=PullStatus.NO_VOLUME, site=site)
    key, _ = found
    return fetch_key(config, site, conn, key=key, client=client)


def pull_latest(
    config: Config,
    *,
    now: datetime | None = None,
    client: S3Client | None = None,
) -> PullResult:
    """Fetch + index the latest volume for the default location. Idempotent."""
    now = now or datetime.now(UTC)
    client = s3.make_client(client)

    conn = locations_store.connect_bootstrapped(config)
    try:
        site = locations_store.default_location(conn, config.site_override).site
        return fetch_volume(config, site, conn, now=now, client=client)
    finally:
        conn.close()
