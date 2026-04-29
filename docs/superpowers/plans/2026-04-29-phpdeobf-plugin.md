# PHP Deobfuscator Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Deobfuscate" tab on the MWDB sample detail page that runs the sample through `~/WORK/PHPDeobfuscator/` (sidecar) and creates a `TextBlob` child of the sample with `blob_type="deobfuscated-php"`.

**Architecture:** Sidecar HTTP service (PHP container, vendored from `~/WORK/PHPDeobfuscator/`, exposes `POST /deobfuscate`) + MWDB plugin under `docker/plugins/phpdeobf/` (Flask resource `POST /api/phpdeobf/<sample_id>` + React tab via `sampleTabsAfter`). Sync invocation, dedupe via `TextBlob.get_or_create(parent=sample)`. Mirrors the existing `yarax_regex` plugin shape.

**Tech Stack:** Python 3.10+ / Flask-RESTful / SQLAlchemy 1.4 (plugin backend); React + TypeScript (plugin FE); PHP 8.5 + nikic/PHP-Parser v4 (sidecar); Docker Compose for dev.

**Spec:** `docs/superpowers/specs/2026-04-29-phpdeobf-plugin-design.md`

---

## File Structure

**New / vendored:**
- `docker/phpdeobf/` — vendored copy of `~/WORK/PHPDeobfuscator/` (PHP source); built as the sidecar image.
  - `docker/phpdeobf/server.php` — JSON-in/JSON-out HTTP wrapper.
  - `docker/phpdeobf/Dockerfile.server` — sidecar container.
  - `docker/phpdeobf/tests/server_test.php` — PHP-side test harness for the wrapper.
  - `docker/phpdeobf/sync-from-source.sh` — helper to refresh from `~/WORK/PHPDeobfuscator/`.

- `docker/plugins/phpdeobf/` — MWDB plugin.
  - `__init__.py` — entrypoint, registers resource.
  - `resource.py` — `POST /api/phpdeobf/<sample_id>`.
  - `client.py` — typed sidecar HTTP client (testable in isolation).
  - `pyproject.toml`, `package.json`.
  - `index.tsx` — registers tab via `sampleTabsAfter`.
  - `api.ts` — typed FE fetch helper.
  - `components/PhpDeobfTab.tsx` — main tab UI.
  - `tests/conftest.py`, `tests/test_client.py`, `tests/test_resource.py`.
  - `README.md` — config / deployment notes.

- `tests/backend/test_phpdeobf.py` — end-to-end test (HTTP against running stack).

**Modified:**
- `docker-compose-dev.yml` — add `phpdeobf` service; add `MWDB_PHPDEOBF_URL` to `mwdb` env; add `phpdeobf` to `MWDB_PLUGINS`.

---

## Task 1: Sidecar — `server.php` JSON wrapper

**Files:**
- Create: `docker/phpdeobf/server.php`
- Test: `docker/phpdeobf/tests/server_test.php`

The sidecar wraps the existing `deobfuscate()` flow from `index.php` in a JSON HTTP endpoint. Empty regex / non-PHP / parser failures map to `200 + status:"error"`; unexpected exceptions to `internal_error`; oversized input to `input_too_large`. PHP source bigger than `MAX_INPUT_BYTES` (default 5 MB) is rejected before parsing.

The test harness boots `php -S` on a high port, curls it, asserts JSON shape. No PHPUnit — the existing `php test.php` runner pattern in this repo uses plain PHP, and we follow that.

- [ ] **Step 1.1: Vendor the PHPDeobfuscator source first (prerequisite for `server.php` to find the autoloader)**

```bash
mkdir -p docker/phpdeobf
rsync -a --delete \
  --exclude='.git' --exclude='vendor' --exclude='samples' --exclude='docs' \
  ~/WORK/PHPDeobfuscator/ docker/phpdeobf/
```

Expected: `docker/phpdeobf/composer.json`, `docker/phpdeobf/index.php`, `docker/phpdeobf/src/`, etc. now exist. `vendor/` is excluded — it gets installed inside the container during build, not vendored into git.

- [ ] **Step 1.2: Add `.gitignore` for sidecar build artifacts**

Create `docker/phpdeobf/.gitignore`:

```
vendor/
composer.lock.bak
```

(Keep `composer.lock` in source control so the sidecar build is reproducible — that file IS in the upstream PHPDeobfuscator repo.)

- [ ] **Step 1.3: Write the sidecar test harness**

Create `docker/phpdeobf/tests/server_test.php`:

```php
<?php
/**
 * Sidecar HTTP test harness.
 *
 * Boots `php -S 127.0.0.1:<port> server.php` from the project root,
 * waits for it to come up, hits each endpoint, asserts the JSON shape,
 * tears down the server. Exit code 0 = pass, non-zero = fail.
 *
 * Run: php docker/phpdeobf/tests/server_test.php
 *      (from the repo root, OR from docker/phpdeobf/)
 */

$rootDir = realpath(__DIR__ . '/..');
chdir($rootDir);

$port = 18080;
$pid = null;

function start_server($port) {
    // Use proc_open so we can capture and later kill the PID.
    $cmd = sprintf('php -S 127.0.0.1:%d server.php > /tmp/phpdeobf-sidecar-test.log 2>&1 & echo $!', $port);
    $pid = (int) trim(shell_exec($cmd));
    // Poll for readiness up to 5s.
    $started = microtime(true);
    while (microtime(true) - $started < 5.0) {
        $sock = @fsockopen('127.0.0.1', $port, $errno, $errstr, 0.2);
        if ($sock) {
            fclose($sock);
            return $pid;
        }
        usleep(100_000);
    }
    fwrite(STDERR, "server failed to start in 5s\n");
    fwrite(STDERR, file_get_contents('/tmp/phpdeobf-sidecar-test.log'));
    posix_kill($pid, SIGTERM);
    exit(1);
}

function stop_server($pid) {
    if ($pid) {
        posix_kill($pid, SIGTERM);
    }
}

function post_json($port, $path, $body) {
    $ch = curl_init("http://127.0.0.1:$port$path");
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST => true,
        CURLOPT_HTTPHEADER => ['Content-Type: application/json'],
        CURLOPT_POSTFIELDS => json_encode($body),
        CURLOPT_TIMEOUT => 10,
    ]);
    $resp = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    return [$code, json_decode($resp, true)];
}

function assert_eq($actual, $expected, $label) {
    if ($actual !== $expected) {
        fwrite(STDERR, "FAIL: $label\n  expected: " . var_export($expected, true) . "\n  got:      " . var_export($actual, true) . "\n");
        exit(1);
    }
    echo "ok: $label\n";
}

$pid = start_server($port);

try {
    // Happy path — trivial PHP, just round-trips through PrettyPrinter.
    [$code, $body] = post_json($port, '/deobfuscate', [
        'source' => "<?php echo 1 + 2;",
    ]);
    assert_eq($code, 200, 'happy path: 200');
    assert_eq($body['status'], 'ok', 'happy path: status=ok');
    if (!isset($body['output']) || !is_string($body['output'])) {
        fwrite(STDERR, "FAIL: happy path: output missing or not a string\n");
        exit(1);
    }
    if (!isset($body['elapsed_ms']) || !is_int($body['elapsed_ms'])) {
        fwrite(STDERR, "FAIL: happy path: elapsed_ms missing or not int\n");
        exit(1);
    }
    echo "ok: happy path: output and elapsed_ms present\n";

    // Parse error — malformed PHP.
    [$code, $body] = post_json($port, '/deobfuscate', [
        'source' => "<?php this is not <<<< valid",
    ]);
    assert_eq($code, 200, 'parse error: 200');
    assert_eq($body['status'], 'error', 'parse error: status=error');
    assert_eq($body['code'], 'parse_error', 'parse error: code=parse_error');

    // Input too large — bigger than the 5 MB default cap.
    $bigSource = "<?php\n" . str_repeat("\$x = 1;\n", 1_000_000);  // ~8 MB
    [$code, $body] = post_json($port, '/deobfuscate', [
        'source' => $bigSource,
    ]);
    assert_eq($code, 200, 'too large: 200');
    assert_eq($body['status'], 'error', 'too large: status=error');
    assert_eq($body['code'], 'input_too_large', 'too large: code=input_too_large');

    echo "\nALL PASSED\n";
} finally {
    stop_server($pid);
}
```

