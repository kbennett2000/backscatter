"""Mutable location store (ADR-0008).

Locations are user-managed runtime data persisted in the SQLite DB — not env config.
The env ``BACKSCATTER_LOCATIONS`` (or single-form) only *seeds* an empty store; once
it has rows, the DB is the source of truth. The active radar ``site`` is **derived**
(via ``nearest_site``), never stored, so editing lat/lon re-resolves automatically.

Every mutation runs in a transaction and re-checks the invariants (≥1 location,
exactly one default, unique names) on the resulting set, raising ``ValueError``.
"""

from __future__ import annotations

import sqlite3

from backscatter.config import (
    Config,
    Location,
    SeedLocation,
    resolve_location,
    validate_locations,
)
from backscatter.store import db, settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS locations (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL COLLATE NOCASE,
    lat        REAL NOT NULL,
    lon        REAL NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    UNIQUE(name)
);
-- DB-level backstop for "at most one default".
CREATE UNIQUE INDEX IF NOT EXISTS idx_locations_one_default
    ON locations(is_default) WHERE is_default = 1;
"""


def init_locations(conn: sqlite3.Connection) -> None:
    """Create the locations table + indexes if needed. Idempotent."""
    conn.executescript(_SCHEMA)
    conn.commit()


def bootstrap(conn: sqlite3.Connection, config: Config) -> None:
    """Ensure schema + the location and retention stores are seeded. Idempotent."""
    db.init_db(conn)  # creates the retention_settings table too
    init_locations(conn)
    ensure_seeded(conn, config)
    settings.ensure_retention_seeded(conn, config)


def connect_bootstrapped(config: Config) -> sqlite3.Connection:
    """Open a DB connection with schema + seeded location store ready."""
    conn = db.connect(config.db_path)
    bootstrap(conn, config)
    return conn


# --- seeding -----------------------------------------------------------------


def is_empty(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 0)


def seed(conn: sqlite3.Connection, rows: tuple[SeedLocation, ...]) -> None:
    """Insert the seed rows (caller ensures the store is empty)."""
    with conn:  # transaction
        conn.executemany(
            "INSERT INTO locations (name, lat, lon, is_default) VALUES (?, ?, ?, ?)",
            [(s.name, s.lat, s.lon, int(s.is_default)) for s in rows],
        )


def ensure_seeded(conn: sqlite3.Connection, config: Config) -> None:
    """Seed the store from the env seed iff it is currently empty (DB wins once set)."""
    if is_empty(conn):
        seed(conn, config.seed_locations)


# --- reads (resolved) --------------------------------------------------------


def _resolve(row: sqlite3.Row, override: str | None) -> Location:
    loc = resolve_location(
        row["name"], row["lat"], row["lon"],
        is_default=bool(row["is_default"]), override=override,
    )
    return Location(loc.name, loc.lat, loc.lon, loc.site, loc.is_default,
                    loc.site_override, id=row["id"])


def current_locations(conn: sqlite3.Connection, override: str | None) -> list[Location]:
    """All locations, default first then by name, each with its resolved site."""
    rows = conn.execute(
        "SELECT * FROM locations ORDER BY is_default DESC, name COLLATE NOCASE"
    ).fetchall()
    return [_resolve(r, override) for r in rows]


def default_location(conn: sqlite3.Connection, override: str | None) -> Location:
    """The default location, resolved. Assumes the store is seeded (≥1, one default)."""
    row = conn.execute("SELECT * FROM locations WHERE is_default = 1").fetchone()
    if row is None:  # only if the store was emptied out of band
        raise ValueError("no default location in the store")
    return _resolve(row, override)


def get(conn: sqlite3.Connection, loc_id: int) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM locations WHERE id = ?", (loc_id,)
    ).fetchone()
    return row


# --- writes (validated) ------------------------------------------------------


def _all_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM locations").fetchall()


def _check_invariants(conn: sqlite3.Connection) -> None:
    rows = _all_rows(conn)
    validate_locations(
        [r["name"] for r in rows], sum(1 for r in rows if r["is_default"])
    )


def create(
    conn: sqlite3.Connection,
    override: str | None,
    *,
    name: str,
    lat: float,
    lon: float,
    make_default: bool,
) -> Location:
    """Create a location. If make_default, it becomes the sole default."""
    name = name.strip()
    if not name:
        raise ValueError("location name must be non-empty")
    if _name_taken(conn, name):
        raise ValueError(f"a location named {name!r} already exists")
    try:
        with conn:
            if make_default:
                conn.execute("UPDATE locations SET is_default = 0")
            cur = conn.execute(
                "INSERT INTO locations (name, lat, lon, is_default) "
                "VALUES (?, ?, ?, ?)",
                (name, lat, lon, int(make_default)),
            )
            loc_id = int(cur.lastrowid or 0)
            _check_invariants(conn)
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"location violates a uniqueness constraint: {exc}") from exc
    return _require(conn, loc_id, override)


def update(
    conn: sqlite3.Connection,
    override: str | None,
    loc_id: int,
    *,
    name: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    make_default: bool | None = None,
) -> Location:
    """Update fields of a location. make_default=True sets it as the sole default."""
    row = get(conn, loc_id)
    if row is None:
        raise KeyError(loc_id)
    new_name = (name.strip() if name is not None else row["name"])
    if not new_name:
        raise ValueError("location name must be non-empty")
    if name is not None and _name_taken(conn, new_name, exclude_id=loc_id):
        raise ValueError(f"a location named {new_name!r} already exists")
    if make_default is False and row["is_default"]:
        raise ValueError(
            "cannot unset the default; set another location as default instead"
        )
    try:
        with conn:
            if make_default:
                conn.execute("UPDATE locations SET is_default = 0")
            conn.execute(
                "UPDATE locations SET name = ?, lat = ?, lon = ?, is_default = ? "
                "WHERE id = ?",
                (
                    new_name,
                    lat if lat is not None else row["lat"],
                    lon if lon is not None else row["lon"],
                    1 if make_default else row["is_default"],
                    loc_id,
                ),
            )
            _check_invariants(conn)
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"location violates a uniqueness constraint: {exc}") from exc
    return _require(conn, loc_id, override)


def delete(conn: sqlite3.Connection, loc_id: int) -> None:
    """Delete a location. Rejects deleting the last one or the current default."""
    row = get(conn, loc_id)
    if row is None:
        raise KeyError(loc_id)
    rows = _all_rows(conn)
    if len(rows) <= 1:
        raise ValueError("cannot delete the only location")
    if row["is_default"]:
        raise ValueError(
            "cannot delete the default location; set another location as default first"
        )
    with conn:
        conn.execute("DELETE FROM locations WHERE id = ?", (loc_id,))


def _name_taken(
    conn: sqlite3.Connection, name: str, *, exclude_id: int | None = None
) -> bool:
    sql = "SELECT 1 FROM locations WHERE name = ? COLLATE NOCASE"
    params: list[object] = [name]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    return conn.execute(sql, params).fetchone() is not None


def _require(conn: sqlite3.Connection, loc_id: int, override: str | None) -> Location:
    row = get(conn, loc_id)
    assert row is not None  # just written
    return _resolve(row, override)
