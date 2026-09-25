# phpdeobf — MWDB plugin

Adds a "PHP Deobfuscate" tab to the sample detail page. It sends the sample's
bytes to the `phpdeobf` sidecar (a hardened HTTP wrapper around the
PHPDeobfuscator engine vendored under `docker/phpdeobf/`) and stores the
results as `TextBlob` children of the sample.

See spec: `docs/superpowers/specs/2026-04-29-phpdeobf-plugin-design.md`.

## The tab

On mount the tab fetches sidecar health (`GET /phpdeobf/health`) and a small
head slice of the sample, which it uses to guess whether the sample looks
like PHP (`<?php`/`<?` in the first 2 KB, or a `.php` filename). If it
doesn't, a "Run anyway" checkbox gates the run button; samples over 5 MB are
blocked outright (the sidecar's own cap — see below).

- **Deobfuscate** runs the `full` preset (the one-click path).
- **Advanced** expands a panel with a switch per sidecar option (see
  Presets below); "Run with these options" sends `preset: "custom"` with
  the panel's state. The panel pre-fills from the `full` preset the moment
  the sample head and engine health are known, but stops re-filling as soon
  as the user touches a switch.
- The `eval_peel` switch is disabled (and unticked) whenever health reports
  `evalhook_available: false`.
- Once a run completes, the tab shows elapsed time, any warnings, links to
  the blobs that were created/reused (the peeled blob's link is annotated
  with `(N layer(s))` from `peeled_layers` when a peel happened), and a
  security-analysis card (`AnalysisPanel`) with source/sink/auto-exec counts
  and a badge per sink category linking to a search on `attribute.phpdeobf_sink`.
- A sidecar-level error (HTTP 200, `status: "error"`) renders the error code
  and message; a transport failure (503) shows a generic "backend
  unavailable" banner.

## Presets

The request body sent to `POST /phpdeobf/<hash>` is:

```json
{"preset": "full" | "static" | "custom", "options": {...}}
```

`presets.py` (backend) is the single source of truth; `api.ts`'s `fullPreset`
only mirrors it client-side to pre-fill the advanced panel before the first
run — the backend always recomputes the options itself.

| Preset | Behaviour |
|---|---|
| `full` (default, omit the body or `{}`) | Every option `true`, except `eval_peel`, which is sniffed from the raw sample bytes (`True` iff the (case-insensitive) literal `eval` appears anywhere in the sample). |
| `static` | Every option `false` — deobfuscate only, no peeling, no analysis, no pure-function execution. |
| `custom` | Takes an explicit `options` map of booleans keyed by option name; any key not present defaults to `false`. Unknown keys or non-boolean values are a 400. |

Option keys (also the sidecar's option keys, unchanged end to end):

| Key | Effect |
|---|---|
| `strip_comments` | Strip comments from the input before reducing. |
| `execute_pure` | Execute provably-pure user functions in a sandboxed subprocess to resolve decoder calls the static reducer can't fold (`-x` in the engine CLI). |
| `eval_peel` | Run the sample under the `php-eval-hook` extension in a hardened sandbox and capture each `eval()` layer. Silently downgraded to `false` when the sidecar has no eval-hook extension built in. |
| `remove_dead_code` | Drop assignments to variables the reduced code never reads. |
| `rename_vars` | Give obfuscated-looking variables readable names when the code proves their role. |
| `analyze` | Run the security-analysis pass (sources/sinks/auto-exec) over the static output, and additionally over the peeled output when `eval_peel` captured layers, then merge both into one report and store it. |

## Sidecar API

Base URL: `MWDB_PHPDEOBF_URL` (mwdb service env), default `http://phpdeobf:8080`.
Full source: `docker/phpdeobf/server.php`.

### `POST /deobfuscate`

Request:

```json
{
  "source": "<?php ...",
  "filename": "input.php",
  "options": {
    "strip_comments": false, "execute_pure": false, "eval_peel": false,
    "remove_dead_code": false, "rename_vars": false, "analyze": false
  }
}
```

`filename` and `options` are optional (defaults: `"input.php"`, all options
`false`). Any option key not listed above is silently ignored on read but
a non-boolean value for a known key is a 400.

Response (HTTP 200, `status: "ok"`):

| Field | Type | Notes |
|---|---|---|
| `output` | string | The deobfuscated source. Always present. |
| `peeled` | `{output, layers, secs}` \| `null` | Present only when `eval_peel` ran and captured ≥1 layer. `layers` is an int, `secs` the peel wall-clock time. |
| `analysis` | object \| `null` | Present only when `analyze` was requested. Has `sources`, `sinks`, `errors` (each finding carries an `origin` of `"static"` or `"peeled"` marking which output it came from), `summary` (`source_count`, `sink_count`, `auto_exec_count`, `categories_present`, summed/unioned across origins), and `analyzed` (`"static"` when only the static output was scanned, `"static+peeled"` when the peeled output was scanned too and merged in — this always includes the static pass, so a sink outside the `eval()` call isn't dropped just because peeling also ran). |
| `warnings` | array of `{code, message}` | See warning codes below. |
| `options` | object | The (defaulted) options the sidecar actually used. |
| `engine` | `{phpdeobf, evalhook_available}` | `phpdeobf` is the vendored engine's short commit hash (from `VERSION`), or `"unknown"`. |
| `elapsed_ms` | int | Total sidecar processing time. |

Sidecar-level failure (still HTTP 200, `status: "error"`): `{"code": "...",
"message": "..."}`. Codes: `input_too_large` (source exceeds
`MAX_INPUT_BYTES`), `parse_error` (PHP-Parser couldn't parse the source),
`internal_error` (anything else, logged server-side with a stack trace).
Malformed requests (non-JSON body, missing/non-string `source`, non-object
`options`, non-boolean option value) are HTTP 400 with the same
`{code, message}` shape (`bad_request`). An unknown route is HTTP 404
(`not_found`).

Warning codes (`warnings[].code`), all non-fatal — the response is still
`status: "ok"` with `output` set to the static (unpeeled) result:

| Code | Meaning |
|---|---|
| `eval_peel_unavailable` | `eval_peel` was requested but the sidecar has no eval-hook extension built. |
| `eval_peel_no_layers` | Peeling ran but the sample didn't call `eval()` at all, or not in a way the hook could capture. |
| `eval_peel_timeout` | Peeling exceeded `PHPDEOBF_PEEL_TIMEOUT`. |
| `eval_peel_error` | Peeling failed for any other reason; `message` carries the underlying error text verbatim. |

### `GET /health`

```json
{"status": "ok", "evalhook_available": true, "phpdeobf": "6f13ff83fd6b"}
```

`evalhook_available` reflects whether the `evalhook.so` extension is loaded
in *this* sidecar process; `phpdeobf` is the same engine version string as
in `POST /deobfuscate`'s `engine.phpdeobf`.

The MWDB-side resource proxies both endpoints as `POST /phpdeobf/<hash>` and
`GET /phpdeobf/health`, mapping a transport failure or unhealthy sidecar to
HTTP 503 on either.

## Storage

On a successful sidecar call, the resource creates or reuses (dedupe is by
content + blob name + parent, via `TextBlob.get_or_create`) up to three
`TextBlob` children of the sample:

| Blob | `blob_type` | Created when |
|---|---|---|
| `deobfuscated-<hash>.php` | `deobfuscated-php` | Always. |
| `deobfuscated-<hash>.evalpeel.php` | `deobfuscated-php-evalpeel` | `peeled.output` was present in the sidecar response. |
| `analysis-<hash>.json` | `phpdeobf-analysis` | `analysis` was present in the sidecar response. |

The response's `peeled_layers` field mirrors `peeled.layers` (an int) when
the peeled blob was stored, else `null` — it exists purely so the tab can
show the layer count without re-parsing the blob.

Each category in `analysis.summary.categories_present` is recorded as a
`phpdeobf_sink` attribute (key `phpdeobf_sink`, value the category name,
e.g. `os_exec`) on the sample, with permission checks off — the
`check_permissions=False` argument to `sample.add_attribute(...)` in
`resource.py`'s `post()`. The attribute definition is created on demand the
first time it's used. Categories map to the sink groups documented in
`docker/phpdeobf/README.md`'s "Security analysis" section (`code_exec`,
`os_exec`, `dynamic_inc`, `dispatch`, `deser`, `file_write`, `network`,
`mail`, `header_inj`, `obfusc`).

A second call with identical input and options is a no-op from the caller's
perspective: `created`/`peeled_created`/`analysis_created` all come back
`false` and no duplicate blobs or attributes are added.

## Configuration

| Env var (mwdb service) | Default | Description |
|---|---|---|
| `MWDB_PHPDEOBF_URL` | `http://phpdeobf:8080` | Sidecar base URL. |

| Env var (sidecar) | Default | Description |
|---|---|---|
| `MAX_INPUT_BYTES` | `5242880` (5 MB) | Per-request source size cap; oversize requests get `input_too_large`. |
| `PHPDEOBF_PEEL_TIMEOUT` | `10` (seconds) | Wall-clock timeout for the `eval_peel` pass. |
| `PHPDEOBF_MEMORY_LIMIT` | `512M` | PHP `memory_limit` for the sidecar process. |
| `PHPDEOBF_EVALHOOK` | unset in general; baked into the image as the path to `evalhook.so` | Path the engine loads the eval-hook extension from; only meaningful when the extension was built (see Dockerfile.server below). |

The plugin (Python side) enforces a matching 5 MB cap on the sample before
it even reaches the sidecar (defense-in-depth) and a 90 s wall-clock timeout
on the HTTP call itself (`resource.py`'s `SIDECAR_TIMEOUT`).

**Dropped: `PHPDEOBF_PURE_TIMEOUT`.** The `execute_pure` pass (pure-function
sandbox execution) has its own wall-clock timeout, but it is **not**
configurable via an env var — it's `PureFunctionExecutor::TIMEOUT_SECONDS`,
a private constant fixed at 10 seconds in the engine source
(`src/PureFunction/PureFunctionExecutor.php`). An earlier iteration of this
plan called for a `PHPDEOBF_PURE_TIMEOUT` env var mirroring
`PHPDEOBF_PEEL_TIMEOUT`; it was dropped as out of scope for this refresh.

## Container hardening

`docker/phpdeobf/Dockerfile.server` builds the `evalhook.so` extension in a
separate `builder` stage (`phpize && ./configure --enable-evalhook && make`)
and copies only the compiled `.so` and the app into the final `php:8.5-cli-bookworm`
image, which runs as `USER nobody`. The `phpdeobf` service in
`compose/compose.with-plugins.yml` (and the equivalent block in
`docker-compose-prod.yml`) locks it down further:

- `read_only: true` root filesystem, with a `noexec,nosuid` `tmpfs` at `/tmp`
  (required — the pure-function sandbox and the eval-hook peel worker both
  need a writable scratch dir under `sys_get_temp_dir()`).
- `mem_limit: 2g`, `pids_limit: 256`.
- `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`.
- `user: "65534:65534"` (nobody:nogroup) at the compose level too.
- Attached only to `phpdeobf-net`, an `internal: true` Docker network with
  no route to the outside world or to any other service network — the mwdb
  service joins `phpdeobf-net` in addition to `default` so it alone can
  reach the sidecar; the sidecar itself cannot reach anything, including the
  Postgres/Redis/mwdb services it shares a host with.
- A `healthcheck` polling `GET /health` (also baked into the image itself).

## Vendored source

Two upstream projects are vendored into `docker/phpdeobf/` (the build
context for the sidecar image — NOT this directory):

- The PHPDeobfuscator engine itself, refreshed from `~/WORK/PHPDeobfuscator/`:

      bash docker/phpdeobf/sync-from-source.sh [--allow-dirty]

  Writes `docker/phpdeobf/VERSION` (source commit, source path, dirty flag,
  sync timestamp — the commit hash is what `engine.phpdeobf` reports).
  Sidecar-owned files (`server.php`, `Dockerfile.server`, both sync scripts,
  `tests/server_test.php`, `evalhook/`, `VERSION`, and a few dotfiles) are
  excluded so they survive the sync; `bin/lib/` is the one exception pulled
  in despite `bin/` otherwise being excluded, since `test.php` needs it at
  runtime.

- The `php-eval-hook` extension, refreshed from `~/WORK/php-eval-hook/`:

      bash docker/phpdeobf/sync-evalhook-from-source.sh

  Copies `evalhook.c`, `config.m4`, `LICENSE` into `docker/phpdeobf/evalhook/`
  and writes `docker/phpdeobf/evalhook/SOURCE` (upstream remote, commit,
  sync timestamp).

Review the diff and commit after either sync.
`docker/phpdeobf/README.md` is the engine's own README (synced verbatim from
upstream) — do not edit it in this repo; changes belong upstream and arrive
via the next sync.

## Tests

Sidecar HTTP wrapper test (mocks nothing — talks to a real `php -S` instance
the harness spins up, covers `/deobfuscate` and `/health` including a peel
case):

    docker run --rm --entrypoint php threatlib-phpdeobf tests/server_test.php

Plugin unit tests (mocked sidecar, no stack needed):

    cd docker/plugins/phpdeobf && python -m pytest tests/ -v

End-to-end tests (require the dev stack up with the `phpdeobf` plugin and
sidecar healthy):

    cd tests/backend
    export MWDB_ADMIN_LOGIN=admin MWDB_URL=http://127.0.0.1/api
    export MWDB_ADMIN_PASSWORD=$(grep MWDB_ADMIN_PASSWORD ../../mwdb-vars.env | cut -d= -f2)
    uv run pytest test_phpdeobf.py -v