- [ ] **Step 1.4: Run the test to verify it fails**

```bash
php docker/phpdeobf/tests/server_test.php
```

Expected: FAIL — `server.php` doesn't exist yet, server fails to start.

- [ ] **Step 1.5: Install composer deps so the autoloader exists**

```bash
cd docker/phpdeobf && composer install && cd ../..
```

Expected: `docker/phpdeobf/vendor/autoload.php` exists.

- [ ] **Step 1.6: Write `server.php`**

Create `docker/phpdeobf/server.php`:

```php
<?php
/**
 * HTTP sidecar wrapper around the PHPDeobfuscator pipeline.
 *
 * Endpoint:
 *   POST /deobfuscate
 *   Content-Type: application/json
 *   Body: {"source": "<php source>", "filename": "input.php"}
 *
 *   200 {"status":"ok","output":"...","elapsed_ms":N}
 *   200 {"status":"error","code":"parse_error|input_too_large|internal_error","message":"..."}
 *
 * Designed for `php -S` (single-process built-in server). Concurrent
 * requests serialise — fine for interactive use.
 */

require __DIR__ . '/vendor/autoload.php';

ini_set('xdebug.var_display_max_depth', -1);
ini_set('memory_limit', '512M');
ini_set('xdebug.max_nesting_level', 1000);

$MAX_INPUT_BYTES = (int) (getenv('MAX_INPUT_BYTES') ?: 5 * 1024 * 1024);

function emit($status, $payload) {
    http_response_code($status);
    header('Content-Type: application/json');
    echo json_encode($payload);
    exit;
}

function emit_error($code, $message) {
    emit(200, ['status' => 'error', 'code' => $code, 'message' => $message]);
}

// Only POST /deobfuscate is supported. Anything else → 404.
if ($_SERVER['REQUEST_METHOD'] !== 'POST' || $_SERVER['REQUEST_URI'] !== '/deobfuscate') {
    emit(404, ['status' => 'error', 'code' => 'not_found', 'message' => 'unknown route']);
}

$raw = file_get_contents('php://input');
$body = json_decode($raw, true);
if (!is_array($body)) {
    emit(400, ['status' => 'error', 'code' => 'bad_request', 'message' => 'body must be a JSON object']);
}

$source = $body['source'] ?? null;
$filename = $body['filename'] ?? 'input.php';
if (!is_string($source)) {
    emit(400, ['status' => 'error', 'code' => 'bad_request', 'message' => 'source is required and must be a string']);
}
if (strlen($source) > $MAX_INPUT_BYTES) {
    emit_error('input_too_large', sprintf(
        'source is %d bytes; cap is %d',
        strlen($source),
        $MAX_INPUT_BYTES
    ));
}
if (!is_string($filename) || $filename === '') {
    $filename = 'input.php';
}

$started = microtime(true);

try {
    $deobf = new \PHPDeobfuscator\Deobfuscator(false);
    $virtualPath = '/var/www/html/' . basename($filename);
    $deobf->getFilesystem()->write($virtualPath, $source);
    $deobf->setCurrentFilename($virtualPath);
    $tree = $deobf->parse($source);
    $tree = $deobf->deobfuscate($tree);
    $output = $deobf->prettyPrint($tree);
} catch (\PhpParser\Error $e) {
    emit_error('parse_error', $e->getMessage());
} catch (\Throwable $e) {
    error_log('phpdeobf internal_error: ' . $e->getMessage() . "\n" . $e->getTraceAsString());
    emit_error('internal_error', $e->getMessage());
}

$elapsedMs = (int) round((microtime(true) - $started) * 1000);

emit(200, [
    'status' => 'ok',
    'output' => $output,
    'elapsed_ms' => $elapsedMs,
]);
```

- [ ] **Step 1.7: Run the test, expect it to pass**

```bash
php docker/phpdeobf/tests/server_test.php
```

Expected: `ALL PASSED`.

- [ ] **Step 1.8: Commit**

```bash
git add docker/phpdeobf/
git commit -m "Plugin phpdeobf: vendor deobfuscator + add HTTP sidecar wrapper"
```

---

## Task 2: Sidecar — Dockerfile.server

**Files:**
- Create: `docker/phpdeobf/Dockerfile.server`

The existing `Dockerfile` runs `php index.php` (CLI). We need an alternative `Dockerfile.server` whose `CMD` boots `php -S 0.0.0.0:8080 server.php`. We keep the existing `Dockerfile` as-is so the deobfuscator's CLI use case isn't disturbed.

- [ ] **Step 2.1: Write `Dockerfile.server`**

Create `docker/phpdeobf/Dockerfile.server`:

```dockerfile
FROM php:8.5-cli-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer

COPY . /app
WORKDIR /app

RUN composer install --no-dev --optimize-autoloader

EXPOSE 8080
CMD ["php", "-S", "0.0.0.0:8080", "server.php"]
```

- [ ] **Step 2.2: Build the image and smoke-test it**

```bash
docker build -f docker/phpdeobf/Dockerfile.server -t phpdeobf-sidecar:test docker/phpdeobf/
docker run --rm -d --name phpdeobf-smoke -p 18080:8080 phpdeobf-sidecar:test
sleep 2
curl -s -X POST http://127.0.0.1:18080/deobfuscate \
  -H 'Content-Type: application/json' \
  -d '{"source":"<?php echo 1+2;"}'
docker stop phpdeobf-smoke
```

Expected output from the curl: `{"status":"ok","output":"<?php\n\necho 3;","elapsed_ms":<small int>}` (whitespace may vary; the contract is `status=ok` and `output` is a string).

- [ ] **Step 2.3: Commit**

```bash
git add docker/phpdeobf/Dockerfile.server
git commit -m "Plugin phpdeobf: add sidecar Dockerfile (php -S server.php)"
```

---

## Task 3: Sync helper script

**Files:**
- Create: `docker/phpdeobf/sync-from-source.sh`

