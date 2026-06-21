# For developers

This section is for people who want to run backscatter from source, understand how it
works, or contribute. If you just want to *use* it, you want the
[Get started](../get-started/index.md) guides instead — no need to come here.

## What backscatter is (the honest pitch)

backscatter is a small, readable Python app with a vanilla-JS frontend. No magic, no
lock-in:

- **It runs anywhere.** A normal Python 3.12 environment, or one Docker container. No
  cloud account, no managed services.
- **Any US location.** The active radar is derived from a configured lat/lon against a
  bundled NEXRAD site table — nothing is hardcoded to one place.
- **All free public data.** Radar comes straight from NOAA's open S3 buckets, accessed
  anonymously. No API keys, no paid feeds, no credit card — and that's a hard rule, not
  a coincidence.
- **Yours to change.** [MIT licensed](https://github.com/kbennett2000/backscatter/blob/main/LICENSE).
  Fork it, run it, modify it.

## The shape of it

```
ingest  →  decode  →  render  →  store  →  api  →  web
(S3)       (Py-ART)   (reproj +   (SQLite   (Fast   (MapLibre
                       colormap)   + files)  API)    map)
```

A background **collect** loop walks that pipeline on an interval; the **api** serves the
saved frames to the browser. The [How it fits together](architecture.md) page draws this
out properly.

## Where to go next

<div class="grid cards" markdown>

-   :material-rocket-launch: __Run it locally__

    ---

    Clone to a running app in a couple of minutes (the fast `uv` path, no Docker).

    [:octicons-arrow-right-24: Run it locally](local.md)

-   :material-sitemap: __How it fits together__

    ---

    The pipeline, module by module, with a diagram.

    [:octicons-arrow-right-24: Architecture](architecture.md)

-   :material-test-tube: __Running the tests__

    ---

    pytest, ruff, mypy, and the frontend test.

    [:octicons-arrow-right-24: Testing](testing.md)

-   :material-source-pull: __Contributing__

    ---

    How changes are made here — slices, ADRs, and the one rule about rendering.

    [:octicons-arrow-right-24: Contributing](contributing.md)

</div>

You can also read the [Roadmap](../ROADMAP.md) (the slice-by-slice build history) and the
[Design decisions](decisions.md) (ADRs) to see *why* things are the way they are.
