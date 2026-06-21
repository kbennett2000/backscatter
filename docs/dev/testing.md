# Running the tests

backscatter keeps a tight set of checks green. Run them all before sending a change.

## The Python checks

```bash
uv run pytest            # the test suite (moto fakes S3; no network needed)
uv run ruff check .      # lint + import ordering
uv run mypy src/backscatter   # type checking (strict)
```

- **pytest** uses [`moto`](https://docs.getmoto.org/) to fake S3 and stubs out Py-ART
  rendering, so the suite is fast and needs no network or real data.
- **ruff** and **mypy** are configured in `pyproject.toml` (ruff line length 88; mypy
  strict).

## The frontend check

The timeline's gap-detection rule has its own unit test, run with Node's built-in test
runner (no npm install needed):

```bash
node --test web/gaps.test.js
```

## Regenerating the documentation images

The screenshots and GIFs in these docs are captured from the **live app**, not drawn by
hand — so they can be regenerated when the UI changes. The capture script uses
[Playwright](https://playwright.dev/) and `ffmpeg`:

```bash
uv sync --group docs                 # installs mkdocs-material + playwright
uv run playwright install chromium   # one-time browser download
# make sure the app has some data first (collect/backfill), then:
uv run --group docs python scripts/capture_docs.py
```

It starts the app, drives it with a real browser, and writes fresh PNGs/GIFs to
`docs/assets/`. The `docs` dependency group (and Playwright) are **tooling only** — they
aren't part of the app's runtime dependencies and never go into the Docker image.

## Previewing the docs site

```bash
uv run --group docs mkdocs serve -a localhost:8001
```

Then open <http://localhost:8001> (port 8001 so it doesn't clash with the app on 8000). A
strict build — what CI runs — is:

```bash
uv run --group docs mkdocs build --strict
```

## What "green" means

A change is ready when `pytest`, `ruff`, `mypy`, `node --test web/gaps.test.js`, and
`mkdocs build --strict` all pass. Anything touching geometry or color mapping additionally
needs a visual check — see [Contributing](contributing.md).
