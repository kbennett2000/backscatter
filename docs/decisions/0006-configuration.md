# 6. Configuration via a single Config dataclass (env + defaults)

## Status
Accepted

## Context
Slice 1 (ingestion) is the first code that needs runtime configuration — the site
code, where raw volumes land, and where the SQLite index lives. CLAUDE.md requires
that these come from config and are never hardcoded, and points at "one config
file / env." Later slices (notably the `collect` service) will need the same
values plus more (poll interval, failover list), so the mechanism has to scale
without a rewrite of every call site.

## Decision
A single frozen `Config` dataclass (`src/backscatter/config.py`) is the **one
source of truth**. Every module takes a `Config`; no module reads the environment
on its own.

`load_config()` resolves each field with precedence **CLI argument > environment
variable > built-in default**:

- `site` — CLI positional (e.g. `backscatter pull KFTG`) > `BACKSCATTER_SITE` >
  `KFTG`.
- `data_dir` — `BACKSCATTER_DATA_DIR` > `./data`.
- `db_path` — `BACKSCATTER_DB_PATH` > `<data_dir>/backscatter.db`.

No config-file parsing yet. The loader is the only place that knows where values
come from, so a TOML (or similar) file loader slots in there later as one more
precedence layer (file sitting between env and defaults) without touching callers.

## Consequences
- Tests and ad-hoc runs configure everything via env vars or by constructing a
  `Config` directly — no global state, easy to isolate in a `tmp_path`.
- Adding a setting = one dataclass field + one line in `load_config()`.
- The defaults make `backscatter pull` work out of the box for the operator's KFTG
  default while staying fully overridable for any CONUS site.

## Alternatives considered
- **A config file (TOML) now.** Rejected for this slice as premature: more
  machinery than ingestion needs, and the dataclass-as-source-of-truth shape means
  we can add it later with no churn. Revisit when `collect` needs richer config.
- **Scattered `os.environ` reads at each call site.** Rejected: no single source
  of truth, hard to test, and exactly what CLAUDE.md warns against.
