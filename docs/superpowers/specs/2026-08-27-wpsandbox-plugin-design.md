# WordPress Sandbox Plugin (`wpsandbox`) — Design

**Status:** ready for implementation planning
**Date:** 2026-08-27
**Fork:** `mwdb-core-wafflemakers`
**Related:** `2026-04-19-php-deobfuscation-pipeline-design.md` (batch normalization via the old DDEV sandbox — CLI only, no execution monitoring), `2026-04-29-phpdeobf-plugin-design.md` (the plugin pattern this spec extends with an async worker). External dependency: SecEx (`github.a8c.com/Automattic/secex`), a self-hosted E2B/Firecracker platform.

## 1. Goal

Add a "WP Sandbox" tab to the sample detail page that runs the sample inside a disposable SecEx microVM containing an instrumented WordPress install, and attaches a behavioural report to the sample: filesystem changes, network activity (real egress, captured), PHP eval trace, and WordPress database/state changes. Reports are first-class MWDB artefacts (a child `TextBlob`, dropped files as child `File`s, searchable attributes) so MWDB stays the single place where samples are both stored and processed.

## 2. Non-goals (v1)

- Cancelling a run that is already executing. Only `queued` runs can be cancelled.
- Automatic retries of failed runs, or job persistence beyond the Redis list.
- Automatic execution on upload (Karton-style). Runs are researcher-initiated.
- Injection mode (prepending code into existing WP files) and plain `php-cli` mode.
- Sinkhole / no-network egress modes. Real logged egress only, with a blocklist.
- Multiple WordPress/PHP version matrices. One template.
- Cypress e2e for the tab.

## 3. Architecture

Four pieces:

1. **SecEx template `wordpress`** — built from `docker/plugins/wpsandbox/template/wordpress.py`; an instrumented WordPress image snapshotted on SecEx.
2. **In-VM harness** — `docker/plugins/wpsandbox/template/harness/`, a Python CLI baked into the template that snapshots, triggers the sample, snapshots again, and prints `report.json`.
3. **MWDB plugin `wpsandbox`** — `docker/plugins/wpsandbox/`: run table, REST resources, FE tab. Same dual Python+npm layout as `phpdeobf`.
4. **Worker** — `docker/plugins/wpsandbox/worker/`: a separate container that consumes jobs from Redis, drives SecEx with the `e2b` Python SDK, and writes results back via MWDB's HTTP API. It does not import `mwdb`.

```
browser ──POST /api/wpsandbox/<hash>──▶ mwdb (plugin) ──RPUSH──▶ redis
                                                                  │ BLPOP
                                       mwdb ◀──HTTP (API key)── worker ──e2b SDK──▶ SecEx VM (harness)
browser ◀──GET /api/wpsandbox/run/<id> (poll)── mwdb
```

The worker is the only component that needs SecEx reachability, so it can be placed on the MWDB droplet (compose default) or on any host inside the a8c network if the droplet cannot reach `sandbox-api-secex.a8c.com`. That placement is a deployment decision, not a design one.

## 4. Template and harness

### 4.1 Template (`wordpress.py`)

