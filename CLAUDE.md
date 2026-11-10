# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

MWDB Core is a malware repository service built by CERT.pl: a Flask REST API backend (Python) + React/TypeScript SPA frontend, backed by Postgres and Redis, with optional S3-compatible storage and Karton pipeline integration. It ships as Docker images and as a PyPI package.

## Common commands

### Backend (Python, managed with uv)

```bash
./bootstrap-dev-env.sh          # one-time: installs uv, pre-commit, dev deps
ruff check --fix .              # lint (CI runs `ruff check --select I` + `ruff format --check`)
ruff format .                   # format
```

Backend sources live in `mwdb/`. `tests/` and `mwdb/web/` are excluded from ruff (see `pyproject.toml`).

### Frontend (`mwdb/web/`)

```bash
cd mwdb/web
npm install
npm run dev        # vite dev server
npm run build      # production build (required before `uv build`)
npm run test       # jest unit tests
npm run lint       # tsc --watch (CI runs plain `npx tsc`)
npx prettier --write src/
npx prettier --check src    # what CI runs
```

### Running the full stack

Compose is modular: `compose.sh` wraps `docker compose -f docker-compose.yml` (production-like base) and layers `compose/compose.with-<feature>.yml` for each `--with <feature>`. `--with dev` is the dev environment (mounts `./mwdb`, `./mwdb/web/src`, and `./docker/plugins` into the containers and enables `HOT_RELOAD=1`); in this fork also add `--with plugins` (see below). First generate env files, then bring it up:

```bash
./gen_vars.sh            # writes mwdb-vars.env + postgres-vars.env (random admin pw, printed)
./gen_vars.sh test       # variant that disables hooks and rate limiting, for tests
./compose.sh --with dev --with plugins up --build
# app at http://localhost:80, admin login printed by gen_vars.sh
```

Other features: `--with karton` (Karton pipeline), `--with s3` (S3 storage via silo), `--with oidc` (OIDC), `--with e2e` (backend + Cypress e2e). `./compose.sh --help` lists them. Frontend jest runs via `compose/compose.web-unit-tests.yml`.

### Tests

Backend e2e tests (`tests/backend/`) hit a running MWDB via HTTP — they are **not** unit tests, they need the stack up.

```bash
# Start stack with test-mode env:
./gen_vars.sh test
./compose.sh --with dev --with plugins up -d
# Then, from tests/backend:
export MWDB_ADMIN_LOGIN=admin
export MWDB_ADMIN_PASSWORD=...      # from mwdb-vars.env
export MWDB_URL=http://127.0.0.1/api
uv run pytest                        # all tests
uv run pytest -k attributes          # single test by keyword
uv run pytest test_search.py         # single file
```

Frontend e2e (`tests/frontend/`) is Cypress, driven by `./compose.sh --with e2e`.

### Debug UI for plugin extension points

Set `VITE_EXTENDABLE_DEBUG_BOX=1` in the `mwdb-web` service env (commented in `compose/compose.with-dev.yml`) to render labelled boxes around every `<Extendable>` slot in the SPA. Useful when a plugin needs to discover where it can hook in.

## Architecture

### Backend request flow

`mwdb/app.py` is the single routing table: it imports every `*Resource` from `mwdb/resources/` and binds it to a URL via `api.add_resource`. There is no decorator-based routing — adding a new endpoint means editing `mwdb/app.py`. The `hash64` URL converter (defined at the top of that file) matches MD5/SHA1/SHA256/SHA512 or the literal `root`, and the `<any(file, config, blob, object):type>` pattern is used pervasively because most endpoints are polymorphic over object types.

Three `before_request` hooks run in order on every API call:
1. `assign_request_id` — stamps `g.request_id` / `g.request_start_time`.
2. `require_auth` — resolves `Authorization: Bearer <token>` against session tokens, then API key tokens, then legacy v1 keys (the last path marks the request as deprecated). `g.auth_user` may be `None` for public endpoints. If maintenance mode is on, non-admins are rejected here.
3. `apply_rate_limit` — consults `core/rate_limit.py`.