The vendored copy under `docker/phpdeobf/` will drift from `~/WORK/PHPDeobfuscator/`. A simple bash script makes refresh explicit.

- [ ] **Step 3.1: Write `sync-from-source.sh`**

Create `docker/phpdeobf/sync-from-source.sh`:

```bash
#!/usr/bin/env bash
# Refresh the vendored PHPDeobfuscator source from ~/WORK/PHPDeobfuscator/.
#
# Run from repo root:  bash docker/phpdeobf/sync-from-source.sh
# Run from anywhere:   <repo>/docker/phpdeobf/sync-from-source.sh
#
# After running, review the diff and commit.

set -euo pipefail

SRC="${PHPDEOBF_SRC:-$HOME/WORK/PHPDeobfuscator}"
DST="$(cd "$(dirname "$0")" && pwd)"

if [[ ! -d "$SRC" ]]; then
  echo "source not found: $SRC" >&2
  echo "set PHPDEOBF_SRC=/path/to/PHPDeobfuscator to override" >&2
  exit 1
fi

# Preserve sidecar-only files (server.php, Dockerfile.server, this script,
# tests/, .gitignore) by listing them as excludes — rsync --delete would
# otherwise wipe them.
rsync -a --delete \
  --exclude='.git' --exclude='vendor' --exclude='samples' --exclude='docs' \
  --exclude='server.php' --exclude='Dockerfile.server' \
  --exclude='sync-from-source.sh' --exclude='tests/server_test.php' \
  --exclude='.gitignore' \
  "$SRC/" "$DST/"

echo "synced from $SRC to $DST"
echo "review with: git -C $(git -C "$DST" rev-parse --show-toplevel) diff --stat"
```

- [ ] **Step 3.2: Make it executable**

```bash
chmod +x docker/phpdeobf/sync-from-source.sh
```

- [ ] **Step 3.3: Verify it runs without changes (idempotency check)**

```bash
bash docker/phpdeobf/sync-from-source.sh
git status docker/phpdeobf/
```

Expected: no changes (`working tree clean` for `docker/phpdeobf/`).

- [ ] **Step 3.4: Commit**

```bash
git add docker/phpdeobf/sync-from-source.sh
git commit -m "Plugin phpdeobf: add helper to refresh vendored source"
```

---

## Task 4: Plugin Python scaffolding

**Files:**
- Create: `docker/plugins/phpdeobf/__init__.py`
- Create: `docker/plugins/phpdeobf/pyproject.toml`

Set up the Python plugin package so it can be imported by `core/plugins.py`. Mirrors `docker/plugins/yarax_regex/` exactly.

- [ ] **Step 4.1: Create `__init__.py`**

Create `docker/plugins/phpdeobf/__init__.py`:

```python
"""PHP Deobfuscator plugin — runs the sample through the phpdeobf sidecar
and creates a deobfuscated TextBlob child of the sample."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mwdb.core.plugins import PluginAppContext

__author__ = "Waffle Makers"
__version__ = "0.1.0"
__doc__ = "PHP Deobfuscator plugin for the MWDB sample detail page."


logger = logging.getLogger("mwdb.plugin.phpdeobf")


def entrypoint(app_context: "PluginAppContext") -> None:
    """Plugin entrypoint — registers the Flask resource.

    The resource module import is deferred to avoid a circular import when
    this package is loaded before the main MWDB app finishes booting.
    """
    from .resource import PhpDeobfResource

    app_context.register_resource(
        PhpDeobfResource, "/phpdeobf/<hash64:identifier>"
    )
    logger.info("Registered POST /api/phpdeobf/<sample_id>")


__plugin_entrypoint__ = entrypoint
```

- [ ] **Step 4.2: Create `pyproject.toml`**

Create `docker/plugins/phpdeobf/pyproject.toml`:

```toml
[project]
name = "phpdeobf"
version = "0.1.0"
description = "PHP Deobfuscator plugin for MWDB"
requires-python = ">=3.10"
dependencies = [
    "requests>=2.31",
]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["phpdeobf"]
package-dir = {"phpdeobf" = "."}
```

- [ ] **Step 4.3: Commit**

```bash
git add docker/plugins/phpdeobf/__init__.py docker/plugins/phpdeobf/pyproject.toml
git commit -m "Plugin phpdeobf: add Python package scaffolding"
```

---

## Task 5: Plugin sidecar client (TDD)

**Files:**
- Create: `docker/plugins/phpdeobf/client.py`
- Create: `docker/plugins/phpdeobf/tests/__init__.py`
- Create: `docker/plugins/phpdeobf/tests/conftest.py`
- Create: `docker/plugins/phpdeobf/tests/test_client.py`

`client.py` is the typed wrapper around `POST <sidecar>/deobfuscate`. It converts transport-level failures (connection refused, 5xx, timeout) into a typed `unavailable` result so the resource layer doesn't have to think about HTTP exceptions. Tests use `responses` (no real HTTP).

- [ ] **Step 5.1: Add `responses` to test dependencies**

Edit `docker/plugins/phpdeobf/pyproject.toml`. Add a `dev` extra:

```toml
[project.optional-dependencies]
dev = ["pytest>=7", "responses>=0.24"]
```

- [ ] **Step 5.2: Write `tests/conftest.py`**

Create `docker/plugins/phpdeobf/tests/__init__.py` (empty file).

Create `docker/plugins/phpdeobf/tests/conftest.py`:

```python
"""Shared pytest fixtures for the phpdeobf plugin.

Adds the plugin package to sys.path so `import phpdeobf...` works without
installing it. Mirrors yarax_regex/tests/conftest.py.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def _identity_decorator(f):
    return f


# Pre-stub mwdb.resources / mwdb.model so the resource module's top-level
# imports succeed without the full MWDB stack (libmagic etc. on macOS hosts).
# test_resource.py monkeypatches these stubs with per-test behaviour.
if "mwdb.resources" not in sys.modules:
    _stub = MagicMock()
    _stub.requires_authorization = _identity_decorator
    sys.modules["mwdb.resources"] = _stub
    import mwdb as _mwdb_pkg
    _mwdb_pkg.resources = _stub

if "mwdb.model" not in sys.modules:
    _stub = MagicMock()
    sys.modules["mwdb.model"] = _stub
    import mwdb as _mwdb_pkg
    _mwdb_pkg.model = _stub
```

- [ ] **Step 5.3: Write the failing client tests**

Create `docker/plugins/phpdeobf/tests/test_client.py`:

