# PHP Deobfuscator Integration Refresh — Design

**Status:** approved, ready for implementation planning
**Date:** 2026-09-25
**Fork:** `mwdb-core-wafflemakers`
**Related:** `2026-04-29-phpdeobf-plugin-design.md` (the plugin this refreshes), `2026-09-24-threatlib-sync-and-curation-design.md` (attribute-definition and sync-script patterns reused here). External: `~/WORK/PHPDeobfuscator` (fork, source of the vendored engine), `~/WORK/php-eval-hook` (upstream `extremecoders-re/php-eval-hook`, commit `25e4e2a`).

## 1. Goal

Bring the `phpdeobf` sidecar and plugin up to date with the current PHPDeobfuscator fork and expose its new capabilities to researchers: comment stripping, dead-code removal, evidence-backed variable renaming, sandboxed execution of pure decoder functions, dynamic peeling of nested `eval()` layers with the php-eval-hook extension, and the security-analysis report. Make the sidecar build again, harden its container, and turn analysis results into searchable threat-intel attributes.

## 2. Findings that motivate this design

- The sidecar image no longer builds. `Dockerfile.server` installs whatever composer is current and the `php:8.5-cli` base has neither the zip extension nor `unzip`. Composer up to 2.8 silently fell back to `git clone` per package; 2.10.3 (August 2026) aborts on the first dist download. Probes on the same base image: 2.8.10 without unzip builds (packages as git checkouts); 2.10.3 without unzip fails; 2.10.3 with unzip builds from archives.
- The vendored copy (`docker/phpdeobf/`) dates from April. The source (`35634d5`) changed about 25 files and added `src/Analysis/`, `src/EvalHook/`, `src/PureFunction/`, `DeadCodeEliminator`, `VariableRenamer`, `CommentStripVisitor`, `UserFunctionPrepass`. The `Deobfuscator` constructor grew from two to seven options and gained `analyze()`. The methods `server.php` calls still exist with compatible defaults, so a plain re-sync would keep the tab working but expose nothing new. Composer requirements are unchanged.
- Eval peeling (`-e`) runs the sample in a php subprocess with the `evalhook` extension loaded via `-d extension=`; it needs the extension compiled against the container's PHP (8.5) and the path in `PHPDEOBF_EVALHOOK`. Pure-function execution (`-x`) also spawns a php subprocess. Both sandboxes use `open_basedir`, `disable_functions`, `disable_classes`, `allow_url_fopen=0`, a memory limit and a timeout, and write under the system temp dir.
- The sync script does not exclude the source tree's untracked artefacts (`out/`, `report/`, `mago-*.tar.gz`) nor its research scripts (`bin/`), and it copies the source's `composer.lock`, which the source repo does not track.
- The pipeline helpers (`deobfuscate()`, `peelEvalLayers()`) live as functions inside the source's CLI script `index.php`, so the sidecar cannot reuse them without duplication.

## 3. Decisions taken during brainstorming

| decision | choice |
|---|---|
| Scope | Full: options, analysis report, eval-hook peeling, hardening |
| Request model | One synchronous request per run; no job queue |
| Run control in the tab | One-click preset plus an "Advanced" panel of switches |
| Analysis results | Child JSON blob plus searchable `phpdeobf_sink` attributes |
| Pipeline sharing | Extract `PHPDeobfuscator\Pipeline` in the source repo, re-vendor; no copy in `server.php` |
| Extension source | Vendored into the fork (`docker/phpdeobf/evalhook/`), built in the image |

## 4. Sidecar image and hardening

### 4.1 Image (`docker/phpdeobf/Dockerfile.server`, multi-stage)

- **Builder** from `php:8.5-cli-bookworm`: `apt-get install $PHPIZE_DEPS unzip`; copy `docker/phpdeobf/evalhook/` and build with `phpize && ./configure --enable-evalhook && make`; install composer pinned (`--version=2.10.3`); `composer install --no-dev --optimize-autoloader` on the vendored tree.
- **Runtime** from the same base: copy `/app` (with `vendor/`) from the builder and `evalhook.so` to `/usr/local/lib/php/evalhook/evalhook.so`; `ENV PHPDEOBF_EVALHOOK=/usr/local/lib/php/evalhook/evalhook.so`; `USER nobody`; `CMD ["php", "-S", "0.0.0.0:8080", "server.php"]`. The main server process never loads the hook; only the peeler's subprocess does. No compiler, composer, git or unzip in the runtime image.

