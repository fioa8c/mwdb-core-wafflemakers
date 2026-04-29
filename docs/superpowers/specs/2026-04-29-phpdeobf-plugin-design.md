# PHP Deobfuscator Plugin — Design

**Status:** ready for implementation planning
**Date:** 2026-04-29
**Fork:** `mwdb-core-wafflemakers`
**Related:** subsystem 3 spec from 2026-04-19 covered batch normalization with a *different* tool (`waffle-makers-tooling/security-research-malware-sandbox/`). This spec is unrelated: it integrates `~/WORK/PHPDeobfuscator/` (a more developed AST deobfuscator using PHP-Parser v4) as an *interactive*, per-sample plugin.

## 1. Goal

Add a "Deobfuscate" tab to the MWDB sample detail page that runs the current sample through `~/WORK/PHPDeobfuscator/` and creates a `TextBlob` child of the sample containing the deobfuscated PHP source. Output is captured as a first-class MWDB artifact so it can be tagged, shared, searched, and used as a parent for further analysis.

## 2. Non-goals

- Async / queue-based execution. Sync only; the deobfuscator is sub-second to a few seconds for realistic web-malware samples.
- Diff view between original and deobfuscated source. The blob viewer is enough.
- Multiple deobfuscator versions / profiles. One pipeline, one button.
- Attributes summarising what was reduced. The child blob is the artifact.
- Auto-routing of the new blob through Karton. Manual for now; can wire later.
- Production deployment of the sidecar. Dev compose only; prod is a separate decision.

## 3. Architecture

Two new pieces, mirroring the existing `yarax_regex` plugin:

1. **`phpdeobf` sidecar service** — a Docker container running `~/WORK/PHPDeobfuscator/` with a thin HTTP wrapper.
2. **`phpdeobf` MWDB plugin** at `docker/plugins/phpdeobf/` — backend resource + frontend tab.

```
        ┌──────────────────┐    POST /api/phpdeobf/<sha>      ┌─────────────────┐
        │  React FE        │ ───────────────────────────────▶│  MWDB backend   │
        │  PhpDeobfTab     │ ◀───────────────────────────────│  resource.py    │
        └──────────────────┘  {status, blob_id, created, …}  └────────┬────────┘
                                                                      │ POST /deobfuscate
                                                                      ▼
                                                           ┌──────────────────────┐
                                                           │  phpdeobf sidecar    │
                                                           │  PHP 8.5 + server.php │
                                                           │  (vendored from      │
                                                           │   ~/WORK/PHPDeobf…)  │
                                                           └──────────────────────┘
```

The sidecar URL is a config knob: env var `MWDB_PHPDEOBF_URL`, default `http://phpdeobf:8080`.

## 4. Sidecar HTTP wrapper

Lives inside the PHPDeobfuscator project itself — keeps the deobfuscator self-contained and reusable outside MWDB.

### 4.1 Files added to PHPDeobfuscator

- `server.php` — JSON-in / JSON-out wrapper around the existing `deobfuscate()` flow from `index.php`. No framework; uses PHP's built-in HTTP server.
- `Dockerfile.server` — extends/replaces the existing `Dockerfile`'s `CMD` to `["php", "-S", "0.0.0.0:8080", "server.php"]`. Composer install unchanged.

### 4.2 Endpoint

```
POST /deobfuscate
Content-Type: application/json
Body: {
  "source":   "<php source>",       # required
  "filename": "input.php"           # optional; passed to setCurrentFilename
}

200 OK  {"status": "ok",    "output": "<deobfuscated>", "elapsed_ms": N}
200 OK  {"status": "error", "code": "<code>", "message": "..."}
```

Error `code` values:
- `parse_error` — `PhpParser\Error` thrown during parse.
- `input_too_large` — `source` exceeds `MAX_INPUT_BYTES` (default 5 MB).
- `internal_error` — any other unhandled exception. Full stack goes to the sidecar log.

200-with-error mirrors the `yarax_regex` convention: the request was well-formed, the *content* was the problem. Genuine HTTP 5xx / connection failures are reserved for "the sidecar is down," which the plugin surfaces as a separate "backend unavailable" UI state.

The sidecar does not enforce a wall-clock timeout — that lives in the plugin (§5.4).

### 4.3 Configuration

Single env var, `MAX_INPUT_BYTES` (default `5242880` = 5 MB). No other config — keep this simple.

## 5. MWDB plugin backend

### 5.1 Layout

`docker/plugins/phpdeobf/`, mirroring `docker/plugins/yarax_regex/`:

```
__init__.py       # entrypoint registers the resource
resource.py       # POST /api/phpdeobf/<sample_id>
client.py         # urllib/requests wrapper around the sidecar (testable in isolation)
pyproject.toml    # pinned deps (requests)
package.json      # FE plugin metadata
index.tsx         # registers the sample tab via sampleTabsAfter
api.ts            # typed fetch helper
components/       # PhpDeobfTab + sub-components
tests/            # unit tests for client.py + resource.py (sidecar mocked)
README.md         # config / deployment notes
```

### 5.2 Endpoint

