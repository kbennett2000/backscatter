# Contributing to backscatter

Thanks for the interest! backscatter is a small, self-hosted hobby project — a free
NEXRAD radar viewer with an unlimited archive. This is a short pointer; a fuller
contributor/dev guide lands with the documentation site (see the roadmap).

## Local setup
Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```
uv sync                              # create the venv + install deps (incl. dev)
uv run backscatter serve             # run the map UI at http://localhost:8000
uv run backscatter collect           # run the continuous collector
```

There's also a one-command container: `docker compose up -d` (see the README).

## Checks (keep these green)
```
uv run ruff check .                  # lint + import order
uv run mypy src/backscatter          # types (strict)
uv run pytest                        # Python tests
node --test web/gaps.test.js         # the frontend gap-detection rule
```

## How we work
- **Small, reviewable vertical slices** — one coherent change per branch/PR, end to
  end, with tests. See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the slice history and
  what's planned.
- **Decisions get an ADR** in [`docs/decisions/`](docs/decisions/) (context / decision /
  consequences / alternatives).
- **Rendering correctness is load-bearing.** Anything touching geometry
  (range/azimuth → lat/lon), reprojection, or color mapping needs value-based tests
  *and* a visual check against a reference — a wrong image can look plausible.
- **No paid anything.** No API keys, paid feeds, or credit-card services; all radar
  data is NOAA's free public S3. Keep it that way.

## Not for life-safety
backscatter is an enthusiast tool. It is **not** for protection of life or property —
that's what the National Weather Service and NOAA Weather Radio are for. Please don't
add features that imply official warning capability.
