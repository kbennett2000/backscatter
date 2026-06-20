"""Runtime configuration — the single source of truth for site, paths, and DB.

Every module takes a :class:`Config`; no module reads the environment directly.
Precedence is **CLI argument > environment variable > built-in default**. The
loader is intentionally small so a file-based loader (e.g. TOML) can drop in
later without changing call sites (see ADR-0006).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SITE = "KFTG"
DEFAULT_DATA_DIR = Path("data")
# Default DB filename, placed inside the resolved data dir unless overridden.
DEFAULT_DB_NAME = "backscatter.db"


@dataclass(frozen=True)
class Config:
    """Resolved runtime configuration."""

    site: str
    data_dir: Path
    db_path: Path


def load_config(*, site: str | None = None) -> Config:
    """Resolve configuration with precedence CLI arg > env > default.

    Args:
        site: Site code from the CLI (e.g. the ``pull`` positional). When given it
            wins over ``BACKSCATTER_SITE`` and the default.
    """
    resolved_site = site or os.environ.get("BACKSCATTER_SITE") or DEFAULT_SITE

    data_dir_env = os.environ.get("BACKSCATTER_DATA_DIR")
    data_dir = Path(data_dir_env) if data_dir_env else DEFAULT_DATA_DIR

    db_path_env = os.environ.get("BACKSCATTER_DB_PATH")
    db_path = Path(db_path_env) if db_path_env else data_dir / DEFAULT_DB_NAME

    return Config(
        site=resolved_site.upper(),
        data_dir=data_dir,
        db_path=db_path,
    )