```
POST /api/phpdeobf/<sample_id>
(no body — sample_id is the only input; everything else is server-side)

200  {"status": "ok",    "blob_id": "<sha256>", "created": true|false, "elapsed_ms": N}
200  {"status": "error", "code": "parse_error" | "input_too_large" | "internal_error", "message": "..."}
404  {"message": "Sample not found or you don't have access"}
413  {"message": "Sample exceeds maximum size of 5 MB"}
503  {"message": "PHP deobfuscator backend unavailable"}
```

`created: false` indicates the dedupe path (§5.5) hit — same content, blob already attached to this sample.

### 5.3 Resource flow (`resource.py`)

In order, on every request:

1. `requires_authorization` — same auth posture as `yarax_regex`.
2. `File.access(sample_id)` → 404 if missing or not authorised.
3. Reject `file_size > 5 MB` with HTTP 413 (defense-in-depth — sidecar also caps).
4. Read sample bytes. Decode as UTF-8 with `errors="replace"` for transport to the sidecar JSON body. (Non-UTF-8 PHP source is rare but legal; the deobfuscator reads raw today, and the round-trip through replacement is acceptable for the v1 scope. If we hit a real sample where this matters, switch the sidecar to `application/octet-stream` for the body.)
5. POST to sidecar with 30 s wall-clock timeout. Connection error / 5xx / timeout → return HTTP 503.
6. Sidecar `status: "error"` → echo `{status, code, message}` straight back at HTTP 200. Frontend renders inline.
7. Sidecar `status: "ok"` → see §5.5 (dedupe + create).
8. Log: `phpdeobf eval sample=<id> elapsed_ms=<N> status=<status> created=<bool>` — parallels the existing `yarax_regex` log line.

### 5.4 Sidecar client (`client.py`)

Thin wrapper around `requests` (or `urllib.request` if we want to avoid pulling `requests` into the plugin). Single function:

```python
def deobfuscate(source: bytes | str, *, filename: str = "input.php",
                base_url: str, timeout: float = 30.0) -> SidecarResult:
    ...
```

Returns a typed result with one of three shapes: `ok`, `error`, or `unavailable`. Exceptions are converted into `unavailable` here so the resource layer doesn't have to think about transport-level failures.

This is the unit under test for most of the backend test suite — the resource layer just glues it to Flask.

### 5.5 Dedupe + create

After a successful sidecar call, before creating a new blob:

1. Compute the output content's SHA256.
2. Look up existing `TextBlob`s with that content hash that are children of the current sample.
   - MWDB's `TextBlob` model already deduplicates by content hash globally (one row per unique content). The check we need is whether the parent-child relation already exists.
3. If found → return its id with `created: false`.
4. Otherwise → create a `TextBlob` with `blob_type="deobfuscated-php"`, attach as a child of the sample, return its id with `created: true`.

The `blob_type` value is canonical for this plugin and lives in a constant in `resource.py`. No new DB index is required: the existing `Object.children` traversal is plenty fast for typical fan-out (a sample has on the order of single-digit child blobs).

## 6. MWDB plugin frontend

`PhpDeobfTab` mounted via `sampleTabsAfter`, same registration pattern as `yarax_regex/index.tsx`.

### 6.1 State machine

```
idle ──click──▶ running ──ok──▶ done(blob_id, created, elapsed_ms)
                          ──err─▶ error(code, message)
                          ──503─▶ unavailable
done / error ──"Run again"──▶ running
```

Refresh resets to `idle`. The persistent artifact is the child blob; the tab itself is just the action surface.

### 6.2 Layout

Single-page tab with three regions, all states share the same skeleton:

- **Header strip** — PHP-detection note. Heuristic: `<?php` or `<?` in the first 2 KB of the sample, OR filename ending in `.php` (case-insensitive). When the heuristic fails, render a yellow "This sample doesn't look like PHP" line and require the user to tick a "Run anyway" checkbox before the button enables. When it passes, the checkbox is hidden and the button is enabled.
- **Primary button** — "Deobfuscate" (or "Run again" once we have a result).
- **Result area** — varies by state:
  - **idle** → empty.
  - **running** → spinner + "Running deobfuscator…".
  - **done(created=true)** → green box: "Created child blob `<short hash>`" with a link to `/blob/<hash>`. Show `elapsed_ms`.
  - **done(created=false)** → green box: "Already deobfuscated — opening existing child blob." with the same link.
  - **error** → red box: small label = `code`, body = `message` verbatim from sidecar.
  - **unavailable** → red box: "PHP deobfuscator backend unavailable. Please contact your administrator." (matches the `yarax_regex` 503 banner phrasing.)

### 6.3 Frontend-side guards

To avoid pointless round trips:

- Disable the button with an inline reason if the sample's `file_size > 5 MB`.
- Disable the button while a request is in flight.

The sample bytes for the heuristic check come from `api.downloadFile(sampleId, 1)`, same as `yarax_regex` does it.

### 6.4 No state persistence

No localStorage, no recall-on-mount of past runs. v1 ships without it.

## 7. Error handling — consolidated

