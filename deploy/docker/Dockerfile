# syntax=docker/dockerfile:1
# Inspired by https://github.com/astral-sh/uv-docker-example/blob/fa761744819c05f4ce207bde1f0a396f6be3915f/multistage.Dockerfile
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

# Omit development dependencies
ENV UV_NO_DEV=1

# Disable Python downloads, because we want to use the system interpreter
# across both images. If using a managed Python version, it needs to be
# copied from the build image into the final image; see `standalone.Dockerfile`
# for an example.
ENV UV_PYTHON_DOWNLOADS=0
# No hardlinks supported
ENV UV_LINK_MODE=copy

# Build toolchain for C/C++ extensions compiled from source by uv sync.
# py-tlsh has no PyPI wheels (built from sdist on every platform).
RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ python3-dev \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project

COPY mwdb /app/mwdb
COPY docker/ pyproject.toml uv.lock /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install /app

ARG plugins
RUN --mount=type=cache,target=/root/.cache/uv \
    for plugin in $plugins $(find /app/plugins \( -name 'setup.py' -o -name 'pyproject.toml' \) -exec dirname {} \; | sort -u);  \
    do uv pip --no-cache-dir install $plugin; done

FROM python:3.12-slim-bookworm

LABEL maintainer="info@cert.pl"

# Runtime libraries:
#   libpq5            — PostgreSQL client lib (psycopg2 runtime)
#   postgresql-client — psql CLI used by mwdb-core's startup checks
#   libmagic1         — file type detection (was apk libmagic)
#   libfuzzy2         — ssdeep runtime (same name on Debian)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libpq5 postgresql-client libmagic1 libfuzzy2 \
    && rm -rf /var/lib/apt/lists/*

# Copy the application from the builder.
# Debian's `nobody` user belongs to the `nogroup` group (Alpine had `nobody:nobody`).
COPY --from=builder --chown=nobody:nogroup /app /app
ENV PATH="/app/venv/bin:$PATH"

USER nobody
RUN mkdir -m 700 /app/uploads

ENV PYTHONPATH=/app
# We still load it directly from the source and not the installed package in .venv
# to make it "editable" in dev environments
ENV FLASK_APP=/app/mwdb/app.py
WORKDIR /app

CMD ["/app/start.sh"]
