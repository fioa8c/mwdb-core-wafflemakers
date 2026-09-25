# syntax=docker/dockerfile:1
# threatlib-sync sidecar: the backend image plus git/ssh, running the
# `threatlib-sync` console script instead of gunicorn. Mirrors
# deploy/docker/Dockerfile; keep the two in step when upstream changes.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app
ENV UV_NO_DEV=1
ENV UV_PYTHON_DOWNLOADS=0
ENV UV_LINK_MODE=copy

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

RUN --mount=type=cache,target=/root/.cache/uv \
    for plugin in $(find /app/plugins \( -name 'setup.py' -o -name 'pyproject.toml' \) -exec dirname {} \; | sort -u); \
    do uv pip --no-cache-dir install $plugin; done

FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libpq5 postgresql-client libmagic1 libfuzzy2 wget git openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder --chown=nobody:nogroup /app /app
ENV PATH="/app/.venv/bin:$PATH"

RUN mkdir -p /data/repo && chown nobody:nogroup /data /data/repo
USER nobody
RUN mkdir -m 700 /app/uploads

ENV PYTHONPATH=/app
ENV FLASK_APP=/app/mwdb/app.py
WORKDIR /app

CMD ["threatlib-sync"]