| Failure | Where caught | User-visible result |
|---|---|---|
| Sample missing / no access | resource (`File.access`) | HTTP 404; FE shows MWDB's normal 404 |
| Sample > 5 MB | resource pre-check + sidecar (defense-in-depth) | HTTP 413 + FE inline message; button also disabled |
| PHP source fails to parse | sidecar → 200 `error/parse_error` | FE error box with parser message |
| Deobfuscator throws unexpectedly | sidecar try/catch → 200 `error/internal_error` | FE error box; full stack in sidecar log |
| Sidecar 5xx / unreachable / 30 s timeout | resource client → 503 | FE "backend unavailable" banner |
| User clicks twice fast | FE disables button while in-flight; backend dedupes anyway | Second click is a no-op (FE) or returns the same blob (BE) |

## 8. Testing

### 8.1 Sidecar (PHP)

Add fixtures under `tests/server/` exercising the JSON wrapper end-to-end: happy path, `parse_error`, `input_too_large`. Reuse the existing `php test.php` runner pattern; add a small harness that boots `server.php` on a high port, curls it, asserts JSON shape.

### 8.2 Plugin backend (Python)

Unit tests in `docker/plugins/phpdeobf/tests/` mirroring `yarax_regex/tests/`. Mock the sidecar HTTP client (`client.py`) so tests don't need a running container. Coverage:

- 404 on unknown sample id.
- 413 on oversized sample.
- Sidecar `error/parse_error` → 200 pass-through.
- Sidecar transport failure → 503.
- Dedupe: `created: true` on first call, `created: false` on second call with identical output.

### 8.3 Plugin frontend

No unit tests for v1. This matches the `yarax_regex` precedent — its FE is also untested. Manual verification through the dev compose stack covers idle → running → done, error, unavailable, and the non-PHP heuristic.

### 8.4 Backend e2e

One test under `tests/backend/test_phpdeobf.py`:

1. Upload a known obfuscated PHP sample (e.g. one of `~/WORK/PHPDeobfuscator/samples/*.php`).
2. POST `/api/phpdeobf/<id>`.
3. Assert response is `status: "ok", created: true`.
4. Assert a child `TextBlob` with `blob_type="deobfuscated-php"` exists under the sample.
5. POST again, assert `created: false` and same `blob_id`.

Skipped unless the sidecar is reachable (env-var skip pattern; matches existing test conventions in `tests/backend/`).

## 9. Deployment / configuration

### 9.1 Vendoring the deobfuscator

The PHPDeobfuscator source is **vendored** under `docker/phpdeobf/` in this repo so the dev compose build context is reproducible. A small `docker/phpdeobf/sync-from-source.sh` script refreshes the vendored copy from `~/WORK/PHPDeobfuscator/` when needed. This trades a manual sync step for not depending on a sibling working directory. (Alternative considered: relative build context pointing at `~/WORK/PHPDeobfuscator/` — rejected because it makes the compose file non-portable.)

> **Two `phpdeobf` paths, on purpose:**
> - `docker/plugins/phpdeobf/` — the **MWDB plugin** (Python + TS), loaded by `core/plugins.py` like every other plugin.
> - `docker/phpdeobf/` — the **vendored deobfuscator source** (PHP), used as the sidecar's Docker build context. Not loaded as a plugin.
>
> They are independent and live side-by-side under `docker/`.

### 9.2 Compose service

New service in `docker-compose-dev.yml`:

```yaml
phpdeobf:
  build:
    context: ./docker/phpdeobf
    dockerfile: Dockerfile.server
  networks:
    default:
  # not exposed on host
  restart: unless-stopped
```

The `mwdb` service gets `MWDB_PHPDEOBF_URL=http://phpdeobf:8080` added to its env.

### 9.3 Env vars

| Var | Default | Where used |
|---|---|---|
| `MWDB_PHPDEOBF_URL` | `http://phpdeobf:8080` | plugin backend `client.py` |
| `MAX_INPUT_BYTES` | `5242880` (5 MB) | sidecar `server.php` |

Documented in `docker/plugins/phpdeobf/README.md`.

### 9.4 Production

Out of scope for this design. Production deployment will need its own pass — likely a separate compose / k8s manifest, possibly behind the existing Traefik with rate limiting (the `sandbox.wafflemakers.xyz` deployment is precedent).

## 10. Risks / open questions

- **Non-UTF-8 PHP source.** v1 routes the body through JSON with replacement decoding. If a real sample loses information through this round trip we'll switch to `application/octet-stream`. Defer until we see it.
- **PHPDeobfuscator memory_limit (512 MB).** Inherited from `index.php`. Keep as-is; the 5 MB input cap keeps us well below this in practice.
- **Sidecar single-process built-in server.** PHP's `-S` is single-threaded; concurrent clicks queue serially. For interactive use this is fine. If we ever batch from a script, swap in php-fpm + nginx.
- **Vendored copy drift.** The sync script is manual, so the vendored copy may lag the source. Mitigated by a CI job (out of scope here) or simply by intent — the sidecar is small enough that drift is visible during PR review.