```python
"""Unit tests for phpdeobf.client — the sidecar HTTP wrapper."""
import responses

from phpdeobf.client import deobfuscate, OkResult, ErrorResult, UnavailableResult


SIDECAR = "http://phpdeobf:8080"


@responses.activate
def test_ok_response_parsed():
    responses.add(
        responses.POST,
        f"{SIDECAR}/deobfuscate",
        json={"status": "ok", "output": "<?php echo 3;", "elapsed_ms": 12},
        status=200,
    )
    result = deobfuscate("<?php echo 1+2;", base_url=SIDECAR)
    assert isinstance(result, OkResult)
    assert result.output == "<?php echo 3;"
    assert result.elapsed_ms == 12


@responses.activate
def test_error_response_parsed():
    responses.add(
        responses.POST,
        f"{SIDECAR}/deobfuscate",
        json={"status": "error", "code": "parse_error", "message": "bad token"},
        status=200,
    )
    result = deobfuscate("not php", base_url=SIDECAR)
    assert isinstance(result, ErrorResult)
    assert result.code == "parse_error"
    assert result.message == "bad token"


@responses.activate
def test_5xx_returns_unavailable():
    responses.add(
        responses.POST, f"{SIDECAR}/deobfuscate", status=502
    )
    result = deobfuscate("<?php echo 1;", base_url=SIDECAR)
    assert isinstance(result, UnavailableResult)


@responses.activate
def test_connection_error_returns_unavailable():
    # No responses.add() → ConnectionError is raised
    result = deobfuscate("<?php echo 1;", base_url=SIDECAR)
    assert isinstance(result, UnavailableResult)


@responses.activate
def test_timeout_returns_unavailable():
    from requests.exceptions import ReadTimeout

    def raise_timeout(request):
        raise ReadTimeout()

    responses.add_callback(
        responses.POST, f"{SIDECAR}/deobfuscate", callback=raise_timeout
    )
    result = deobfuscate("<?php echo 1;", base_url=SIDECAR, timeout=0.001)
    assert isinstance(result, UnavailableResult)


@responses.activate
def test_filename_passed_through():
    captured = {}

    def callback(request):
        import json as _json
        captured.update(_json.loads(request.body))
        return (200, {}, '{"status":"ok","output":"x","elapsed_ms":1}')

    responses.add_callback(
        responses.POST, f"{SIDECAR}/deobfuscate", callback=callback
    )
    deobfuscate("<?php echo 1;", filename="evil.php", base_url=SIDECAR)
    assert captured["filename"] == "evil.php"
    assert captured["source"] == "<?php echo 1;"
```

- [ ] **Step 5.4: Run tests, expect them to fail**

```bash
cd docker/plugins/phpdeobf && python -m pytest tests/test_client.py -v
```

