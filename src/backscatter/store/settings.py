"""Runtime retention settings (ADR-0013).

Retention used to be immutable env config. It is now user-managed runtime state in the
SQLite store — the same model as locations (ADR-0008): the env
(``BACKSCATTER_RETENTION_DAYS`` / ``_MAX_GB``) only *seeds* an empty store; once the
row exists the DB is the source of truth. The collect loop reads this live each prune
pass, so a UI edit takes effect without a restart and reaches the separate collect
process via the shared DB.

A single ``retention_settings`` row holds the policy. ``NULL`` for either limit means
that limit is off (matching the env semantics where an unset/0 value disables it). The
storage unit for the size cap is **bytes**; the API speaks **GB** and converts at the
boundary.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from backscatter.config import Config

_GIB = 1024**3  # GB↔bytes for the size cap (mirrors config._GIB)


@dataclass(frozen=True)
class RetentionPolicy:
    """The active retention policy. ``None`` means that limit is off."""

    max_age_days: float | None
    max_size_bytes: int | None

    @property
    def active(self) -> bool:
        """Whether any limit is in force (else prune is a no-op)."""
        return self.max_age_days is not None or self.max_size_bytes is not None


def ensure_retention_seeded(conn: sqlite3.Connection, config: Config) -> None:
    """Seed the singleton row from env-derived config iff absent (DB wins once set)."""
    exists = conn.execute(
        "SELECT 1 FROM retention_settings WHERE id = 1"
    ).fetchone()
    if exists is None:
        with conn:
            conn.execute(
                "INSERT INTO retention_settings "
                "(id, max_age_days, max_size_bytes, updated_at) VALUES (1, ?, ?, ?)",
                (
                    config.retention_max_age_days,
                    config.retention_max_size_bytes,
                    _now_iso(),
                ),
            )


def get_retention(conn: sqlite3.Connection) -> RetentionPolicy:
    """Read the current policy. An unseeded store reads as no limits (prune no-op).

    Normal startup seeds the row from env via :func:`ensure_retention_seeded`; this
    fallback keeps an un-bootstrapped connection (e.g. a backfill worker on a brand-new
    DB before serve seeds it) safe rather than raising.
    """
    row = conn.execute(
        "SELECT max_age_days, max_size_bytes FROM retention_settings WHERE id = 1"
    ).fetchone()
    if row is None:
        return RetentionPolicy(None, None)
    return RetentionPolicy(row["max_age_days"], row["max_size_bytes"])


def update_retention(
    conn: sqlite3.Connection,
    *,
    max_age_days: float | None,
    max_size_bytes: int | None,
) -> RetentionPolicy:
    """Validate and replace the policy (full replace; ``None`` disables that limit)."""
    age = validate_age_days(max_age_days)
    size = validate_size_bytes(max_size_bytes)
    with conn:
        conn.execute(
            "INSERT INTO retention_settings "
            "(id, max_age_days, max_size_bytes, updated_at) VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "max_age_days = excluded.max_age_days, "
            "max_size_bytes = excluded.max_size_bytes, "
            "updated_at = excluded.updated_at",
            (age, size, _now_iso()),
        )
    return RetentionPolicy(age, size)


# --- validation (shared by the updater and the API) --------------------------


def validate_age_days(days: float | None) -> float | None:
    """Age limit in days: None/0 → off (None), <0 → error."""
    if days is None:
        return None
    days = float(days)
    if days < 0:
        raise ValueError("max_age_days must be >= 0 (0 or blank disables the limit)")
    return None if days == 0 else days


def validate_size_bytes(size_bytes: int | None) -> int | None:
    """Size cap in bytes: None → off, <=0 → error."""
    if size_bytes is None:
        return None
    size_bytes = int(size_bytes)
    if size_bytes <= 0:
        raise ValueError("max_size_bytes must be > 0 (blank disables the size cap)")
    return size_bytes


def gb_to_bytes(gb: float | None) -> int | None:
    """API boundary: GB → bytes. None → off, <=0 → error."""
    if gb is None:
        return None
    gb = float(gb)
    if gb <= 0:
        raise ValueError("max_size_gb must be > 0 (blank disables the size cap)")
    return int(gb * _GIB)


def bytes_to_gb(size_bytes: int | None) -> float | None:
    """API boundary: bytes → GB for display. None → None."""
    return None if size_bytes is None else size_bytes / _GIB


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
