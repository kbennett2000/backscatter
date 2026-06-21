"""Real-time chunks bucket — assemble the latest 0.5° sweep before the volume completes.

The chunks bucket delivers partial volume data as it is scanned: each object is one LDM
record under a rotating per-volume dir
(``<SITE>/<1-999>/<YYYYMMDD-HHMMSS-CHUNK#-TYPE>``, type S=start/I=intermediate/E=end).
Concatenating the raw chunk bytes in order yields a partial AR2V stream Py-ART decodes
directly; once a second elevation cut appears, the lowest cut (0.5 deg reflectivity) is
complete and identical to the eventual assembled volume's tilt (proven: max diff 0 dBZ).

Slice 26a is the assembler only — finding the active volume + decoding its lowest sweep.
The live wiring into collect/serve + the archive reconciliation is Slice 26b.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial

from backscatter.decode.volume import Sweep, try_decode_lowest
from backscatter.ingest.s3 import S3Client

CHUNKS_BUCKET = "unidata-nexrad-level2-chunks"

# Concurrency for the active-dir scan. It reads the first key of every rotating
# volume dir (~500 for an active site); serial that is ~45s S3 round-trips, past a
# poll interval, so we fan the LISTs out. A boto3 client is safe across threads.
_DIR_SCAN_WORKERS = 32

# A chunk basename: <YYYYMMDD>-<HHMMSS>-<chunknum>-<type>, e.g. 20260621-212734-001-S.
_CHUNK_RE = re.compile(r"(?P<date>\d{8})-(?P<time>\d{6})-(?P<num>\d+)-(?P<type>[SIE])$")


@dataclass(frozen=True)
class Chunk:
    """One chunk object: its key, sequence number, type, and the volume's start time."""

    key: str
    num: int
    kind: str  # 'S' (start) | 'I' (intermediate) | 'E' (end)
    start: datetime  # volume start; same for all chunks in a dir (== assembled time)


def parse_chunk_key(key: str) -> Chunk:
    """Parse a chunks-bucket key (or basename) into a :class:`Chunk`."""
    m = _CHUNK_RE.search(key)
    if m is None:
        raise ValueError(f"not a chunk key: {key!r}")
    start = datetime.strptime(
        m.group("date") + m.group("time"), "%Y%m%d%H%M%S"
    ).replace(tzinfo=UTC)
    return Chunk(key=key, num=int(m.group("num")), kind=m.group("type"), start=start)


def order_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """Chunks in volume (scan) order — by chunk number."""
    return sorted(chunks, key=lambda c: c.num)


def list_chunk_dirs(client: S3Client, site: str) -> list[str]:
    """The rotating volume-dir prefixes for a site (e.g. ``'KFTG/70/'``)."""
    prefixes: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=CHUNKS_BUCKET, Prefix=f"{site}/", Delimiter="/"
    ):
        prefixes += [p["Prefix"] for p in page.get("CommonPrefixes", [])]
    return prefixes


def list_dir_chunks(client: S3Client, dir_prefix: str) -> list[Chunk]:
    """Every chunk in a volume dir, in volume order."""
    chunks: list[Chunk] = []
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=CHUNKS_BUCKET, Prefix=dir_prefix
    ):
        for obj in page.get("Contents", []):
            try:
                chunks.append(parse_chunk_key(obj["Key"]))
            except ValueError:
                continue  # non-chunk object (shouldn't happen) — skip
    return order_chunks(chunks)


def _dir_start(client: S3Client, dir_prefix: str) -> tuple[datetime, str] | None:
    """Read a dir's first chunk → ``(volume start, dir)``, or ``None`` if unreadable."""
    contents = client.list_objects_v2(
        Bucket=CHUNKS_BUCKET, Prefix=dir_prefix, MaxKeys=1
    ).get("Contents", [])
    if not contents:
        return None
    try:
        return parse_chunk_key(contents[0]["Key"]).start, dir_prefix
    except ValueError:
        return None


def find_latest_volume_dir(client: S3Client, site: str) -> str | None:
    """The volume dir with the most recent start time (the active / newest volume).

    Reads each dir's first key (chunk 1 = the volume start) and takes the max start.
    The per-dir reads run concurrently (``_DIR_SCAN_WORKERS``) so the O(dirs) scan is
    a few seconds, not ~45s. The collect loop (26b) calls this only at cold start or
    volume rollover (see :func:`advance_cursor`); a mid-scan volume is ridden cheaply.
    """
    dirs = list_chunk_dirs(client, site)
    with ThreadPoolExecutor(max_workers=_DIR_SCAN_WORKERS) as pool:
        starts = [r for r in pool.map(partial(_dir_start, client), dirs) if r]
    if not starts:
        return None
    return max(starts, key=lambda s: s[0])[1]


@dataclass(frozen=True)
class LiveCursor:
    """Per-site live-chunks state carried across collect polls.

    Lets the loop ride the active volume dir cheaply (one LIST/poll) and run the
    expensive ``find_latest_volume_dir`` scan only at cold start or rollover. ``done``
    means the 0.5 deg cut for ``volume_dir`` is already assembled+indexed (or already
    present) — we stop touching that volume until a newer dir appears.
    """

    volume_dir: str | None = None
    chunk_count: int = 0
    scan_time: datetime | None = None
    done: bool = False


def advance_cursor(client: S3Client, site: str, cursor: LiveCursor) -> LiveCursor:
    """Refresh the cursor for one poll: ride the active dir, or find a newer one.

    Riding an incomplete active dir is one cheap LIST to refresh the chunk count. On
    cold start, or once the active volume is ``done``, run the (parallel) latest-dir
    scan and start riding a newer dir if one appeared; otherwise return the cursor
    unchanged (no newer volume yet — retried next poll).
    """
    if cursor.volume_dir is not None and not cursor.done:
        found = list_dir_chunks(client, cursor.volume_dir)
        return replace(
            cursor,
            chunk_count=len(found),
            scan_time=found[0].start if found else cursor.scan_time,
        )
    latest = find_latest_volume_dir(client, site)
    if latest is None or latest == cursor.volume_dir:
        return cursor
    found = list_dir_chunks(client, latest)
    return LiveCursor(
        volume_dir=latest,
        chunk_count=len(found),
        scan_time=found[0].start if found else None,
        done=False,
    )


def assemble_lowest_sweep(
    client: S3Client, dir_prefix: str
) -> tuple[Sweep, bytes] | None:
    """Fetch the dir's chunks in order, concatenating until the 0.5° cut is complete.

    Returns ``(sweep, concatenated_bytes)`` once the lowest cut is frozen
    (``try_decode_lowest`` succeeds), or ``None`` if the available chunks don't yet
    complete it (volume still arriving / chunks missing). Stops fetching as soon as it's
    complete (~8 chunks), so it doesn't download the whole multi-MB volume.
    """
    buf = b""
    for chunk in list_dir_chunks(client, dir_prefix):
        buf += client.get_object(Bucket=CHUNKS_BUCKET, Key=chunk.key)["Body"].read()
        sweep = try_decode_lowest(buf)
        if sweep is not None:
            return sweep, buf
    return None