`after_request` runs `execute_hook_handlers()` on 2xx responses (this is how plugin hooks fire) and emits metrics + structured logs.

Layering inside `mwdb/`:
- `core/` — Flask app bootstrap (`core/app.py` exposes `app` and `api`), config, auth primitives, plugin loader, search DSL parser (`core/search/` — Luqum-based Lucene query → SQLAlchemy), metrics, Karton client, logging, rate limits, capabilities.
- `model/` — SQLAlchemy 1.4 models; `model/migrations/` holds Alembic migrations (excluded from ruff). `flask db migrate` / `flask db upgrade` via `flask-migrate`.
- `schema/` — Marshmallow request/response schemas, one module per resource area.
- `resources/` — Flask-RESTful `Resource` classes. Each module mirrors a domain (file, config, blob, attribute, karton, oauth, remotes, …).
- `cli/` — the `mwdb-core` Click CLI (configure / create admin / run server); entry point is `mwdb.cli:cli`.
- `web/` — built SPA bundle is served as static assets when `MWDB_SERVE_WEB` is on (`core/static.py` blueprint).

### Object model

All user-visible artefacts (`File`, `Config`, `TextBlob`) are subclasses of a common `Object` and share tags, comments, shares, attributes, karton analyses, and parent/child relations. That is why routes are templated over `<any(file, config, blob, object):type>` — the `object` variant hits the base class, which is useful for cross-type operations (search, tag, share). When adding a feature to one object type, check whether it should live on `Object` so all types inherit it.

### Plugins

Plugins are Python packages listed in `MWDB_PLUGINS` or auto-discovered from `MWDB_LOCAL_PLUGINS_FOLDER` (mounted at `./docker/plugins` in dev). `core/plugins.py` calls each plugin's `entrypoint(PluginAppContext)` during app startup, which lets plugins register hook handlers, REST resources, URL converters, OpenAPI schemas, and OIDC provider classes. Hook handlers plug into `core/hooks.py` and run in the `after_request` phase for successful responses.

### Frontend plugin extension points

The SPA exposes an `<Extendable ident="...">` component (`mwdb/web/src/commons/plugins/Extendable.tsx`) which renders plugin-supplied React components at named slots. Adding new UI typically means wrapping it in `<Extendable>` so downstream users can customise behaviour without forking. The FE plugin system is separate from the backend plugin system — FE plugins live under `mwdb/web/` via the plugin loader in `commons/plugins/index.jsx`.

### Frontend routing / layout

Entry is `mwdb/web/src/index.jsx` → `App.tsx`. Route views are under `components/Views/`, feature areas under `components/{File,Config,Blob,Profile,Settings,Upload,Remote,...}`. Shared hooks/utilities/API client live in `commons/` (`commons/api` is the axios-based API client, `commons/auth` handles tokens/capabilities, `commons/plugins` holds `Extendable`).

## Conventions worth knowing

- **Versioning is semver and coupled**: a version bump belongs in the same PR as the change when `setup.py`/`mwdb.version` matches the latest release. Use `python3 dev/bump_version <new-version>` (see `CONTRIBUTING.md`).
- **Python ≥ 3.10**, pinned deps in `pyproject.toml`. SQLAlchemy is 1.4 (classic-style queries), Flask 3, Marshmallow 3, Flask-RESTful. Do not upgrade without intent.
- **Pre-commit** (`.pre-commit-config.yaml`) runs ruff (`--select I` + format) on commit. Backend test deps live in the `test` dep group of `pyproject.toml` (there is no separate `tests/backend/requirements.txt` any more).
- **Deprecation path** for legacy APIs uses `mwdb.core.deprecated.uses_deprecated_api(...)`. The legacy `/meta/...` endpoints in `resources/metakey.py` are the canonical example — new work should go through `/attribute/...`.

## Fork-specific additions (Waffle Makers)

This is a fork of CERT.pl's mwdb-core with in-tree plugins, extra CLI commands and a production deployment. Upstream conventions above still apply.

