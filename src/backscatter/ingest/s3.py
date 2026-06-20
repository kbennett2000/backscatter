"""Anonymous access to NOAA's NEXRAD Level 2 archive bucket.

Access is unsigned — no AWS account or credentials (CLAUDE.md / ADR-0001). The
client is injectable so tests can pass a moto-backed client.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import boto3
from botocore import UNSIGNED
from botocore.client import Config as BotoConfig

from backscatter.ingest.naming import is_volume_key

# boto3 ships no inline types; we don't depend on the mypy_boto3_s3 stubs, so the
# S3 client is typed as Any (only a handful of methods are used here).
S3Client = Any

# Assembled volume scans, one object per complete scan (ADR-0001).
BUCKET = "unidata-nexrad-level2"
REGION = "us-east-1"


def make_client(client: S3Client | None = None) -> S3Client:
    """Return ``client`` if given, else a new unsigned S3 client."""
    if client is not None:
        return client
    return boto3.client(
        "s3",
        region_name=REGION,
        config=BotoConfig(signature_version=UNSIGNED),
    )


def date_prefix(site: str, date: datetime) -> str:
    """Build the ``YYYY/MM/DD/SITE/`` listing prefix for a site + UTC date."""
    return f"{date:%Y/%m/%d}/{site}/"


def list_volume_keys(client: S3Client, site: str, date: datetime) -> list[str]:
    """List assembled ``_V06`` keys under a site's prefix for the given UTC date."""
    prefix = date_prefix(site, date)
    keys: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if is_volume_key(key):
                keys.append(key)
    return keys


def download_volume(client: S3Client, key: str) -> bytes:
    """Download a single volume object and return its raw bytes."""
    response = client.get_object(Bucket=BUCKET, Key=key)
    body: bytes = response["Body"].read()
    return body
