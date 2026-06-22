"""Retention / pruning — bound the archive by age and/or size (ADR-0009).

There is one archive-wide policy (not per-location): a frame is pruned if it is
older than the age limit **or** falls in the oldest-first overflow above the size
cap — whichever triggers. Frames are one row per ``(site, scan_time)``, so
co-located locations already share a frame; pruning a row reclaims it exactly once.

Pruning removes the raw volume, the rendered PNG + JSON sidecar, **and** the index
row together. Selection (`select_candidates`) is pure and shared by dry-run and the
live path, so a `--dry-run` preview reports exactly what a real prune would remove.

Delete ordering — files first, then the row (honoring "delete the row only after
the files are gone"): a missing file is treated as success (idempotent, self-healing
after a crash), and a genuine file error (permission/locked) skips that frame with
its row **left intact** — never a dangling index row pointing at a deleted file.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from backscatter.config import Config
from backscatter.store import db
from backscatter.store.settings import RetentionPolicy

log = logging.getLogger("backscatter.prune")

# Rendered frames live under ``<data_dir>/renders/<image_path>`` (same literal the
# collect loop uses); kept here so prune does not depend on the api layer.
RENDERS_SUBDIR = "renders"

# How many sample error strings a report carries (full count is still reported).
_MAX_ERROR_SAMPLES = 5


class PruneReason(StrEnum):
    """Why a frame was selected for pruning."""

    AGE = "age"
    SIZE = "size"
    BOTH = "both"


@dataclass(frozen=True)
class PruneCandidate:
    """One frame selected for pruning, with its on-disk artifacts."""

    site: str
    scan_time: str  # stored ISO-8601 string
    reason: PruneReason
    bytes: int  # real on-disk footprint (raw + png + sidecar)
    raw: Path
    png: Path | None  # None when the volume was never rendered
    sidecar: Path | None

    def paths(self) -> tuple[Path, ...]:
        return tuple(p for p in (self.raw, self.png, self.sidecar) if p is not None)


@dataclass(frozen=True)
class PruneReport:
    """Outcome of a prune pass (or, for a dry-run, what one would do)."""

    dry_run: bool
    deleted: int  # frames removed (dry-run: would remove)
    bytes_reclaimed: int  # bytes freed (dry-run: would free)
    oldest: str | None  # oldest affected scan_time
    newest: str | None  # newest affected scan_time
    by_reason: dict[str, int]
    skipped: int  # frames that errored and were left intact (live only)
    errors: tuple[str, ...] = ()


def select_candidates(
    conn: sqlite3.Connection,
    config: Config,
    policy: RetentionPolicy,
    *,
    now: datetime,
) -> list[PruneCandidate]:
    """Pure selection: which frames ``policy`` would prune. No deletion.

    ``config`` supplies on-disk paths; ``policy`` (the live, DB-backed limits) decides
    what's over the line.
    """
    rows = db.frames_for_retention(conn)  # oldest scan first
    if not rows:
        return []

    age_hits: set[int] = set()
    if policy.max_age_days is not None:
        cutoff = (
            now - timedelta(days=policy.max_age_days)
        ).isoformat()
        age_hits = {r["id"] for r in rows if r["scan_time"] < cutoff}

    # Real on-disk bytes are needed for the size cap (over every row) and for the
    # report (over the selected rows). Compute lazily and cache by row id.
    sizes: dict[int, int] = {}

    def row_bytes(row: sqlite3.Row) -> int:
        cached = sizes.get(row["id"])
        if cached is None:
            cached = _row_bytes(row, config)
            sizes[row["id"]] = cached
        return cached

    size_hits: set[int] = set()
    if policy.max_size_bytes is not None:
        cap = policy.max_size_bytes
        total = sum(row_bytes(r) for r in rows)
        for r in rows:  # oldest first — drop until back under the cap
            if total <= cap:
                break
            size_hits.add(r["id"])
            total -= sizes[r["id"]]

    selected = age_hits | size_hits
    candidates: list[PruneCandidate] = []
    for r in rows:
        if r["id"] not in selected:
            continue
        in_age, in_size = r["id"] in age_hits, r["id"] in size_hits
        reason = (
            PruneReason.BOTH
            if in_age and in_size
            else PruneReason.AGE
            if in_age
            else PruneReason.SIZE
        )
        raw, png, sidecar = _row_paths(r, config)
        candidates.append(
            PruneCandidate(
                site=r["site"],
                scan_time=r["scan_time"],
                reason=reason,
                bytes=row_bytes(r),
                raw=raw,
                png=png,
                sidecar=sidecar,
            )
        )
    return candidates


def run_prune(
    conn: sqlite3.Connection,
    config: Config,
    policy: RetentionPolicy,
    *,
    now: datetime,
    dry_run: bool,
) -> PruneReport:
    """Select per ``policy`` and, unless ``dry_run``, delete each selected frame."""
    candidates = select_candidates(conn, config, policy, now=now)

    if dry_run:
        return _report(candidates, dry_run=True, skipped=0, errors=())

    affected: list[PruneCandidate] = []
    errors: list[str] = []
    skipped = 0
    for cand in candidates:
        try:
            _delete_frame(conn, cand)
        except OSError as exc:
            # A real file error (permission/locked) — leave the row intact so the
            # frame stays valid and is retried next pass. Never orphan the index.
            skipped += 1
            if len(errors) < _MAX_ERROR_SAMPLES:
                errors.append(f"{cand.site} {cand.scan_time}: {exc}")
            log.warning(
                "prune: skipping %s %s (%s)", cand.site, cand.scan_time, exc
            )
            continue
        affected.append(cand)

    report = _report(affected, dry_run=False, skipped=skipped, errors=tuple(errors))
    if report.deleted:
        log.info(
            "pruned %d frame(s), reclaimed %s",
            report.deleted,
            human_bytes(report.bytes_reclaimed),
        )
    return report


def _delete_frame(conn: sqlite3.Connection, cand: PruneCandidate) -> None:
    """Remove a frame's files (idempotent) then its row — files first.

    A missing file is success (already gone). Any other ``OSError`` propagates so the
    caller skips this frame with its row untouched — the row is deleted only after
    every file is gone, so the index never points at a deleted file.
    """
    for path in cand.paths():
        try:
            path.unlink()
        except FileNotFoundError:
            pass  # already gone — idempotent, self-heals a half-pruned frame
    db.delete_frame(conn, site=cand.site, scan_time=cand.scan_time)


def _row_paths(
    row: sqlite3.Row, config: Config
) -> tuple[Path, Path | None, Path | None]:
    """The raw volume, rendered PNG, and sidecar paths for a row (PNG/sidecar
    None when unrendered)."""
    raw = Path(row["path"])
    if not row["image_path"]:
        return raw, None, None
    png = config.data_dir / RENDERS_SUBDIR / row["image_path"]
    return raw, png, png.with_suffix(".json")


def _row_bytes(row: sqlite3.Row, config: Config) -> int:
    """Real on-disk bytes for a frame (raw + png + sidecar); missing files count 0."""
    total = 0
    for path in _row_paths(row, config):
        if path is None:
            continue
        try:
            total += path.stat().st_size
        except OSError:
            pass  # already gone or unreadable — contributes nothing to footprint
    return total


def _report(
    affected: list[PruneCandidate],
    *,
    dry_run: bool,
    skipped: int,
    errors: tuple[str, ...],
) -> PruneReport:
    scans = [c.scan_time for c in affected]
    by_reason: dict[str, int] = {}
    for c in affected:
        by_reason[c.reason.value] = by_reason.get(c.reason.value, 0) + 1
    return PruneReport(
        dry_run=dry_run,
        deleted=len(affected),
        bytes_reclaimed=sum(c.bytes for c in affected),
        oldest=min(scans) if scans else None,
        newest=max(scans) if scans else None,
        by_reason=by_reason,
        skipped=skipped,
        errors=errors,
    )


def human_bytes(n: int) -> str:
    """Format a byte count compactly (e.g. ``1.5 GB``)."""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"  # unreachable, satisfies the type checker