- Base `php:8.4-apache`; MariaDB; WP-CLI; latest WordPress installed at `/var/www/html` with a fixed admin user (`admin` / known password), a few seeded posts, and two stock plugins so tampering has realistic targets.
- Instrumentation:
  - **evalhook** PHP extension (https://github.com/snake66/php-eval-hook, as used by the existing sandbox) with an `auto_prepend_file` that registers `__eval()` and appends `{depth, sha256, code}` per layer to `/var/log/harness/eval.jsonl`. It never blocks execution.
  - **mitmproxy** in transparent mode: iptables redirects outbound 80/443 to it; `HTTP_PROXY`/`HTTPS_PROXY` env set for Apache/PHP; the mitmproxy CA is in the system trust store so TLS is decrypted. Flows are dumped to `/var/log/harness/flows.jsonl` via a mitmproxy addon. If SecEx enforces an upstream egress proxy, mitmproxy chains to it (`--mode upstream`); capture point is unchanged.
  - **dnsmasq** with `log-queries` as the VM resolver → `/var/log/harness/dns.log`.
  - **auditd** watch on `/var/www/html` (`-p wa`) → FS events with pid. Complemented by a full pre/post manifest (`path, size, mode, sha256`) so the diff is correct even if events are missed.
  - **Egress blocklist**: iptables `DROP` for `WPSANDBOX_EGRESS_BLOCKLIST` (CIDRs; default RFC1918 + a8c ranges, injected at run time by the worker) and for outbound TCP 25/465/587.
- Snapshotted after setup so every run boots from the identical warm state. The VM is destroyed after each run.

### 4.2 Harness CLI

```
harness run --mode webroot|plugin --sample /tmp/sample \
            [--path wp-content/uploads/x.php] [--method GET|POST] [--query 'a=1'] [--body '...'] \
            [--timeout 120] [--blocklist 10.0.0.0/8,...]
```

1. Apply blocklist; start log tail markers.
2. `snapshot pre`: FS manifest; `mysqldump`; `wp user list`, `wp option list`, `wp cron event list`, `wp post list` as JSON.
3. Trigger:
   - **webroot**: copy sample to `--path` (default `wp-content/uploads/<sha256[:8]>.php`), request it with `--method`/`--query`/`--body`; record status, headers, first 64 KB of body, elapsed ms.
   - **plugin**: unzip into `wp-content/plugins/<slug>` (slug = top-level dir in the zip, else zip basename), `wp plugin activate <slug>`, then request `/`, `/wp-admin/` (admin cookie), `/wp-cron.php?doing_wp_cron`, `/?s=x`, `/wp-login.php`. All requests are recorded under `trigger.requests`.
4. Sleep `--timeout` seconds (background activity drains); PHP `max_execution_time` is set to `--timeout`.
5. `snapshot post`; diff against pre.
6. Write `report.json` **incrementally** to `/var/log/harness/report.json` after each phase, and print the final document to stdout. If killed mid-way, the partial file is what the worker retrieves.

Size caps: file contents and diffs ≤ 256 KB each; request bodies ≤ 64 KB; response bodies ≤ 256 KB; eval layers ≤ 256 KB each. Every cut is recorded by JSON path in `truncated[]`.

## 5. Plugin (`docker/plugins/wpsandbox/`)

### 5.1 Model

Table `wpsandbox_run`, created by a plugin Alembic migration registered via `PluginAppContext`:

| column | type | notes |
|---|---|---|
| `id` | uuid pk | |
| `object_id` | fk `object.id` | the sample |
| `requested_by` | fk `user.id` | |
| `mode` | text | `webroot` \| `plugin` |
| `params` | jsonb | `path, method, query, body, timeout` |
| `status` | text | `queued` \| `running` \| `done` \| `failed` \| `timeout` |
| `created_at`, `started_at`, `finished_at` | timestamptz | |
| `error` | text nullable | |
| `report_blob_id` | fk `object.id` nullable | the report `TextBlob` |
| `sandbox_id` | text nullable | SecEx sandbox id, for log correlation |

Derived, not stored: a run `running` for longer than `MWDB_WPSANDBOX_MAX_TIMEOUT + 600 s` is reported as `failed` with `error="worker lost"`.

### 5.2 REST

| Method | Path | Auth | Behaviour |
|---|---|---|---|
| `POST` | `/api/wpsandbox/<hash64:identifier>` | `adding_blobs` on the sample | Validate (`400`: sample > `MAX_SAMPLE_BYTES`, plugin mode on non-zip, unknown mode, timeout > `MAX_TIMEOUT`); `409 {run_id}` if an identical `(object, mode, params)` run is `queued|running`; else insert row, `RPUSH wpsandbox:jobs <run_id>`, `202 {run_id}` |
| `GET` | `/api/wpsandbox/<hash64:identifier>` | read access to sample | Runs for the sample, newest first |
| `GET` | `/api/wpsandbox/run/<run_id>` | read access to sample | Single run (used for polling) |
| `PATCH` | `/api/wpsandbox/run/<run_id>` | worker only (`g.auth_user.login == MWDB_WPSANDBOX_WORKER_LOGIN`) | Update `status, started_at, finished_at, error, report_blob_id, sandbox_id` |
| `DELETE` | `/api/wpsandbox/run/<run_id>` | requester or `manage_users` | Only if `queued`: `LREM` from the list, delete row; `409` otherwise |

Duplicate detection compares `params` after normalisation (defaults filled in).

### 5.3 Config (mwdb service)

| Env | Default | Purpose |
|---|---|---|
| `MWDB_WPSANDBOX_REDIS_URL` | `MWDB_REDIS_URI` | queue |
| `MWDB_WPSANDBOX_WORKER_LOGIN` | `wpsandbox-worker` | login allowed to `PATCH` |
| `MWDB_WPSANDBOX_MAX_SAMPLE_BYTES` | `20971520` | 20 MB |
| `MWDB_WPSANDBOX_MAX_TIMEOUT` | `300` | seconds |

On startup the plugin ensures attribute definitions `c2_host`, `dropped_file`, `wp_user_added` exist (same helper pattern as `ensure_normalized_tlsh_definition`).

### 5.4 Frontend tab

Registered via `sampleTabsAfter` as "WP Sandbox".

- **Launcher**: mode selector; webroot fields `path` (default as above), `method`, `query`, `body`; `timeout` (default 120). "Run" disabled with tooltip when the user lacks `adding_blobs`. `409` selects the existing run.
- **Run list**: time, mode, requester, status (spinner for non-terminal), duration. Polls every 3 s while any run is non-terminal; stops otherwise.
- **Report view** for the selected run: collapsible sections with count badges — Summary (derived-attribute chips + truncation notice) → Network → Filesystem (created: content in a code viewer; modified: unified diff) → Database → PHP eval layers → Trigger responses → Raw JSON (link to the blob). Failed runs show `error` and a "Re-run" button.
- All report content is rendered as text. No HTML rendering of any captured body.

## 6. Worker (`docker/plugins/wpsandbox/worker/`)

Own Python package (`wpsandbox-worker`), own Dockerfile, compose service `wpsandbox-worker` in `docker-compose-dev.yml` and `docker-compose-prod.yml`. Dependencies: `redis`, `e2b`, `requests`.

Loop (`WPSANDBOX_CONCURRENCY` threads, default 2):

1. `BLPOP wpsandbox:jobs`.
2. `GET /api/wpsandbox/run/<id>`; skip if not `queued`. `GET /api/file/<hash>/download`. `PATCH` → `running`, `started_at`.
3. `Sandbox.create(template=WPSANDBOX_TEMPLATE, timeout=params.timeout + 120)`; record `sandbox_id`; `files.write("/tmp/sample", bytes)`; `commands.run("harness run …", timeout=params.timeout + 90)`; parse stdout. On any exception, attempt `files.read("/var/log/harness/report.json")` for a partial report. `sandbox.kill()` in `finally`.
4. `POST /api/blob` with `parent=<hash>`, `blob_name="wp-sandbox-report.json"`, `blob_type="wp-sandbox-report"`, content = report JSON, shared with the parent's groups.
5. For each `filesystem.created[]` entry whose content was truncated, download it via `commands.run("cat …")`/`files.read` and `POST /api/file` as a child of the sample (also shared with the parent's groups).
6. Attributes on the sample: `c2_host` for each distinct host in `network.dns[].name ∪ network.flows[].url host`; `dropped_file` for each `filesystem.created[].sha256`; `wp_user_added` for each `database.users.added[].login`.
7. `PATCH` → `done` (`report_blob_id`, `finished_at`), or `timeout` (partial report uploaded if any), or `failed` (`error`).

Error policy:
- MWDB HTTP errors: retry once immediately; then `failed` with status + body head.
- SecEx unreachable / no capacity: exponential backoff (1 s → 60 s), `LPUSH` the job back at the head, run stays `queued`.
- Harness crash: `failed`, `error` = last 2 KB of stderr.
- Sample hangs: harness timeout + worker outer cap → `timeout`; partial report if available.
- Worker death: no recovery in v1 (surfaces as "worker lost").

Config: `E2B_API_KEY`, `E2B_API_URL`, `E2B_PROXY` (optional), `WPSANDBOX_TEMPLATE=wordpress`, `WPSANDBOX_MWDB_URL`, `WPSANDBOX_MWDB_API_KEY`, `WPSANDBOX_REDIS_URL`, `WPSANDBOX_CONCURRENCY=2`, `WPSANDBOX_EGRESS_BLOCKLIST` (CIDRs), `WPSANDBOX_FAKE_SANDBOX=0|1` (tests).

Logs: structured JSON lines with `run_id`, `sandbox_id`, `phase`, `duration_ms`.

## 7. Report schema (v1)

```json
{
  "schema_version": 1,
  "run": { "mode": "webroot", "params": {}, "template": "wordpress@<build-id>",
           "wp_version": "", "php_version": "", "started_at": "", "duration_s": 0 },
  "trigger": { "requests": [ { "url": "", "method": "", "status": 0, "response_head": "", "elapsed_ms": 0 } ] },
  "filesystem": {
    "created":  [ { "path": "", "size": 0, "mode": "0644", "sha256": "", "content": null } ],
    "modified": [ { "path": "", "sha256_before": "", "sha256_after": "", "diff": "" } ],
    "deleted":  [ { "path": "" } ],
    "events":   [ { "ts": "", "op": "open|write|unlink|chmod", "path": "", "pid": 0 } ]
  },
  "database": {
    "users":   { "added": [], "modified": [] },
    "options": { "added": [], "modified": [ { "name": "", "before": "", "after": "" } ] },
    "cron":    { "added": [] },
    "posts":   { "added": [], "modified": [] },
    "other_tables_changed": []
  },
  "network": {
    "dns":   [ { "ts": "", "name": "", "answers": [] } ],
    "flows": [ { "ts": "", "method": "", "url": "", "status": 0,
                 "request_headers": {}, "request_body": "", "response_headers": {},
                 "response_body": "", "response_sha256": "" } ]
  },
  "php": { "eval_layers": [ { "depth": 1, "sha256": "", "code": "" } ], "errors": [] },
  "truncated": []
}
```

`content` is `null` when the file exceeded the inline cap (the file is then uploaded as a child `File`). Paths are relative to `/var/www/html`.

## 8. Security

- Single-use VM, killed in `finally`; template snapshot is read-only.
- Worker MWDB user is in a dedicated `wpsandbox` group with capabilities `adding_files`, `adding_blobs`, `adding_attributes` only. Artefacts are shared with the parent sample's groups, nothing more.
- Real egress is an accepted risk (decided 2026-08-26). Mitigations: CIDR blocklist for internal ranges, SMTP ports blocked, everything else logged through mitmproxy.
- Report content is untrusted: text-only rendering in the UI; no `dangerouslySetInnerHTML`.
- `PATCH` is restricted by login, not by capability, so no ordinary user can forge run state.

## 9. Testing

1. **Harness unit tests** (`template/tests/`): manifest diff, DB dump diff, flow/eval/dns log parsing, truncation bookkeeping, plugin slug derivation. Fixtures are small captured dumps.
2. **Plugin resource tests** (`docker/plugins/wpsandbox/tests/`, same conftest pattern as `phpdeobf`): validation (`400`s), `409` duplicate, Redis push via `fakeredis`, `GET`/`PATCH`/`DELETE` transitions, worker-only `PATCH` auth, "worker lost" derivation.
3. **Worker unit tests** (`worker/tests/`): fake `Sandbox` with scripted `commands.run`; MWDB mocked with `responses`. Happy path; partial report on timeout; re-queue on SecEx outage; `kill()` always called; attribute extraction; dropped-file upload.
4. **E2E** (`tests/backend/test_wpsandbox.py`, running stack, worker with `WPSANDBOX_FAKE_SANDBOX=1` returning a canned report): upload → run → poll → child blob + attributes + dropped `File`. `tests/backend/test_wpsandbox_live.py` (skipped unless `E2B_API_KEY` set): one real run of a benign PHP sample that echoes, `curl`s a known URL and touches a file; asserts every report section is non-empty. This is the only check that the template's instrumentation actually works.
5. **Frontend** (`jest`): report section components — count badges, truncation notice, empty states — against the canned report fixture.

## 10. Deployment notes

- New compose services: `wpsandbox-worker` (dev + prod). Add `wpsandbox` to `MWDB_PLUGINS` in both compose files.
- One-time: build the template (`cd docker/plugins/wpsandbox/template && uv run wordpress.py`) against SecEx; create the `wpsandbox-worker` MWDB user + group + API key; set worker env.
- Verify SecEx reachability from the droplet before choosing worker placement. Document in `deploy/DEPLOYMENT.md`.