### 4.2 Container (`compose/compose.with-plugins.yml` and `docker-compose-prod.yml`, identical settings)

`read_only: true`; `tmpfs: - /tmp:size=512m`; `mem_limit: 2g`; `pids_limit: 256`; `cap_drop: [ALL]`; `security_opt: [no-new-privileges:true]`; `user: "65534:65534"`; healthcheck on `GET /health`. Network: the sidecar joins a dedicated compose network `phpdeobf-net` with `internal: true`; the `mwdb` service joins it too. MWDB can reach the sidecar; the sidecar has no route out.

### 4.3 Limits (env on the sidecar, with defaults)

`MAX_INPUT_BYTES` (5 MB), `PHPDEOBF_PEEL_TIMEOUT` (10 s), `PHPDEOBF_PURE_TIMEOUT` (the executor's default), `PHPDEOBF_MEMORY_LIMIT` (512M, server process). `server.php` sets `max_execution_time=120` for itself.

## 5. Sidecar HTTP API (`server.php`)

### `POST /deobfuscate`

Request: `{"source": str, "filename": str, "options"?: {strip_comments, execute_pure, eval_peel, remove_dead_code, rename_vars, analyze}}`. Every option is a boolean defaulting to `false`, so the existing client's body is unchanged in behaviour. Unknown keys are ignored; a non-boolean value is a 400.

Response, HTTP 200 on success:

```json
{"status": "ok",
 "output": "<static pipeline output>",
 "peeled": {"output": "...", "layers": 3, "secs": 1.2},
 "analysis": {"version": 1, "filename": "...", "sources": [], "sinks": [], "errors": [],
              "summary": {"source_count": 0, "sink_count": 0, "auto_exec_count": 0, "categories_present": []},
              "analyzed": "peeled"},
 "warnings": [{"code": "eval_peel_unavailable", "message": "..."}],
 "options": {"strip_comments": true, "...": false},
 "engine": {"phpdeobf": "35634d5", "evalhook_available": true},
 "elapsed_ms": 1840}
```

- `peeled` is `null` when peeling was not requested or captured nothing; a warning then carries the peeler's reason (`eval_peel_unavailable`, `eval_peel_no_layers`, `eval_peel_timeout`, `eval_peel_error`).
- `analysis` is `null` unless requested. When present it is the formatter's JSON payload plus `analyzed` (`peeled` when a peeled output exists, else `static`).
- Errors keep today's shapes: `parse_error`, `input_too_large`, `internal_error` at HTTP 200 with `status: "error"`; malformed body 400; unknown route 404.

### `GET /health`

`{"status": "ok", "evalhook_available": bool, "phpdeobf": "<commit from VERSION>"}`.

### Pipeline sharing

`PHPDeobfuscator\Pipeline` (new, in the source repo): static `deobfuscate($code, $filename, $dumpOrig, $stripComments, $executePure, $removeDeadCode, $renameVars): array{tree, code}` and `peelEvalLayers($code, $filename, $stripComments, $executePure, $removeDeadCode, $renameVars, ?string &$error): ?string`, containing exactly the logic now in `index.php`; `index.php` calls it. `server.php` calls the same class. Engine version comes from `docker/phpdeobf/VERSION` (written by the sync script).

## 6. Plugin (`docker/plugins/phpdeobf/`)

### Endpoint `POST /api/phpdeobf/<hash>`

Optional JSON body `{"preset": "full" | "static" | "custom", "options": {...}}`.

- `full` (default): all static options on, `analyze` on, `eval_peel` on when the sample bytes contain `eval` (case-insensitive).
- `static`: all options off (today's behaviour).
- `custom`: the explicit `options` map.

The resource reads the sample (5 MB cap), calls the sidecar once (client timeout 90 s), stores results, fires `on_created_text_blob` per new blob.

### Storage (children of the sample)

| result | where | name / type |
|---|---|---|
| static output | `TextBlob` | `deobfuscated-<hash>.php`, `blob_type` `deobfuscated-php` |
| peeled output | `TextBlob` | `deobfuscated-<hash>.evalpeel.php`, `blob_type` `deobfuscated-php-evalpeel` |
| analysis report | `TextBlob` | `analysis-<hash>.json`, `blob_type` `phpdeobf-analysis` |
| sink categories | attribute `phpdeobf_sink` on the sample | one value per `summary.categories_present` entry |

Blobs dedupe by content via `TextBlob.get_or_create`. The `phpdeobf_sink` attribute definition is created at plugin entrypoint (idempotent). Values are added with the permission check off (server-derived) and are additive across runs; they are never removed.

### Response to the tab

`blob_id`, `peeled_blob_id | null`, `analysis_blob_id | null`, `analysis_summary | null`, `warnings`, `engine`, effective `options`, `elapsed_ms`, per-blob `created` flags. Sidecar `error` results pass through at HTTP 200; transport failures 503; sample not found 404; oversize 413.

### `GET /api/phpdeobf/health`

Proxies the sidecar's `/health`; 503 when unreachable.

### Client (`client.py`)

`deobfuscate(source, base_url, timeout, options)` and `health(base_url)`; typed results extended with `peeled`, `analysis`, `warnings`, `engine`.

## 7. Tab (`components/PhpDeobfTab.tsx`)

- On mount: `GET /api/phpdeobf/health`; engine badge with commit and peeling availability.
- Primary button **Deobfuscate** runs `full`. Collapsed **Advanced** disclosure lists the six switches pre-filled with the preset values and a "Run with these options" button (`custom`); the eval-peel switch is disabled with a tooltip when the extension is unavailable. The PHP heuristic, size guard and "run anyway" checkbox stay.
- Result view: summary strip (elapsed, outputs produced, warnings in plain words); output blob links (static always; peeled with layer count); analysis panel with source, sink and auto-exec counts and one badge per sink category, each badge linking to the search `attribute.phpdeobf_sink:<category>`; link to the JSON report blob. Sidecar errors render as today.
- Reruns replace the view; the button is disabled while a run is in flight; the request is aborted on unmount.

## 8. Vendoring and sync

1. **Source repo first:** add `src/Pipeline.php`, make `index.php` use it, keep `php test.php` green, commit and push.
2. **`sync-from-source.sh`:** add excludes `out/`, `report/`, `bin/`, `mago-*.tar.gz`, `.worktrees/`, `.DS_Store`, `composer.lock` (the fork keeps its own lock); write `docker/phpdeobf/VERSION` with the source commit; refuse a dirty source tree unless `--allow-dirty`.
3. **Extension vendoring:** `docker/phpdeobf/evalhook/` with `evalhook.c`, `config.m4`, `LICENSE`, `SOURCE` (upstream URL + commit), copied by `sync-evalhook-from-source.sh` (tracked files only).
4. **Docs:** `docker/phpdeobf/README.md` (API and options, storage table, sync scripts, hardening); the fork section of `CLAUDE.md` mentions the second sync script once that section is committed.

## 9. Testing

- Source repo: `php test.php` corpus tests stay green after the Pipeline extraction.
- Sidecar `tests/server_test.php` (local PHP, no stack): existing cases; `options` echo; `analyze` payload with `analyzed`; `eval_peel` on a small self-decoding sample yields `peeled.layers >= 1` when `PHPDEOBF_EVALHOOK` is set, otherwise a visible skip; non-boolean option → 400; `GET /health` shape.
- Image-level check (documented, run during the plan): build, then `curl` the container with `eval_peel` on the self-decoding sample and confirm layers are captured. This proves the extension builds and works against PHP 8.5.
- Plugin unit tests (sidecar mocked): preset → option mapping, `eval` sniffing, three blob kinds with correct names and types, attribute values from `categories_present`, warnings and summary pass-through, health proxy and 503 mapping, dedupe on rerun.
- Backend e2e `tests/backend/test_phpdeobf.py`: `full` preset creates the analysis blob and the `phpdeobf_sink` attribute; a second run creates nothing new.
- Manual: the tab on an obfuscated sample and on one with `eval()` layers.

## 10. Rollout

1. PHPDeobfuscator PR (Pipeline extraction) merged.
2. Fork PR on branch `phpdeobf-refresh`, stacked on `phptype` (itself stacked on `threatlib`, PR #2 → #3).
3. Prod: rebuild `phpdeobf`, confirm `GET /api/phpdeobf/health` reports the extension, run the tab on one known sample. No data migration; existing `deobfuscated-php` blobs stay valid.

## 11. Non-goals

Async jobs; automatic analysis on upload; source findings as attributes; TLSH of deobfuscated output; changes to `yarax_regex` or `threatlib`.

## 12. Risks

- The extension has not yet been built against PHP 8.5; the plan's first image task surfaces any `phpize`/compile failure before anything else is built.
- `php -S` is single-process: a long peel blocks other researchers' runs for up to about a minute. Acceptable for the current team; a job queue is the upgrade path if it becomes a problem.
