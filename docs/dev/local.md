# Run it locally

The fast path to a running app from source — no Docker, just [uv](https://docs.astral.sh/uv/).
Good for development and poking around.

## Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** (the package/venv
  manager this project uses)

## Clone and install

```bash
git clone https://github.com/kbennett2000/backscatter.git
cd backscatter
uv sync          # creates a venv and installs everything (incl. dev tools)
```

`uv sync` reads `pyproject.toml` + `uv.lock`, so you get the exact pinned dependency set
(including Py-ART and the rest of the scientific stack).

## Configure your location

Same env vars as the Docker setup — set them in your shell (or a `.env` that your shell
sources). The simplest:

```bash
export BACKSCATTER_LOCATIONS='[{"name":"Home","lat":39.3603,"lon":-104.5969,"default":true}]'
```

Everything is read in one place ([`config.py`](https://github.com/kbennett2000/backscatter/blob/main/src/backscatter/config.py));
no module reads the environment directly.

## Run the pieces

backscatter is one CLI with a few subcommands:

```bash
uv run backscatter serve        # the web UI + API at http://localhost:8000
uv run backscatter collect      # the continuous collector (pull → render → index)
```

In real use you run both (that's what the container does). For a quick look, run `serve`
in one terminal and `collect` in another.

!!! tip "It starts empty — give it data fast"
    A fresh checkout has no radar yet. Either let `collect` run for a while, or pull a
    chunk of history immediately with a backfill:

    ```bash
    uv run backscatter backfill --start 2026-06-01T00:00:00Z --end 2026-06-01T03:00:00Z --dry-run
    ```

    Drop `--dry-run` (and add `--yes`) once the preview looks right.

## Handy one-offs

```bash
uv run backscatter site --near "39.36,-104.60"   # which radar covers a point?
uv run backscatter pull KFTG                      # fetch the latest volume for one site
uv run backscatter render path/to/VOLUME_V06      # render a single frame to a PNG
uv run backscatter prune --dry-run                # preview retention cleanup
```

Where things live on disk: raw radar volumes and rendered PNGs go under `data/`, with a
small SQLite index (`data/backscatter.db`). All of it is git-ignored.

Next: see [how it fits together](architecture.md), or [run the tests](testing.md).
