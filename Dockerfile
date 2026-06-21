# backscatter — single self-hosted container (FastAPI server + collect loop).
#
# Base is glibc Debian slim, NOT Alpine: arm-pyart pulls the full scientific stack
# (numpy/scipy/netCDF4/cartopy/matplotlib/…) which ships only manylinux (glibc)
# wheels. An ldd of those wheels needs just libstdc++/libgomp/libz from the system —
# everything heavy (OpenBLAS/HDF5/GEOS/PROJ/freetype) is bundled in the wheels.

# ---- builder: resolve + install deps into /app/.venv with uv -----------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

# The app is installed *editable* so its package resolves to /app/src/backscatter —
# the server locates the web/ dir via Path(__file__).parents[3]/"web" (= /app/web),
# which only holds when the source tree is laid out here. Hence src/ + web/ siblings.
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY web/ ./web/

RUN uv sync --frozen --no-dev

# ---- runtime: slim image with just the shared libs the wheels need -----------
FROM python:3.12-slim-bookworm AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libstdc++6 \
        libgomp1 \
        ca-certificates \
        bash \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app /app
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
# World-readable/executable so any (non-root) UID compose assigns can run the venv.
RUN chmod +x /app/docker-entrypoint.sh && chmod -R a+rX /app

ENV PATH="/app/.venv/bin:$PATH" \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/mpl \
    HOME=/tmp \
    BACKSCATTER_DATA_DIR=/data \
    BACKSCATTER_DB_PATH=/data/backscatter.db \
    BACKSCATTER_PORT=8085

EXPOSE 8085
ENTRYPOINT ["/app/docker-entrypoint.sh"]
