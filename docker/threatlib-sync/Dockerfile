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
       curl ca-certificates make \
    && rm -rf /var/lib/apt/lists/*

# libmagic built from source: bookworm's libmagic 5.44 classifies many PHP
# files as "JavaScript source"; 5.46 gets them right. The runtime stage
# copies the shared library and the compiled magic database from here.
ARG FILE_VERSION=5.46
ARG FILE_SHA256=c9cc77c7c560c543135edc555af609d5619dbef011997e988ce40a3d75d86088
RUN curl -fsSL -o /tmp/file.tar.gz "https://astron.com/pub/file/file-${FILE_VERSION}.tar.gz" \
    && echo "${FILE_SHA256}  /tmp/file.tar.gz" | sha256sum -c - \
    && tar xzf /tmp/file.tar.gz -C /tmp \
    && cd "/tmp/file-${FILE_VERSION}" \
    && ./configure --prefix=/usr/local --disable-static --disable-libseccomp \
    && make -j"$(nproc)" \
    && make install \
    && cd / \
    && rm -rf /tmp/file.tar.gz "/tmp/file-${FILE_VERSION}"

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
       libpq5 postgresql-client libfuzzy2 wget git openssh-client \
    && rm -rf /var/lib/apt/lists/*

# libmagic 5.46 from the builder stage (see there); ldconfig makes it
# the one python-magic finds, and its compiled-in default database path
# is /usr/local/share/misc/magic.mgc.
COPY --from=builder /usr/local/lib/libmagic.so.1* /usr/local/lib/
COPY --from=builder /usr/local/share/misc/magic.mgc /usr/local/share/misc/magic.mgc
RUN ldconfig

COPY --from=builder --chown=nobody:nogroup /app /app
ENV PATH="/app/.venv/bin:$PATH"

RUN mkdir -p /data/repo && chown nobody:nogroup /data /data/repo
USER nobody
RUN mkdir -m 700 /app/uploads

ENV PYTHONPATH=/app
ENV FLASK_APP=/app/mwdb/app.py
WORKDIR /app

CMD ["threatlib-sync"]