**Product direction:** this fork is meant to be the single source of truth for Automattic's malware researchers — not just where samples are stored, but where they are *processed*. Analysis tooling (PHPDeobfuscator, regex/YARA playgrounds, sandbox runs, future analyzers) belongs inside MWDB as in-tree plugins (a tab on the sample page + a REST resource), Karton-style sidecar services, or `mwdb-core` CLI commands — not as standalone scripts or external UIs. When asked to add an analysis capability, default to integrating it here following the plugin pattern below.

### In-tree plugins (`docker/plugins/`)

Each plugin is a single directory that is **both** a Python package (`pyproject.toml`, `__init__.py` exposing `entrypoint(app_context)`) **and** an npm package (`package.json` named `@mwdb-web/plugin-<name>`, `index.tsx` default-exporting the FE hooks such as `sampleTabsAfter`). The Docker images pick them up automatically: `deploy/docker/Dockerfile` pip-installs every dir with a `pyproject.toml`, and `Dockerfile-web` npm-installs every dir with a `package.json`; vite then exposes them via the virtual module `@mwdb-web/plugins`.

Because they are pip-installed into site-packages (not autodiscovered from `/app/plugins`), backend plugins must be listed explicitly in `MWDB_PLUGINS` in both `compose/compose.with-plugins.yml` (the fork-owned dev overlay, which also defines plugin sidecars like `phpdeobf`) and `docker-compose-prod.yml` — a new plugin needs that line updated in both files. Keep fork additions in that overlay rather than editing upstream's `compose/compose.with-dev.yml`, so upstream syncs merge cleanly.

- `yarax_regex` — YARA-X live regex playground tab; `POST /api/yarax/regex`.
- `phpdeobf` — "PHP Deobfuscate" tab; `POST /api/phpdeobf/<hash>` calls the `phpdeobf` sidecar (`MWDB_PHPDEOBF_URL`, default `http://phpdeobf:8080`) and creates a `TextBlob` child with `blob_type="deobfuscated-php"`. See `docker/plugins/phpdeobf/README.md`.
- `malleus` — no UI; creates the Malleus attribute definitions, grants the reporter/scanner groups (`MWDB_MALLEUS_SET_GROUPS` / `MWDB_MALLEUS_READ_GROUPS`) their permissions, and hooks that strip disallowed `context` keys and foreign `source:` tags. See `docker/plugins/malleus/README.md`.

The sidecar's PHP source in `docker/phpdeobf/` is **vendored** from `~/WORK/PHPDeobfuscator/` — don't edit it in place; refresh with `bash docker/phpdeobf/sync-from-source.sh` and commit.

Plugin tests (unit, sidecar mocked — no stack needed):

```bash
cd docker/plugins/phpdeobf && python -m pytest tests/ -v
cd docker/plugins/yarax_regex && python -m pytest tests/ -v
php docker/phpdeobf/tests/server_test.php     # sidecar HTTP wrapper
cd tests/backend && uv run pytest test_phpdeobf.py -v   # e2e, needs running stack
```

### Extra `mwdb-core` CLI commands (`mwdb/cli/`)

- `import-jetpack-threat-library <dir>` — bulk import of the threat library.
- `normalize-php <sandbox-url>` / `evalhook-php <sandbox-url>` — batch PHP deobfuscation / eval() analysis against the PHP sandbox service.

The sandbox comes from a sibling checkout `../waffle-makers-tooling` and is only started on demand via `docker-compose-sandbox.yml` layered on top of the prod compose. Run batch commands with `MWDB_ENABLE_HOOKS=0` so plugin hooks don't fire per object (exact invocations in `deploy/DEPLOYMENT.md`).

### Production

`docker-compose-prod.yml` (Traefik v3 + Let's Encrypt, `.env` with `MWDB_DOMAIN`/`ACME_EMAIL`) is deployed to a Digital Ocean droplet; `deploy/DEPLOYMENT.md` is the runbook, including backup/restore, cross-host Postgres migration and firewall gotchas. Read it before touching the prod compose file.