Expected: ImportError on `phpdeobf.client` (it doesn't exist yet).

- [ ] **Step 5.5: Implement `client.py`**

Create `docker/plugins/phpdeobf/client.py`:

```python
"""HTTP client for the phpdeobf sidecar.

Converts transport failures (connection refused, 5xx, timeout) into a typed
`UnavailableResult` so the resource layer can treat "sidecar down" as a
single state independent of which thing failed.
"""
from dataclasses import dataclass
from typing import Union

import requests

DEFAULT_TIMEOUT = 30.0


@dataclass
class OkResult:
    output: str
    elapsed_ms: int


@dataclass
class ErrorResult:
    code: str
    message: str


@dataclass
class UnavailableResult:
    """Sidecar is unreachable / unhealthy. The cause is logged but not
    surfaced to callers — the user-facing message is the same regardless."""
    detail: str


SidecarResult = Union[OkResult, ErrorResult, UnavailableResult]


def deobfuscate(
    source: str,
    *,
    filename: str = "input.php",
    base_url: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> SidecarResult:
    url = f"{base_url.rstrip('/')}/deobfuscate"
    try:
        resp = requests.post(
            url,
            json={"source": source, "filename": filename},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return UnavailableResult(detail=f"transport error: {exc}")

    if resp.status_code >= 500:
        return UnavailableResult(detail=f"sidecar HTTP {resp.status_code}")

    try:
        body = resp.json()
    except ValueError:
        return UnavailableResult(detail="sidecar returned non-JSON body")

    status = body.get("status")
    if status == "ok":
        return OkResult(
            output=body.get("output", ""),
            elapsed_ms=int(body.get("elapsed_ms", 0)),
        )
    if status == "error":
        return ErrorResult(
            code=str(body.get("code", "unknown")),
            message=str(body.get("message", "")),
        )
    return UnavailableResult(detail=f"unexpected sidecar status: {status!r}")
```

- [ ] **Step 5.6: Run tests, expect them to pass**

```bash
cd docker/plugins/phpdeobf && python -m pytest tests/test_client.py -v
```

Expected: 6 passed.

- [ ] **Step 5.7: Commit**

```bash
git add docker/plugins/phpdeobf/client.py docker/plugins/phpdeobf/tests/
git commit -m "Plugin phpdeobf: add typed sidecar client with unit tests"
```

---

## Task 6: Plugin resource (TDD)

**Files:**
- Create: `docker/plugins/phpdeobf/resource.py`
- Create: `docker/plugins/phpdeobf/tests/test_resource.py`

The Flask resource ties everything together: auth, sample lookup, size cap, sidecar call, dedupe + create blob. Tests use a minimal Flask app with stubbed `File.access` and `TextBlob.get_or_create`, mirroring `yarax_regex`'s `test_resource.py`.

- [ ] **Step 6.1: Write the failing resource tests**

Create `docker/plugins/phpdeobf/tests/test_resource.py`:

```python
"""Integration tests for phpdeobf.resource using a Flask test client.

The MWDB Flask app is heavyweight — we build a minimal Flask app with our
resource registered, stubbing File.access, TextBlob.get_or_create, and the
sidecar client. Mirrors yarax_regex's test_resource.py.
"""
import sys
from unittest.mock import MagicMock

import pytest
from flask import Flask


@pytest.fixture
def app(monkeypatch):
    import functools

    def noop_decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            return f(*args, **kwargs)
        return wrapper

    import mwdb.resources
    monkeypatch.setattr(mwdb.resources, "requires_authorization", noop_decorator)

    fake_file_access = MagicMock()
    monkeypatch.setattr("mwdb.model.File.access", fake_file_access)
    fake_textblob = MagicMock()
    monkeypatch.setattr("mwdb.model.TextBlob", fake_textblob)

    fake_client = MagicMock()
    if "phpdeobf.resource" in sys.modules:
        del sys.modules["phpdeobf.resource"]
    from phpdeobf import resource as resource_mod
    monkeypatch.setattr(resource_mod, "client", fake_client)

    flask_app = Flask(__name__)
    flask_app.add_url_rule(
        "/api/phpdeobf/<identifier>",
        view_func=resource_mod.PhpDeobfResource.as_view("phpdeobf"),
    )
    flask_app.fake_file_access = fake_file_access
    flask_app.fake_textblob = fake_textblob
    flask_app.fake_client = fake_client
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _set_sample(app, sample_bytes: bytes):
    fake_file = MagicMock()
    fake_file.read.return_value = sample_bytes
    fake_file.file_size = len(sample_bytes)
    fake_file.share_3rd_party = False
    app.fake_file_access.return_value = fake_file
    return fake_file


def _set_sidecar_ok(app, output: str, elapsed_ms: int = 5):
    from phpdeobf.client import OkResult
    app.fake_client.deobfuscate.return_value = OkResult(output=output, elapsed_ms=elapsed_ms)


def _set_sidecar_error(app, code: str, message: str):
    from phpdeobf.client import ErrorResult
    app.fake_client.deobfuscate.return_value = ErrorResult(code=code, message=message)


def _set_sidecar_unavailable(app):
    from phpdeobf.client import UnavailableResult
    app.fake_client.deobfuscate.return_value = UnavailableResult(detail="x")


def test_404_when_sample_missing(app, client):
    app.fake_file_access.return_value = None
    resp = client.post("/api/phpdeobf/abc123")
    assert resp.status_code == 404


def test_413_when_sample_too_large(app, client):
    fake_file = _set_sample(app, b"\x00" * 10)
    fake_file.file_size = 10 * 1024 * 1024  # 10 MB > 5 MB cap
    resp = client.post("/api/phpdeobf/abc123")
    assert resp.status_code == 413


def test_503_when_sidecar_unavailable(app, client):
    _set_sample(app, b"<?php echo 1;")
    _set_sidecar_unavailable(app)
    resp = client.post("/api/phpdeobf/abc123")
    assert resp.status_code == 503


def test_sidecar_error_passed_through_at_200(app, client):
    _set_sample(app, b"not really php")
    _set_sidecar_error(app, "parse_error", "syntax error at line 1")
    resp = client.post("/api/phpdeobf/abc123")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "error"
    assert body["code"] == "parse_error"
    assert body["message"] == "syntax error at line 1"


def test_ok_creates_blob_and_returns_id(app, client):
    _set_sample(app, b"<?php echo 1+2;")
    _set_sidecar_ok(app, output="<?php\n\necho 3;", elapsed_ms=7)

    fake_blob = MagicMock()
    fake_blob.dhash = "deadbeef" * 8
    app.fake_textblob.get_or_create.return_value = (fake_blob, True)

    resp = client.post("/api/phpdeobf/abc123")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["blob_id"] == "deadbeef" * 8
    assert body["created"] is True
    assert body["elapsed_ms"] == 7

    # get_or_create called with our content + canonical blob_type
    call_kwargs = app.fake_textblob.get_or_create.call_args.kwargs
    call_args = app.fake_textblob.get_or_create.call_args.args
    # First positional args: (content, blob_name, blob_type, ...)
    assert call_args[0] == "<?php\n\necho 3;"
    assert call_args[2] == "deobfuscated-php"


def test_dedupe_returns_existing_blob_with_created_false(app, client):
    _set_sample(app, b"<?php echo 1+2;")
    _set_sidecar_ok(app, output="<?php\n\necho 3;")

    fake_blob = MagicMock()
    fake_blob.dhash = "cafebabe" * 8
    app.fake_textblob.get_or_create.return_value = (fake_blob, False)

    resp = client.post("/api/phpdeobf/abc123")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["blob_id"] == "cafebabe" * 8
    assert body["created"] is False
```

- [ ] **Step 6.2: Run tests, expect failure**

```bash
cd docker/plugins/phpdeobf && python -m pytest tests/test_resource.py -v
```

Expected: ImportError on `phpdeobf.resource`.

- [ ] **Step 6.3: Implement `resource.py`**

Create `docker/plugins/phpdeobf/resource.py`:

```python
"""Flask-RESTful resource for POST /api/phpdeobf/<sample_id>."""
import os

from flask import jsonify
from werkzeug.exceptions import NotFound, RequestEntityTooLarge, ServiceUnavailable

from mwdb.core.service import Resource
from mwdb.model import File, TextBlob
from mwdb.resources import requires_authorization

from . import logger
from . import client as _client_module

# Re-export under a name the tests can monkeypatch.
client = _client_module

# Maximum sample size — same cap the sidecar enforces. Defense-in-depth.
MAX_SAMPLE_SIZE = 5 * 1024 * 1024  # 5 MB
SIDECAR_TIMEOUT = 30.0
BLOB_TYPE = "deobfuscated-php"


def _sidecar_url() -> str:
    return os.environ.get("MWDB_PHPDEOBF_URL", "http://phpdeobf:8080")


class PhpDeobfResource(Resource):
    @requires_authorization
    def post(self, identifier):
        """
        ---
        summary: Deobfuscate a PHP sample and create a child TextBlob
        description: |
            Sends the sample's bytes to the phpdeobf sidecar; on success
            creates (or reuses) a TextBlob child of this sample with
            blob_type="deobfuscated-php". Sidecar errors are passed
            through at HTTP 200; transport failures map to HTTP 503.
        security:
            - bearerAuth: []
        tags:
            - phpdeobf
        parameters:
            - in: path
              name: identifier
              schema:
                type: string
              required: true
              description: SHA256/MD5/SHA1/SHA512 of the sample
        responses:
            200:
                description: ok with blob_id, OR sidecar error pass-through
            404:
                description: Sample not found or unauthorized
            413:
                description: Sample exceeds 5 MB cap
            503:
                description: Sidecar unavailable
        """
        sample = File.access(identifier)
        if sample is None:
            raise NotFound("Sample not found or you don't have access to it")

        sample_size = getattr(sample, "file_size", None)
        if sample_size is not None and sample_size > MAX_SAMPLE_SIZE:
            raise RequestEntityTooLarge(
                f"Sample exceeds maximum size of {MAX_SAMPLE_SIZE} bytes"
            )

        sample_bytes = sample.read()
        # Decode with replacement: PHP source is *usually* UTF-8 / ASCII;
        # malformed bytes survive the round-trip as U+FFFD which is acceptable
        # for the v1 scope. Spec §10 documents the upgrade path.
        source = sample_bytes.decode("utf-8", errors="replace")

        result = client.deobfuscate(
            source,
            base_url=_sidecar_url(),
            timeout=SIDECAR_TIMEOUT,
        )

        from .client import OkResult, ErrorResult, UnavailableResult

        if isinstance(result, UnavailableResult):
            logger.warning("phpdeobf sidecar unavailable: %s", result.detail)
            raise ServiceUnavailable("PHP deobfuscator backend unavailable")

        if isinstance(result, ErrorResult):
            logger.info(
                "phpdeobf eval sample=%s status=error code=%s",
                identifier,
                result.code,
            )
            return jsonify(
                {
                    "status": "error",
                    "code": result.code,
                    "message": result.message,
                }
            )

        assert isinstance(result, OkResult)

        # Use a stable blob name so the dedupe path actually triggers — same
        # content + same blob_name + same parent → same TextBlob row.
        blob_name = f"deobfuscated-{identifier}.php"
        share_3rd_party = bool(getattr(sample, "share_3rd_party", False))

        blob, is_new = TextBlob.get_or_create(
            result.output,
            blob_name,
            BLOB_TYPE,
            share_3rd_party=share_3rd_party,
            parent=sample,
        )

        logger.info(
            "phpdeobf eval sample=%s elapsed_ms=%d status=ok created=%s",
            identifier,
            result.elapsed_ms,
            is_new,
        )

        return jsonify(
            {
                "status": "ok",
                "blob_id": blob.dhash,
                "created": bool(is_new),
                "elapsed_ms": result.elapsed_ms,
            }
        )
```

- [ ] **Step 6.4: Run tests, expect them to pass**

```bash
cd docker/plugins/phpdeobf && python -m pytest tests/test_resource.py -v
```

Expected: 6 passed.

- [ ] **Step 6.5: Run full plugin test suite**

```bash
cd docker/plugins/phpdeobf && python -m pytest tests/ -v
```

Expected: 12 passed (6 client + 6 resource).

- [ ] **Step 6.6: Commit**

```bash
git add docker/plugins/phpdeobf/resource.py docker/plugins/phpdeobf/tests/test_resource.py
git commit -m "Plugin phpdeobf: add Flask resource + integration tests"
```

---

## Task 7: Plugin frontend — types and API helper

**Files:**
- Create: `docker/plugins/phpdeobf/package.json`
- Create: `docker/plugins/phpdeobf/api.ts`

Sets up the TypeScript fetcher with discriminated union types — same pattern as `yarax_regex/api.ts`.

- [ ] **Step 7.1: Create `package.json`**

Create `docker/plugins/phpdeobf/package.json`:

```json
{
    "name": "@mwdb-web/plugin-phpdeobf",
    "version": "0.1.0",
    "description": "PHP Deobfuscator plugin for the MWDB sample detail page",
    "main": "./index.tsx",
    "private": true
}
```

- [ ] **Step 7.2: Create `api.ts`**

Create `docker/plugins/phpdeobf/api.ts`:

```typescript
import { AxiosInstance } from "axios";

export type DeobfOk = {
    status: "ok";
    blob_id: string;
    created: boolean;
    elapsed_ms: number;
};

export type DeobfError = {
    status: "error";
    code: string;
    message: string;
};

export type DeobfResult = DeobfOk | DeobfError;

/**
 * Trigger PHP deobfuscation of the sample identified by `sampleId`.
 *
 * Returns DeobfOk on success, DeobfError when the sidecar reports an
 * application-level failure (200 + status:"error"). Transport-level failures
 * (404, 413, 503) reach the caller as a thrown axios error and should be
 * handled in the caller's catch.
 */
export async function deobfuscate(
    api: AxiosInstance,
    sampleId: string,
    signal?: AbortSignal,
): Promise<DeobfResult> {
    const resp = await api.post<DeobfResult>(
        `/phpdeobf/${sampleId}`,
        {},
        { signal },
    );
    return resp.data;
}

export function isOk(r: DeobfResult): r is DeobfOk {
    return r.status === "ok";
}
```

- [ ] **Step 7.3: Commit**

```bash
git add docker/plugins/phpdeobf/package.json docker/plugins/phpdeobf/api.ts
git commit -m "Plugin phpdeobf: add FE types and API helper"
```

---

## Task 8: Plugin frontend — `PhpDeobfTab` component

**Files:**
- Create: `docker/plugins/phpdeobf/components/PhpDeobfTab.tsx`

Single-file tab component — the spec didn't justify breaking it into sub-components for v1. Implements the full state machine: idle → running → done / error / unavailable, with the non-PHP heuristic and 5 MB FE-side guard.

- [ ] **Step 8.1: Create the component**

Create `docker/plugins/phpdeobf/components/PhpDeobfTab.tsx`:

```tsx
import { useContext, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { APIContext } from "@mwdb-web/commons/api";
import { negateBuffer } from "@mwdb-web/commons/helpers";
import { ObjectContext } from "@mwdb-web/components/ShowObject";
import type { ObjectData } from "@mwdb-web/types/types";

import { deobfuscate, isOk } from "../api";
import type { DeobfResult } from "../api";

const MAX_SAMPLE_SIZE = 5 * 1024 * 1024;

type State =
    | { kind: "idle" }
    | { kind: "running" }
    | { kind: "done"; result: DeobfResult }
    | { kind: "unavailable" };

/** Heuristic: does this sample look like PHP? */
function looksLikePhp(text: string, fileName: string | undefined): boolean {
    const head = text.slice(0, 2048);
    if (head.includes("<?php") || head.includes("<?")) return true;
    if (fileName && /\.php$/i.test(fileName)) return true;
    return false;
}

function shortHash(h: string): string {
    return h.length > 12 ? `${h.slice(0, 8)}…${h.slice(-4)}` : h;
}

export function PhpDeobfTab() {
    const api = useContext(APIContext);
    const objectContext = useContext(ObjectContext);
    const object = objectContext?.object as Partial<ObjectData> | undefined;
    const sampleId = object?.id ?? "";
    const fileName = (object as { file_name?: string } | undefined)?.file_name;
    const fileSize = (object as { file_size?: number } | undefined)?.file_size;

    const [state, setState] = useState<State>({ kind: "idle" });
    const [sampleHead, setSampleHead] = useState<string>("");
    const [runAnyway, setRunAnyway] = useState(false);

    // Fetch a small slice of the sample for the heuristic check on mount.
    useEffect(() => {
        if (!sampleId) return;
        let cancelled = false;
        (async () => {
            try {
                const resp = await api.downloadFile(sampleId, 1);
                const bytes = negateBuffer(resp.data);
                const text = new TextDecoder("utf-8", { fatal: false }).decode(
                    bytes.slice(0, 2048),
                );
                if (!cancelled) setSampleHead(text);
            } catch {
                // If we can't peek, default the heuristic to "doesn't look like PHP";
                // the user can still tick "run anyway".
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [api, sampleId]);

    const isPhpLike = useMemo(
        () => looksLikePhp(sampleHead, fileName),
        [sampleHead, fileName],
    );
    const isOversize =
        typeof fileSize === "number" && fileSize > MAX_SAMPLE_SIZE;
    const buttonDisabled =
        isOversize ||
        state.kind === "running" ||
        (!isPhpLike && !runAnyway);

    async function onRun() {
        setState({ kind: "running" });
        try {
            const result = await deobfuscate(api.axios, sampleId);
            setState({ kind: "done", result });
        } catch (e: unknown) {
            const err = e as {
                response?: { status?: number; data?: { message?: string } };
                message?: string;
            };
            if (err.response?.status === 503) {
                setState({ kind: "unavailable" });
                return;
            }
            const message =
                err.response?.data?.message ?? err.message ?? "Request failed.";
            setState({
                kind: "done",
                result: {
                    status: "error",
                    code: `http_${err.response?.status ?? "unknown"}`,
                    message,
                },
            });
        }
    }

    return (
        <div style={{ padding: "20px", maxWidth: "800px" }}>
            <h4>PHP Deobfuscator</h4>

            {!isPhpLike && (
                <div
                    style={{
                        background: "#fff3cd",
                        border: "1px solid #ffeeba",
                        padding: "8px 12px",
                        marginBottom: "12px",
                        borderRadius: "4px",
                    }}
                >
                    This sample doesn&apos;t look like PHP.{" "}
                    <label style={{ marginLeft: "6px" }}>
                        <input
                            type="checkbox"
                            checked={runAnyway}
                            onChange={(e) => setRunAnyway(e.target.checked)}
                        />{" "}
                        Run anyway
                    </label>
                </div>
            )}

            {isOversize && (
                <div
                    style={{
                        background: "#f8d7da",
                        border: "1px solid #f5c6cb",
                        padding: "8px 12px",
                        marginBottom: "12px",
                        borderRadius: "4px",
                    }}
                >
                    Sample is larger than {MAX_SAMPLE_SIZE} bytes; the
                    deobfuscator only accepts samples up to that size.
                </div>
            )}

            <button
                type="button"
                className="btn btn-primary"
                disabled={buttonDisabled}
                onClick={onRun}
            >
                {state.kind === "running"
                    ? "Running…"
                    : state.kind === "done"
                      ? "Run again"
                      : "Deobfuscate"}
            </button>

            <div style={{ marginTop: "16px" }}>
                {state.kind === "running" && <em>Running deobfuscator…</em>}

                {state.kind === "unavailable" && (
                    <div
                        style={{
                            background: "#f8d7da",
                            border: "1px solid #f5c6cb",
                            padding: "10px 14px",
                            borderRadius: "4px",
                            color: "#721c24",
                        }}
                    >
                        PHP deobfuscator backend unavailable. Please contact
                        your administrator.
                    </div>
                )}

                {state.kind === "done" && isOk(state.result) && (
                    <div
                        style={{
                            background: "#d4edda",
                            border: "1px solid #c3e6cb",
                            padding: "10px 14px",
                            borderRadius: "4px",
                            color: "#155724",
                        }}
                    >
                        {state.result.created
                            ? "Created child blob "
                            : "Already deobfuscated — opening existing child blob: "}
                        <Link to={`/blob/${state.result.blob_id}`}>
                            {shortHash(state.result.blob_id)}
                        </Link>
                        <span
                            style={{
                                color: "#155724aa",
                                marginLeft: "10px",
                                fontSize: "0.9em",
                            }}
                        >
                            ({state.result.elapsed_ms} ms)
                        </span>
                    </div>
                )}

                {state.kind === "done" && !isOk(state.result) && (
                    <div
                        style={{
                            background: "#f8d7da",
                            border: "1px solid #f5c6cb",
                            padding: "10px 14px",
                            borderRadius: "4px",
                            color: "#721c24",
                        }}
                    >
                        <div
                            style={{
                                fontSize: "0.8em",
                                textTransform: "uppercase",
                                letterSpacing: "1px",
                                marginBottom: "4px",
                                opacity: 0.7,
                            }}
                        >
                            {state.result.code}
                        </div>
                        <pre
                            style={{
                                whiteSpace: "pre-wrap",
                                margin: 0,
                                fontFamily: "inherit",
                            }}
                        >
                            {state.result.message}
                        </pre>
                    </div>
                )}
            </div>
        </div>
    );
}
```

- [ ] **Step 8.2: Commit**

```bash
git add docker/plugins/phpdeobf/components/PhpDeobfTab.tsx
git commit -m "Plugin phpdeobf: add PhpDeobfTab React component"
```

---

## Task 9: Plugin frontend — registration

**Files:**
- Create: `docker/plugins/phpdeobf/index.tsx`

Registers the tab via `sampleTabsAfter`, identical to how `yarax_regex` does it.

- [ ] **Step 9.1: Create `index.tsx`**

Create `docker/plugins/phpdeobf/index.tsx`:

```tsx
import { faWandMagicSparkles } from "@fortawesome/free-solid-svg-icons";

import { ObjectTab } from "@mwdb-web/components/ShowObject";

import { PhpDeobfTab } from "./components/PhpDeobfTab";

export default () => ({
    sampleTabsAfter: [
        () => (
            <ObjectTab
                tab="phpdeobf"
                label="PHP Deobfuscate"
                icon={faWandMagicSparkles}
                component={PhpDeobfTab}
            />
        ),
    ],
});
```

- [ ] **Step 9.2: Commit**

```bash
git add docker/plugins/phpdeobf/index.tsx
git commit -m "Plugin phpdeobf: register sample tab via sampleTabsAfter"
```

---

## Task 10: Wire the sidecar + plugin into docker-compose-dev.yml

**Files:**
- Modify: `docker-compose-dev.yml`

Two changes:
1. Add a `phpdeobf` service that builds the sidecar.
2. Add `phpdeobf` to `MWDB_PLUGINS` and `MWDB_PHPDEOBF_URL` to the `mwdb` service env.

- [ ] **Step 10.1: Edit `docker-compose-dev.yml` to add the sidecar service**

Add this to the `services:` map (next to `mwdb`, `postgres`, etc):

```yaml
  phpdeobf:
    build:
      context: ./docker/phpdeobf
      dockerfile: Dockerfile.server
    restart: unless-stopped
```

- [ ] **Step 10.2: Edit `docker-compose-dev.yml` to register the plugin and the URL**

Find the `mwdb` service's `environment:` block and update `MWDB_PLUGINS`:

```yaml
      MWDB_PLUGINS: "yarax_regex,phpdeobf"
      MWDB_PHPDEOBF_URL: "http://phpdeobf:8080"
```

- [ ] **Step 10.3: Bring up the stack and verify the plugin loads**

```bash
./gen_vars.sh test
docker compose -f docker-compose-dev.yml up --build -d phpdeobf mwdb
docker compose -f docker-compose-dev.yml logs mwdb 2>&1 | grep -i phpdeobf
```

Expected: a log line `Registered POST /api/phpdeobf/<sample_id>` from `mwdb.plugin.phpdeobf`.

- [ ] **Step 10.4: Curl the sidecar from inside the mwdb container to confirm networking**

```bash
docker compose -f docker-compose-dev.yml exec mwdb \
  curl -s -X POST http://phpdeobf:8080/deobfuscate \
  -H 'Content-Type: application/json' \
  -d '{"source":"<?php echo 1+2;"}'
```

Expected: a JSON response with `"status":"ok"`.

- [ ] **Step 10.5: Commit**

```bash
git add docker-compose-dev.yml
git commit -m "Plugin phpdeobf: wire sidecar service + plugin registration into dev compose"
```

---

## Task 11: Backend e2e test

**Files:**
- Create: `tests/backend/test_phpdeobf.py`

End-to-end HTTP test against the running stack — uploads `e835f.php` (an obfuscated sample shipped with PHPDeobfuscator), POSTs to `/api/phpdeobf/<id>`, asserts a deobfuscated child TextBlob appears, and asserts a second call dedupes.

- [ ] **Step 11.1: Copy a known obfuscated PHP sample into the test fixtures**

```bash
mkdir -p tests/backend/fixtures
cp ~/WORK/PHPDeobfuscator/samples/e835f.php tests/backend/fixtures/phpdeobf_sample.php
```

- [ ] **Step 11.2: Write the test**

Create `tests/backend/test_phpdeobf.py`:

```python
"""End-to-end test for the phpdeobf plugin.

Requires the dev compose stack with the `phpdeobf` plugin enabled. Skipped
when the plugin endpoint isn't reachable (mirrors how other tests in this
directory degrade).

Run from tests/backend/:
    uv run pytest test_phpdeobf.py -v
"""
from pathlib import Path

import pytest


FIXTURE = Path(__file__).parent / "fixtures" / "phpdeobf_sample.php"


@pytest.mark.skipif(
    not FIXTURE.exists(),
    reason=(
        f"fixture {FIXTURE} missing — "
        f"run `cp ~/WORK/PHPDeobfuscator/samples/e835f.php {FIXTURE}`"
    ),
)
def test_phpdeobf_creates_child_blob_and_dedupes(admin_session):
    """Upload an obfuscated PHP sample, call the plugin endpoint twice,
    assert the first creates a child TextBlob and the second dedupes."""
    sample = admin_session.add_sample(
        filename="phpdeobf_sample.php",
        content=FIXTURE.read_bytes(),
    )
    sample_id = sample["id"]

    # First call — should create a new child blob.
    resp = admin_session.session.post(
        admin_session.mwdb_url + f"/phpdeobf/{sample_id}",
    )
    if resp.status_code == 503:
        pytest.skip("phpdeobf sidecar unavailable")
    if resp.status_code == 404 and "not found" not in resp.text.lower():
        pytest.skip("phpdeobf plugin endpoint not registered")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok", body
    assert body["created"] is True
    blob_id = body["blob_id"]

    # The blob is a child of the sample, and has our blob_type.
    sample_full = admin_session.get_sample(sample_id)
    child_ids = [c["id"] for c in sample_full.get("children", [])]
    assert blob_id in child_ids, sample_full

    blob = admin_session.get_blob(blob_id)
    assert blob["blob_type"] == "deobfuscated-php"

    # Second call — should dedupe.
    resp = admin_session.session.post(
        admin_session.mwdb_url + f"/phpdeobf/{sample_id}",
    )
    assert resp.status_code == 200
    body2 = resp.json()
    assert body2["status"] == "ok"
    assert body2["created"] is False
    assert body2["blob_id"] == blob_id
```

> **Helper API reference** (already exists in `tests/backend/utils.py`):
> - `admin_session` — pytest fixture from `conftest.py`, an `MwdbTest` instance logged in as admin.
> - `admin_session.add_sample(filename=..., content=...)` → dict with `"id"`.
> - `admin_session.get_sample(id)` / `admin_session.get_blob(id)` → object dict.
> - `admin_session.session` — underlying `requests.Session` with auth headers preset.
> - `admin_session.mwdb_url` — base URL (no trailing slash).

- [ ] **Step 11.3: Run the e2e test**

```bash
cd tests/backend
export MWDB_ADMIN_LOGIN=admin
export MWDB_ADMIN_PASSWORD="$(grep MWDB_ADMIN_PASSWORD ../../mwdb-vars.env | cut -d= -f2)"
export MWDB_URL=http://127.0.0.1/api
uv run pytest test_phpdeobf.py -v
```

Expected: 1 passed.

- [ ] **Step 11.4: Commit**

```bash
git add tests/backend/test_phpdeobf.py tests/backend/fixtures/phpdeobf_sample.php
git commit -m "Plugin phpdeobf: add e2e test (upload + deobfuscate + dedupe)"
```

---

## Task 12: README and final manual verification

**Files:**
- Create: `docker/plugins/phpdeobf/README.md`

Quick reference for the plugin's config + how to refresh the vendored source.

- [ ] **Step 12.1: Write the README**

Create `docker/plugins/phpdeobf/README.md`:

```markdown
# phpdeobf — MWDB plugin

Adds a "PHP Deobfuscate" tab to the sample detail page. Click → run the sample
through the `phpdeobf` sidecar → create a `TextBlob` child of the sample with
`blob_type="deobfuscated-php"`.

See spec: `docs/superpowers/specs/2026-04-29-phpdeobf-plugin-design.md`.

## Configuration

| Env var (mwdb service) | Default | Description |
|---|---|---|
| `MWDB_PHPDEOBF_URL` | `http://phpdeobf:8080` | Sidecar base URL. |

| Env var (sidecar) | Default | Description |
|---|---|---|
| `MAX_INPUT_BYTES` | `5242880` (5 MB) | Per-request source size cap. |

The plugin enforces a matching 5 MB cap on the sample side (defense-in-depth).
The plugin enforces a 30 s wall-clock timeout on the sidecar call.

## Vendored source

The PHP deobfuscator source lives under `docker/phpdeobf/` (NOT this directory).
That's the build context for the sidecar image. To refresh from
`~/WORK/PHPDeobfuscator/`:

    bash docker/phpdeobf/sync-from-source.sh

Then review and commit.

## Tests

Plugin unit tests (mocked sidecar):

    cd docker/plugins/phpdeobf && python -m pytest tests/ -v

Sidecar HTTP wrapper tests:

    php docker/phpdeobf/tests/server_test.php

End-to-end test (requires running stack):

    cd tests/backend && uv run pytest test_phpdeobf.py -v
```

- [ ] **Step 12.2: Manual UI verification**

With the dev stack up:

1. Open `http://localhost/` and log in as admin.
2. Upload a PHP sample (e.g. `~/WORK/PHPDeobfuscator/samples/e835f.php`).
3. Open the sample detail page. Confirm the "PHP Deobfuscate" tab appears.
4. Click the tab. Confirm the heuristic banner does NOT appear (it's a `.php` file).
5. Click "Deobfuscate". Confirm a green box appears with a link to the new blob and an `elapsed_ms` value.
6. Click the link. Confirm the blob page loads and shows the deobfuscated source.
7. Go back, click "Run again". Confirm the box says "Already deobfuscated — opening existing child blob" with the same hash.
8. Upload a non-PHP file (e.g. a small text file). Open the tab. Confirm the yellow "doesn't look like PHP" banner appears with a "Run anyway" checkbox, and that the button is disabled until the box is ticked.
9. Stop the `phpdeobf` service: `docker compose -f docker-compose-dev.yml stop phpdeobf`. Try to deobfuscate again. Confirm the red "backend unavailable" banner appears. Restart it: `docker compose -f docker-compose-dev.yml start phpdeobf`.

If any step fails, fix and re-verify before moving on.

- [ ] **Step 12.3: Commit README**

```bash
git add docker/plugins/phpdeobf/README.md
git commit -m "Plugin phpdeobf: add README"
```

- [ ] **Step 12.4: Final review of all commits**

```bash
git log --oneline master..HEAD
```

Expected: one commit per task above (12 commits).

---

## Notes for the implementer

- **Order matters.** Tasks 1–3 vendor + sidecar must complete before Task 10 (compose wiring), and Task 10 before Task 11 (e2e). Tasks 4–9 (the plugin code) can interleave with sidecar work but Task 11 needs everything.
- **Don't skip tests.** The unit tests in Tasks 5 and 6 catch ~80% of bugs you'd otherwise hit during manual UI verification — much faster feedback.
- **`requires_authorization` decorator import** comes from `mwdb.resources` (see how `yarax_regex` does it). It's already monkeypatched out in tests via `conftest.py`.
- **MWDB versioning.** This is a plugin, not a core change — no `mwdb.version` bump needed.
- **YAGNI.** The spec deliberately leaves out async, diff view, multi-version pipelines, attribute summaries. Don't add them.
- **PHP is single-process** in the sidecar (`php -S`). Concurrent UI clicks serialise. That's fine for v1; if it ever becomes a bottleneck, swap in php-fpm + nginx.
