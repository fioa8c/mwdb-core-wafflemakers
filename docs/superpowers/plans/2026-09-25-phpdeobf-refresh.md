# PHP Deobfuscator Integration Refresh — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `phpdeobf` sidecar build again on the current PHPDeobfuscator fork, build the php-eval-hook extension into its image, harden its container, and expose the new options, eval-hook peeling and the security-analysis report through the plugin and the sample tab, with sink categories stored as searchable attributes.

**Architecture:** One synchronous `POST /deobfuscate` in the PHP sidecar (`docker/phpdeobf/server.php`) takes an `options` object and returns static output, peeled output, analysis JSON and warnings; it calls a new `PHPDeobfuscator\Pipeline` class extracted in the source repo so the CLI and the sidecar share one pipeline. The MWDB plugin (`docker/plugins/phpdeobf/`) maps presets to options, stores three blob kinds and a `phpdeobf_sink` attribute, and proxies `/health`. The tab gets a one-click preset, an advanced panel and an analysis view. The image is multi-stage (builder compiles the vendored extension and runs composer; runtime has neither) and the container is read-only, capability-dropped and on an internal network.

**Tech Stack:** PHP 8.5 (`php:8.5-cli-bookworm`), composer 2.10.3, nikic/php-parser 4, php-eval-hook (`phpize` build), Flask-RESTful plugin resources, React + TypeScript tab, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-25-phpdeobf-refresh-design.md`

## Global Constraints

- Work in the worktree `/Users/fioa8c/WORK/mwdb-core-wafflemakers/.claude/worktrees/threatlib` on branch `phpdeobf-refresh` (stacked on `phptype`); never `cd` to the parent repository root. The venv for Python tests is `.venv/` there; ruff is `.venv/bin/ruff`. Local PHP is `/opt/homebrew/bin/php` (8.4).
- Source of the engine: `~/WORK/PHPDeobfuscator` (branch `master`, currently `35634d5`). Source of the extension: `~/WORK/php-eval-hook` (upstream `https://github.com/extremecoders-re/php-eval-hook.git`, commit `25e4e2a`, tracked files `LICENSE README.md config.m4 evalhook.c`).
- Vendored engine dir: `docker/phpdeobf/` (sidecar-owned files: `server.php`, `Dockerfile.server`, `sync-from-source.sh`, `sync-evalhook-from-source.sh`, `tests/server_test.php`, `.gitignore`, `README.md`, `VERSION`, `evalhook/`). Everything else there is synced from the source and must not be hand-edited.
- Sidecar options (exact keys, all booleans, default `false`): `strip_comments`, `execute_pure`, `eval_peel`, `remove_dead_code`, `rename_vars`, `analyze`.
- Warning codes: `eval_peel_unavailable`, `eval_peel_no_layers`, `eval_peel_timeout`, `eval_peel_error`.
- Env on the sidecar: `MAX_INPUT_BYTES` (default 5242880), `PHPDEOBF_PEEL_TIMEOUT` (default 10), `PHPDEOBF_MEMORY_LIMIT` (default `512M`), `PHPDEOBF_EVALHOOK` (set by the image to `/usr/local/lib/php/evalhook/evalhook.so`). `PHPDEOBF_PURE_TIMEOUT` from the spec is dropped: the executor's timeout is a class constant (10 s) and is not configurable without touching the engine; record this deviation in the fork README.
- Blob names and types: static `deobfuscated-<sha256>.php` / `deobfuscated-php`; peeled `deobfuscated-<sha256>.evalpeel.php` / `deobfuscated-php-evalpeel`; analysis `analysis-<sha256>.json` / `phpdeobf-analysis`. Attribute key `phpdeobf_sink`, one value per `summary.categories_present` entry, added with `check_permissions=False`, never removed.
- Presets: `full` = all static options + `analyze` + `eval_peel` iff the sample bytes contain `eval` (case-insensitive); `static` = all off; `custom` = explicit map.
- Plugin client timeout 90 s. Health proxy route `GET /api/phpdeobf/health` → 503 when the sidecar is unreachable.
- Container hardening (dev overlay and prod, identical): `read_only: true`, `tmpfs: [/tmp:size=512m]`, `mem_limit: 2g`, `pids_limit: 256`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, `user: "65534:65534"`, healthcheck `GET /health`; network `phpdeobf-net` with `internal: true`, joined by `phpdeobf` and `mwdb`.
- Lint: `.venv/bin/ruff check --fix docker/plugins/phpdeobf && .venv/bin/ruff format docker/plugins/phpdeobf` before each plugin commit; `cd mwdb/web && npx prettier --config package.json --write "../../docker/plugins/phpdeobf/**/*.{ts,tsx}"` for the tab.
- Plugin unit tests: `cd docker/plugins/phpdeobf && ../../../.venv/bin/python -m pytest tests/ -v` (`responses` is installed in the venv). Backend e2e from `tests/backend` with `MWDB_ADMIN_LOGIN=admin`, `MWDB_ADMIN_PASSWORD` from `../../mwdb-vars.env`, `MWDB_URL=http://127.0.0.1/api`, `../../.venv/bin/python -m pytest test_phpdeobf.py -v`.
- Dev stack: `./compose.sh --with dev --with plugins up --build -d mwdb mwdb-web phpdeobf threatlib-sync`; never `down`, never remove volumes. `docker compose` without the wrapper does not see the sidecar.
- Every commit ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

## File Structure

**PHPDeobfuscator repo (`~/WORK/PHPDeobfuscator`):**
- Create `src/Pipeline.php` — `PHPDeobfuscator\Pipeline` with `deobfuscate()` and `peelEvalLayers()` moved out of `index.php`.
- Modify `index.php` — call `Pipeline`.

**Fork, sidecar (`docker/phpdeobf/`):**
- Modify `sync-from-source.sh` — excludes, `VERSION`, dirty check.
- Create `sync-evalhook-from-source.sh`, `evalhook/{evalhook.c,config.m4,LICENSE,SOURCE}`, `VERSION`.
- Modify `Dockerfile.server` — multi-stage, extension build, composer pin.
- Modify `server.php` — options, health, warnings, engine.
- Modify `tests/server_test.php` — new cases.
- Modify `README.md` (fork-owned sidecar README; the synced engine README is `docker/phpdeobf/README.md`? No: the engine's README is synced over `README.md`. The fork's sidecar docs live in `docker/plugins/phpdeobf/README.md`) — see Task 9.

**Fork, plugin (`docker/plugins/phpdeobf/`):**
- Modify `client.py`, `resource.py`, `__init__.py`; create `attributes.py`, `presets.py`.
- Modify `tests/test_client.py`, `tests/test_resource.py`; create `tests/test_presets.py`.
- Modify `api.ts`, `components/PhpDeobfTab.tsx`; create `components/OptionsPanel.tsx`, `components/AnalysisPanel.tsx`.
- Modify `README.md`.

**Fork, infra and tests:** `compose/compose.with-plugins.yml`, `docker-compose-prod.yml`, `tests/backend/test_phpdeobf.py`.

---

### Task 1: Extract `PHPDeobfuscator\Pipeline` in the source repo

**Files:**
- Create: `~/WORK/PHPDeobfuscator/src/Pipeline.php`
- Modify: `~/WORK/PHPDeobfuscator/index.php` (the two functions at the top become calls into `Pipeline`)

**Interfaces:**
- Produces: `PHPDeobfuscator\Pipeline::deobfuscate(string $code, string $filename, bool $dumpOrig = false, bool $stripComments = false, bool $executePure = false, bool $removeDeadCode = false, bool $renameVars = false): array` returning `[$tree, $code]`; `Pipeline::peelEvalLayers(string $code, string $filename, bool $stripComments, bool $executePure, bool $removeDeadCode, bool $renameVars, ?string &$error = null, array $peelerOpts = []): ?string` (peeler opts pass straight to `EvalHook\EvalPeeler`'s `$opts`: `timeout`, `memory`, `maxLayers`, `php`); `Pipeline::evalHookAvailable(): bool`.

- [ ] **Step 1: Create a branch in the source repo**

```bash
cd ~/WORK/PHPDeobfuscator && git checkout -b pipeline-class
```

- [ ] **Step 2: Write the failing check**

The corpus runner is the test here. Add a temporary probe to prove the class is missing:

```bash
cd ~/WORK/PHPDeobfuscator && php -r 'require "vendor/autoload.php"; var_dump(class_exists("PHPDeobfuscator\\Pipeline"));'
```

Expected: `bool(false)`. (If `vendor/` is missing locally, run `composer install` first; `composer` is expected on the Mac, otherwise `docker run --rm -v "$PWD":/app -w /app composer:2.10.3 install`.)

- [ ] **Step 3: Write `src/Pipeline.php`**

Move the bodies verbatim from `index.php` (`deobfuscate()` and `peelEvalLayers()`), only replacing the recursive `deobfuscate(...)` call inside the peel loop with `self::deobfuscate(...)` and the `new EvalPeeler()` with `new EvalHook\EvalPeeler(null, $peelerOpts)`:

```php
<?php
namespace PHPDeobfuscator;

/**
 * The deobfuscation pipeline shared by the CLI (index.php) and the MWDB
 * sidecar. Keep behaviour identical to the former top-level functions in
 * index.php; index.php is now a thin wrapper around this class.
 */
class Pipeline
{
    /** @return array{0: array, 1: string} [$tree, $code] */
    public static function deobfuscate($code, $filename, $dumpOrig = false, $stripComments = false, $executePure = false, $removeDeadCode = false, $renameVars = false)
    {
        $deobf = new Deobfuscator($dumpOrig, false, $stripComments, $executePure, null, $removeDeadCode, $renameVars);
        $cwd = '/var/www/html/';
        $virtualPath = $cwd . basename($filename);
        $deobf->getFilesystem()->write($virtualPath, $code);
        $deobf->setCurrentFilename($virtualPath);
        $tree = $deobf->parse($code);
        $tree = $deobf->deobfuscate($tree);
        $newCode = $deobf->prettyPrint($tree);
        return array($tree, $newCode);
    }

    public static function evalHookAvailable(): bool
    {
        return (new EvalHook\EvalPeeler())->isAvailable();
    }

    /**
     * Dynamically peel eval() layers with the php-eval-hook sandbox, then run each
     * captured layer through the static pipeline. Returns the assembled source, or
     * null when the extension is unavailable / nothing was captured ($error says why).
     */
    public static function peelEvalLayers($code, $filename, $stripComments, $executePure, $removeDeadCode, $renameVars, &$error = null, array $peelerOpts = [])
    {
        $peeler = new EvalHook\EvalPeeler(null, $peelerOpts);
        if (!$peeler->isAvailable()) {
            $error = "eval-hook extension not found (build php-eval-hook, or set PHPDEOBF_EVALHOOK to its evalhook.so)";
            return null;
        }
        $result = $peeler->peel($code, basename($filename));
        if (empty($result['layers'])) {
            $error = !empty($result['error'])
                ? $result['error']
                : 'no eval() layers were captured'
                  . ($result['killed'] ? ' (sandbox timed out)' : '')
                  . (trim($result['stderr']) !== '' ? '; sandbox stderr: ' . substr(trim($result['stderr']), 0, 200) : '');
            return null;
        }
        $out = "<?php\n\n// Dynamically unpacked with php-eval-hook: "
            . count($result['layers']) . " eval() layer(s) captured in {$result['secs']}s.\n"
            . "// Each layer is the source passed to eval(), then statically deobfuscated.\n";
        foreach ($result['layers'] as $layer) {
            $out .= "\n// ===== eval() layer {$layer['n']} ({$layer['len']} bytes) =====\n";
            $layerCode = $layer['code'];
            try {
                list(, $clean) = self::deobfuscate('<?php ' . $layerCode, $filename, false, $stripComments, $executePure, $removeDeadCode, $renameVars);
                $out .= ltrim(preg_replace('/^<\?php\s*/', '', $clean)) . "\n";
            } catch (\Throwable $e) {
                $out .= "// (layer left raw: " . $e->getMessage() . ")\n" . rtrim($layerCode) . "\n";
            }
        }
        return $out;
    }
}
```

Compare against the current `index.php` line by line before deleting anything there: the bodies must match except for the two substitutions above.

- [ ] **Step 4: Make `index.php` use it**

Delete the two function definitions from `index.php` and add after the `require`:

```php
use PHPDeobfuscator\Pipeline;

function deobfuscate($code, $filename, $dumpOrig, $stripComments = false, $executePure = false, $removeDeadCode = false, $renameVars = false) {
    return Pipeline::deobfuscate($code, $filename, $dumpOrig, $stripComments, $executePure, $removeDeadCode, $renameVars);
}

function peelEvalLayers($code, $filename, $stripComments, $executePure, $removeDeadCode, $renameVars, &$error = null) {
    return Pipeline::peelEvalLayers($code, $filename, $stripComments, $executePure, $removeDeadCode, $renameVars, $error);
}
```

(Keeping the thin wrappers means every existing call site in `index.php` stays untouched.)

- [ ] **Step 5: Verify**

```bash
cd ~/WORK/PHPDeobfuscator && php -r 'require "vendor/autoload.php"; var_dump(class_exists("PHPDeobfuscator\\Pipeline"));' && php test.php && echo CORPUS_OK && php index.php -f tests/../samples/any.php >/dev/null 2>&1; php index.php -h | head -3
```

Expected: `bool(true)`, the corpus runner prints its per-file results with no failures and `CORPUS_OK`, and the CLI help still prints. Also run one real sample through `-e` to prove peeling still works locally (the Mac has `modules/evalhook.so` built for PHP 8.4): `PHPDEOBF_EVALHOOK=$HOME/WORK/php-eval-hook/modules/evalhook.so php index.php -f <a sample with eval()> -e | head -5` shows the `// Dynamically unpacked` banner.

- [ ] **Step 6: Commit, push, open the PR, and note the commit**

```bash
cd ~/WORK/PHPDeobfuscator && git add src/Pipeline.php index.php && git commit -m "refactor: extract Pipeline class so the CLI and the MWDB sidecar share one pipeline

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>" && git push -u origin pipeline-class && gh pr create --fill --base master
```

Record the commit hash: the fork's Task 2 syncs from this branch (`PHPDEOBF_SRC` still points at the checkout; leave it on `pipeline-class` until the PR merges, then `master` carries the same code).

---

### Task 2: Sync scripts, re-vendor the engine, vendor the extension

**Files:**
- Modify: `docker/phpdeobf/sync-from-source.sh`
- Create: `docker/phpdeobf/sync-evalhook-from-source.sh`, `docker/phpdeobf/evalhook/SOURCE`, `docker/phpdeobf/VERSION` (both written by the scripts)
- Re-synced: everything under `docker/phpdeobf/` that is not sidecar-owned; `docker/phpdeobf/evalhook/{evalhook.c,config.m4,LICENSE}`

**Interfaces:**
- Produces: `docker/phpdeobf/VERSION` containing the source commit hash (first line) used by `server.php`'s `engine.phpdeobf`; `docker/phpdeobf/evalhook/` used by the Dockerfile.

- [ ] **Step 1: Rewrite `sync-from-source.sh`**

```bash
#!/usr/bin/env bash
# Refresh the vendored PHPDeobfuscator engine from ~/WORK/PHPDeobfuscator/.
#
#   bash docker/phpdeobf/sync-from-source.sh [--allow-dirty]
#   PHPDEOBF_SRC=/path/to/PHPDeobfuscator bash docker/phpdeobf/sync-from-source.sh
#
# Sidecar-owned files are preserved (see the exclude list). The source repo's
# untracked research artefacts (out/, report/, bin/, samples/, docs/, stray
# tarballs) and its composer.lock are never copied: the fork keeps its own
# composer.lock so the image build is reproducible from the fork alone.
# Writes VERSION with the source commit so the sidecar can report it.
set -euo pipefail

SRC="${PHPDEOBF_SRC:-$HOME/WORK/PHPDeobfuscator}"
DST="$(cd "$(dirname "$0")" && pwd)"
ALLOW_DIRTY=0
[[ "${1:-}" == "--allow-dirty" ]] && ALLOW_DIRTY=1

if [[ ! -d "$SRC/.git" ]]; then
  echo "source not found or not a git checkout: $SRC" >&2
  echo "set PHPDEOBF_SRC=/path/to/PHPDeobfuscator to override" >&2
  exit 1
fi

DIRTY_COUNT="$(git -C "$SRC" status --porcelain --untracked-files=no | wc -l | tr -d ' ')"
if [[ "$DIRTY_COUNT" != "0" && "$ALLOW_DIRTY" != "1" ]]; then
  echo "source tree has $DIRTY_COUNT modified tracked file(s); commit them or pass --allow-dirty" >&2
  exit 1
fi
COMMIT="$(git -C "$SRC" rev-parse --short=12 HEAD)"

rsync -a --delete \
  --exclude='.git' --exclude='.worktrees' --exclude='.DS_Store' \
  --exclude='vendor' --exclude='composer.lock' \
  --exclude='samples' --exclude='docs' --exclude='bin' --exclude='out' --exclude='report' \
  --exclude='mago-*.tar.gz' \
  --exclude='server.php' --exclude='Dockerfile.server' \
  --exclude='sync-from-source.sh' --exclude='sync-evalhook-from-source.sh' \
  --exclude='evalhook' --exclude='VERSION' \
  --exclude='tests/server_test.php' --exclude='.gitignore' \
  --exclude='CLAUDE.md' --exclude='.htaccess' --exclude='Dockerfile' \
  "$SRC/" "$DST/"

{
  echo "$COMMIT"
  echo "source=$SRC"
  echo "dirty=$([[ "$DIRTY_COUNT" == "0" ]] && echo no || echo yes)"
  echo "synced=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$DST/VERSION"

echo "synced PHPDeobfuscator $COMMIT from $SRC to $DST"
echo "review with: git diff --stat -- $DST"
```

- [ ] **Step 2: Write `sync-evalhook-from-source.sh`**

```bash
#!/usr/bin/env bash
# Vendor the php-eval-hook extension sources (tracked files only) from
# ~/WORK/php-eval-hook/ into docker/phpdeobf/evalhook/ and record the commit.
#
#   bash docker/phpdeobf/sync-evalhook-from-source.sh
#   EVALHOOK_SRC=/path/to/php-eval-hook bash docker/phpdeobf/sync-evalhook-from-source.sh
set -euo pipefail

SRC="${EVALHOOK_SRC:-$HOME/WORK/php-eval-hook}"
DST="$(cd "$(dirname "$0")" && pwd)/evalhook"

if [[ ! -d "$SRC/.git" ]]; then
  echo "source not found or not a git checkout: $SRC" >&2
  exit 1
fi
for f in evalhook.c config.m4 LICENSE; do
  if ! git -C "$SRC" ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    echo "$f is not a tracked file in $SRC" >&2
    exit 1
  fi
done

mkdir -p "$DST"
for f in evalhook.c config.m4 LICENSE; do
  cp "$SRC/$f" "$DST/$f"
done
{
  echo "upstream=$(git -C "$SRC" remote get-url origin)"
  echo "commit=$(git -C "$SRC" rev-parse HEAD)"
  echo "synced=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$DST/SOURCE"

echo "vendored php-eval-hook $(git -C "$SRC" rev-parse --short HEAD) into $DST"
```

- [ ] **Step 3: Run both scripts and inspect**

```bash
chmod +x docker/phpdeobf/sync-from-source.sh docker/phpdeobf/sync-evalhook-from-source.sh
PHPDEOBF_SRC=$HOME/WORK/PHPDeobfuscator bash docker/phpdeobf/sync-from-source.sh
bash docker/phpdeobf/sync-evalhook-from-source.sh
git status --short docker/phpdeobf | head -40
cat docker/phpdeobf/VERSION docker/phpdeobf/evalhook/SOURCE
```

Expected: `src/Pipeline.php`, `src/Analysis/`, `src/EvalHook/`, `src/PureFunction/` and the changed reducers appear; `bin/`, `out/`, `report/`, `docs/`, `samples/`, `*.tar.gz` do NOT appear; `server.php`, `Dockerfile.server`, `tests/server_test.php`, `composer.lock` are unchanged; `VERSION`'s first line is the source commit; `evalhook/` holds exactly `evalhook.c config.m4 LICENSE SOURCE`. `docker/phpdeobf/.gitignore` must ignore `vendor/` (check; add if missing). If the source's `tests/` gained `.txt` corpus files, they sync too (that is intended; the runner in Task 3 exercises them).

- [ ] **Step 4: Commit**

```bash
git add docker/phpdeobf
git commit -m "phpdeobf: re-vendor PHPDeobfuscator (Pipeline, analysis, eval-hook, pure functions) and vendor php-eval-hook sources

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Multi-stage sidecar image with the extension

**Files:**
- Modify: `docker/phpdeobf/Dockerfile.server`

**Interfaces:**
- Produces: image with `/app` (engine + `vendor/`), `/usr/local/lib/php/evalhook/evalhook.so`, `ENV PHPDEOBF_EVALHOOK`, running as `nobody`, `CMD php -S 0.0.0.0:8080 server.php`.

- [ ] **Step 1: Write the failing check**

The current build fails at composer (reproduced in the investigation). Run it once to have the failure on record:

```bash
./compose.sh --with dev --with plugins build phpdeobf 2>&1 | tail -5
```

Expected: `ERROR: ... composer install ... exit code: 1` with "The zip extension and unzip/7z commands are both missing".

- [ ] **Step 2: Rewrite the Dockerfile**

```dockerfile
# syntax=docker/dockerfile:1
# phpdeobf sidecar: the PHPDeobfuscator engine behind server.php, with the
# php-eval-hook extension built from the vendored sources in ./evalhook.
FROM php:8.5-cli-bookworm AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends $PHPIZE_DEPS unzip \
    && rm -rf /var/lib/apt/lists/*

# Build the eval-hook extension against this image's PHP.
COPY evalhook /build/evalhook
WORKDIR /build/evalhook
RUN phpize && ./configure --enable-evalhook && make -j"$(nproc)" \
    && mkdir -p /out/evalhook && cp modules/evalhook.so /out/evalhook/evalhook.so

# Composer pinned: unpinned installs drifted to 2.10, which no longer falls back
# to git checkouts when unzip is missing.
RUN curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer --version=2.10.3

COPY . /app
WORKDIR /app
RUN composer install --no-dev --optimize-autoloader --no-interaction

FROM php:8.5-cli-bookworm

COPY --from=builder /out/evalhook/evalhook.so /usr/local/lib/php/evalhook/evalhook.so
COPY --from=builder --chown=nobody:nogroup /app /app
ENV PHPDEOBF_EVALHOOK=/usr/local/lib/php/evalhook/evalhook.so
WORKDIR /app
USER nobody
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
    CMD php -r 'exit(@file_get_contents("http://127.0.0.1:8080/health") === false ? 1 : 0);'
CMD ["php", "-S", "0.0.0.0:8080", "server.php"]
```

Add a `docker/phpdeobf/.dockerignore` with `tests/server_test.php` NOT excluded (it runs inside the image in Task 4) but `vendor/`, `.git`, `*.log` excluded.

- [ ] **Step 3: Build and prove the extension works against PHP 8.5**

```bash
./compose.sh --with dev --with plugins build phpdeobf 2>&1 | tail -3
docker run --rm --entrypoint php threatlib-phpdeobf -d extension=/usr/local/lib/php/evalhook/evalhook.so -r 'var_dump(extension_loaded("evalhook"));'
docker run --rm --entrypoint php threatlib-phpdeobf -r 'require "vendor/autoload.php"; var_dump(PHPDeobfuscator\Pipeline::evalHookAvailable());'
docker run --rm --entrypoint php threatlib-phpdeobf test.php | tail -3
docker run --rm --entrypoint sh threatlib-phpdeobf -c 'printf "%s" "<?php eval(base64_decode(\"ZWNobyAxOw==\"));" > /tmp/e.php && php index.php -f /tmp/e.php -e | head -4'
```

Expected: build succeeds; `bool(true)` twice; the corpus runner reports no failures; the `-e` run prints the `// Dynamically unpacked with php-eval-hook: 1 eval() layer(s)` banner followed by `echo 1;`. (The image name is `threatlib-phpdeobf` because the compose project is `threatlib`; confirm with `docker images | grep phpdeobf`.) If `phpize`/`make` fails on PHP 8.5, stop and report the compiler error verbatim: that is the spec's named risk and needs a decision before anything else is built.

- [ ] **Step 4: Commit**

```bash
git add docker/phpdeobf/Dockerfile.server docker/phpdeobf/.dockerignore
git commit -m "phpdeobf: multi-stage image with pinned composer, unzip and the php-eval-hook extension built for PHP 8.5

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Sidecar API — options, peeling, analysis, health

**Files:**
- Modify: `docker/phpdeobf/server.php`
- Test: `docker/phpdeobf/tests/server_test.php`

**Interfaces:**
- Produces: `POST /deobfuscate` and `GET /health` exactly as spec §5.

- [ ] **Step 1: Extend the test harness (failing first)**

Append to `tests/server_test.php` inside the `try` block, before `echo "\nALL PASSED\n";`:

```php
    // --- health ---
    [$code, $body] = get_json($port, '/health');
    assert_eq($code, 200, 'health: 200');
    assert_eq($body['status'], 'ok', 'health: status=ok');
    if (!is_bool($body['evalhook_available'] ?? null) || !is_string($body['phpdeobf'] ?? null)) {
        throw new \RuntimeException('FAIL: health: evalhook_available/phpdeobf missing');
    }
    $evalhook = $body['evalhook_available'];
    echo "ok: health shape (evalhook_available=" . var_export($evalhook, true) . ")\n";

    // --- options echo + null fields when nothing requested ---
    [$code, $body] = post_json($port, '/deobfuscate', ['source' => "<?php echo 1 + 2;"]);
    assert_eq($body['options'], [
        'strip_comments' => false, 'execute_pure' => false, 'eval_peel' => false,
        'remove_dead_code' => false, 'rename_vars' => false, 'analyze' => false,
    ], 'defaults: all options false');
    assert_eq($body['peeled'], null, 'defaults: peeled null');
    assert_eq($body['analysis'], null, 'defaults: analysis null');
    assert_eq($body['warnings'], [], 'defaults: no warnings');
    if (!is_string($body['engine']['phpdeobf'] ?? null)) {
        throw new \RuntimeException('FAIL: engine.phpdeobf missing');
    }

    // --- analysis on a sample with a sink ---
    [$code, $body] = post_json($port, '/deobfuscate', [
        'source' => "<?php \$c = \$_GET['c']; system(\$c);",
        'options' => ['analyze' => true, 'strip_comments' => true],
    ]);
    assert_eq($code, 200, 'analysis: 200');
    assert_eq($body['status'], 'ok', 'analysis: status=ok');
    assert_eq($body['analysis']['version'], 1, 'analysis: version 1');
    assert_eq($body['analysis']['analyzed'], 'static', 'analysis: analyzed=static');
    if (!in_array('os_exec', $body['analysis']['summary']['categories_present'], true)) {
        throw new \RuntimeException('FAIL: analysis: os_exec category expected, got ' . json_encode($body['analysis']['summary']));
    }
    echo "ok: analysis: os_exec sink found\n";

    // --- bad option value ---
    [$code, $body] = post_json($port, '/deobfuscate', ['source' => '<?php echo 1;', 'options' => ['analyze' => 'yes']]);
    assert_eq($code, 400, 'bad option: 400');
    assert_eq($body['code'], 'bad_request', 'bad option: code=bad_request');

    // --- eval peel ---
    $selfDecoding = "<?php eval(base64_decode('ZWNobyAxOw=='));";
    [$code, $body] = post_json($port, '/deobfuscate', [
        'source' => $selfDecoding, 'options' => ['eval_peel' => true, 'analyze' => true],
    ]);
    assert_eq($code, 200, 'peel: 200');
    if ($evalhook) {
        if (!is_array($body['peeled']) || ($body['peeled']['layers'] ?? 0) < 1) {
            throw new \RuntimeException('FAIL: peel: expected >=1 layer, got ' . json_encode($body['peeled']) . ' warnings=' . json_encode($body['warnings']));
        }
        if (strpos($body['peeled']['output'], 'echo 1') === false) {
            throw new \RuntimeException('FAIL: peel: layer content missing');
        }
        assert_eq($body['analysis']['analyzed'], 'peeled', 'peel: analysis ran on peeled output');
        echo "ok: peel: {$body['peeled']['layers']} layer(s) captured\n";
    } else {
        assert_eq($body['peeled'], null, 'peel (no extension): peeled null');
        assert_eq($body['warnings'][0]['code'], 'eval_peel_unavailable', 'peel (no extension): warning');
        echo "SKIP: eval-hook extension not available here; peel assertions ran in fallback mode only\n";
    }
```

and add the helper next to `post_json`:

```php
function get_json($port, $path) {
    $ch = curl_init("http://127.0.0.1:$port$path");
    curl_setopt_array($ch, [CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 10]);
    $resp = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    return [$code, json_decode($resp, true)];
}
```

Raise `CURLOPT_TIMEOUT` in `post_json` to 60 (peeling can take seconds).

- [ ] **Step 2: Run it inside the built image and see it fail**

```bash
docker run --rm --entrypoint php threatlib-phpdeobf tests/server_test.php 2>&1 | tail -5
```

Expected: fails at `health: 200` (the route returns 404 today).

- [ ] **Step 3: Rewrite `server.php`**

```php
<?php
/**
 * HTTP sidecar wrapper around the PHPDeobfuscator pipeline.
 *
 *   POST /deobfuscate  {"source": "...", "filename": "x.php", "options": {...}}
 *   GET  /health
 *
 * See docker/plugins/phpdeobf/README.md for the full contract.
 */

require __DIR__ . '/vendor/autoload.php';

use PHPDeobfuscator\Pipeline;
use PHPDeobfuscator\Deobfuscator;
use PHPDeobfuscator\Analysis\ReportFormatter;

ini_set('xdebug.var_display_max_depth', -1);
ini_set('memory_limit', getenv('PHPDEOBF_MEMORY_LIMIT') ?: '512M');
ini_set('xdebug.max_nesting_level', 1000);
set_time_limit(120);

$MAX_INPUT_BYTES = (int) (getenv('MAX_INPUT_BYTES') ?: 5 * 1024 * 1024);
$PEEL_TIMEOUT = (int) (getenv('PHPDEOBF_PEEL_TIMEOUT') ?: 10);
$OPTION_KEYS = ['strip_comments', 'execute_pure', 'eval_peel', 'remove_dead_code', 'rename_vars', 'analyze'];

function engine_version() {
    $f = __DIR__ . '/VERSION';
    if (!is_file($f)) return 'unknown';
    $first = strtok((string) file_get_contents($f), "\n");
    return trim($first) ?: 'unknown';
}

function emit($status, $payload) {
    http_response_code($status);
    header('Content-Type: application/json');
    echo json_encode($payload);
    exit;
}

function emit_error($code, $message) {
    emit(200, ['status' => 'error', 'code' => $code, 'message' => $message]);
}

$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
$method = $_SERVER['REQUEST_METHOD'];

if ($method === 'GET' && $path === '/health') {
    emit(200, [
        'status' => 'ok',
        'evalhook_available' => Pipeline::evalHookAvailable(),
        'phpdeobf' => engine_version(),
    ]);
}
if ($method !== 'POST' || $path !== '/deobfuscate') {
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
if (!is_string($filename) || $filename === '') {
    $filename = 'input.php';
}
$options = array_fill_keys($OPTION_KEYS, false);
$given = $body['options'] ?? [];
if (!is_array($given)) {
    emit(400, ['status' => 'error', 'code' => 'bad_request', 'message' => 'options must be an object']);
}
foreach ($OPTION_KEYS as $k) {
    if (array_key_exists($k, $given)) {
        if (!is_bool($given[$k])) {
            emit(400, ['status' => 'error', 'code' => 'bad_request', 'message' => "option $k must be a boolean"]);
        }
        $options[$k] = $given[$k];
    }
}
if (strlen($source) > $MAX_INPUT_BYTES) {
    emit_error('input_too_large', sprintf('source is %d bytes; cap is %d', strlen($source), $MAX_INPUT_BYTES));
}

$started = microtime(true);
$warnings = [];
$peeled = null;
$analysis = null;

try {
    list($tree, $output) = Pipeline::deobfuscate(
        $source, $filename, false,
        $options['strip_comments'], $options['execute_pure'], $options['remove_dead_code'], $options['rename_vars']
    );

    if ($options['eval_peel']) {
        if (!Pipeline::evalHookAvailable()) {
            $warnings[] = ['code' => 'eval_peel_unavailable', 'message' => 'eval-hook extension not found in this sidecar'];
        } else {
            $peelError = null;
            $t0 = microtime(true);
            $peeledOut = Pipeline::peelEvalLayers(
                $source, $filename,
                $options['strip_comments'], $options['execute_pure'], $options['remove_dead_code'], $options['rename_vars'],
                $peelError, ['timeout' => $PEEL_TIMEOUT]
            );
            if ($peeledOut !== null) {
                $layers = preg_match_all('/^\/\/ ===== eval\(\) layer \d+/m', $peeledOut);
                $peeled = ['output' => $peeledOut, 'layers' => $layers, 'secs' => round(microtime(true) - $t0, 2)];
            } else {
                $code = 'eval_peel_error';
                if (strpos((string) $peelError, 'no eval() layers') !== false) $code = 'eval_peel_no_layers';
                if (strpos((string) $peelError, 'timed out') !== false) $code = 'eval_peel_timeout';
                $warnings[] = ['code' => $code, 'message' => (string) $peelError];
            }
        }
    }

    if ($options['analyze']) {
        $analyzed = $peeled !== null ? 'peeled' : 'static';
        $codeToAnalyze = $peeled !== null ? $peeled['output'] : $output;
        $deobf = new Deobfuscator();
        $findings = $deobf->analyze($codeToAnalyze, $peeled !== null ? null : $tree);
        $formatter = new ReportFormatter();
        $analysis = json_decode($formatter->formatJson($findings, basename($filename)), true);
        $analysis['analyzed'] = $analyzed;
    }
} catch (\PhpParser\Error $e) {
    emit_error('parse_error', $e->getMessage());
} catch (\Throwable $e) {
    error_log('phpdeobf internal_error: ' . $e->getMessage() . "\n" . $e->getTraceAsString());
    emit_error('internal_error', $e->getMessage());
}

emit(200, [
    'status' => 'ok',
    'output' => $output,
    'peeled' => $peeled,
    'analysis' => $analysis,
    'warnings' => $warnings,
    'options' => $options,
    'engine' => ['phpdeobf' => engine_version(), 'evalhook_available' => Pipeline::evalHookAvailable()],
    'elapsed_ms' => (int) round((microtime(true) - $started) * 1000),
]);
```

Note: `Deobfuscator::analyze(string $code, ?array $fallbackTree)` — check its signature in the synced `src/Deobfuscator.php:140` and pass the tree only for the static case as above.

- [ ] **Step 4: Rebuild, run the harness inside the image, and locally without the extension**

```bash
./compose.sh --with dev --with plugins build phpdeobf 2>&1 | tail -1
docker run --rm --entrypoint php threatlib-phpdeobf tests/server_test.php
```

Expected: every `ok:` line including `peel: 1 layer(s) captured`, then `ALL PASSED`. Then locally (Mac, no extension unless you export `PHPDEOBF_EVALHOOK`): from `docker/phpdeobf`, `composer install` if `vendor/` is missing (or copy it out of the image: `docker run --rm --entrypoint tar threatlib-phpdeobf -C /app -cf - vendor | tar -xf - -C docker/phpdeobf`), then `php tests/server_test.php` prints the `SKIP:` line for peeling and `ALL PASSED`.

- [ ] **Step 5: Commit**

```bash
git add docker/phpdeobf/server.php docker/phpdeobf/tests/server_test.php
git commit -m "phpdeobf sidecar: options, eval-hook peeling, analysis report and /health

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Container hardening and internal network

**Files:**
- Modify: `compose/compose.with-plugins.yml` (the `phpdeobf` service; add `networks`)
- Modify: `docker-compose-prod.yml` (the `phpdeobf` service, `mwdb` networks, top-level `networks`)

- [ ] **Step 1: Dev overlay**

Replace the `phpdeobf` service block with:

```yaml
  phpdeobf:
    build:
      context: ./docker/phpdeobf
      dockerfile: Dockerfile.server
    restart: unless-stopped
    read_only: true
    tmpfs:
      - /tmp:size=512m
    mem_limit: 2g
    pids_limit: 256
    cap_drop: [ALL]
    security_opt:
      - no-new-privileges:true
    user: "65534:65534"
    networks:
      - phpdeobf-net
    healthcheck:
      test: ["CMD", "php", "-r", "exit(@file_get_contents('http://127.0.0.1:8080/health') === false ? 1 : 0);"]
      interval: 15s
      timeout: 3s
      retries: 3
```

and under `services.mwdb` add:

```yaml
    networks:
      - default
      - phpdeobf-net
```

plus a top-level:

```yaml
networks:
  phpdeobf-net:
    internal: true
```

(Keep the existing top-level `volumes:` block; `threatlib-sync` stays on `default`.)

- [ ] **Step 2: Prod compose**

Apply the same `phpdeobf` block (prod has no `build` differences), add `networks: [default, phpdeobf-net]` to `mwdb` (keep its traefik labels untouched), and the same top-level `networks` entry. Validate both merges:

```bash
docker compose -f docker-compose.yml -f compose/compose.with-dev.yml -f compose/compose.with-plugins.yml config | grep -n -A3 'phpdeobf-net'
MWDB_DOMAIN=x ACME_EMAIL=y docker compose -f docker-compose-prod.yml config | grep -n -A3 'phpdeobf-net'
```

Expected: the network appears with `internal: true`, attached to `mwdb` and `phpdeobf` in both.

- [ ] **Step 3: Bring the sidecar up with the running stack and prove reachability and isolation**

```bash
./compose.sh --with dev --with plugins up -d --build mwdb phpdeobf
./compose.sh --with dev --with plugins ps phpdeobf
./compose.sh --with dev --with plugins exec mwdb wget -qO- http://phpdeobf:8080/health
./compose.sh --with dev --with plugins exec phpdeobf php -r 'var_dump(@fsockopen("1.1.1.1", 80, $e, $s, 2));'
./compose.sh --with dev --with plugins exec phpdeobf sh -c 'touch /app/x 2>&1; touch /tmp/x && echo tmp-writable'
```

Expected: `phpdeobf` is `healthy`; `mwdb` gets `{"status":"ok","evalhook_available":true,...}`; the egress probe prints `bool(false)`; `/app` is read-only (`Read-only file system`) and `/tmp` writable.

- [ ] **Step 4: Commit**

```bash
git add compose/compose.with-plugins.yml docker-compose-prod.yml
git commit -m "phpdeobf: harden the sidecar container and isolate it on an internal network

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Plugin client — options, health, extended results

**Files:**
- Modify: `docker/plugins/phpdeobf/client.py`
- Test: `docker/plugins/phpdeobf/tests/test_client.py`

**Interfaces:**
- Produces: `OkResult(output: str, elapsed_ms: int, peeled: dict | None, analysis: dict | None, warnings: list[dict], engine: dict, options: dict)`; `deobfuscate(source, *, filename="input.php", base_url, timeout=DEFAULT_TIMEOUT, options=None)`; `DEFAULT_TIMEOUT = 90.0`; `HealthResult(evalhook_available: bool, phpdeobf: str)`; `health(*, base_url, timeout=5.0) -> HealthResult | UnavailableResult`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_client.py`)

```python
@responses.activate
def test_options_are_sent_and_extended_fields_parsed():
    responses.add(
        responses.POST,
        f"{SIDECAR}/deobfuscate",
        json={
            "status": "ok", "output": "<?php echo 3;", "elapsed_ms": 12,
            "peeled": {"output": "<?php echo 1;", "layers": 1, "secs": 0.4},
            "analysis": {"version": 1, "summary": {"categories_present": ["os_exec"]}, "analyzed": "peeled"},
            "warnings": [{"code": "eval_peel_no_layers", "message": "x"}],
            "options": {"analyze": True},
            "engine": {"phpdeobf": "abc123", "evalhook_available": True},
        },
        status=200,
    )
    result = deobfuscate("<?php", base_url=SIDECAR, options={"analyze": True, "eval_peel": True})
    assert isinstance(result, OkResult)
    sent = json.loads(responses.calls[0].request.body)
    assert sent["options"] == {"analyze": True, "eval_peel": True}
    assert result.peeled["layers"] == 1
    assert result.analysis["analyzed"] == "peeled"
    assert result.warnings[0]["code"] == "eval_peel_no_layers"
    assert result.engine["phpdeobf"] == "abc123"
    assert result.options == {"analyze": True}


@responses.activate
def test_missing_extended_fields_default_safely():
    responses.add(responses.POST, f"{SIDECAR}/deobfuscate",
                  json={"status": "ok", "output": "x", "elapsed_ms": 1}, status=200)
    result = deobfuscate("<?php", base_url=SIDECAR)
    assert result.peeled is None and result.analysis is None
    assert result.warnings == [] and result.engine == {} and result.options == {}
    sent = json.loads(responses.calls[0].request.body)
    assert "options" not in sent


@responses.activate
def test_health_ok_and_unavailable():
    responses.add(responses.GET, f"{SIDECAR}/health",
                  json={"status": "ok", "evalhook_available": True, "phpdeobf": "abc123"}, status=200)
    h = health(base_url=SIDECAR)
    assert isinstance(h, HealthResult)
    assert h.evalhook_available is True and h.phpdeobf == "abc123"
    responses.reset()
    responses.add(responses.GET, f"{SIDECAR}/health", status=502)
    assert isinstance(health(base_url=SIDECAR), UnavailableResult)
```

Add `import json` and `from phpdeobf.client import health, HealthResult` at the top.

- [ ] **Step 2: Run to see them fail**

```bash
cd docker/plugins/phpdeobf && ../../../.venv/bin/python -m pytest tests/test_client.py -v
```

Expected: `ImportError: cannot import name 'health'`.

- [ ] **Step 3: Implement**

In `client.py`: `DEFAULT_TIMEOUT = 90.0`; extend `OkResult`:

```python
@dataclass
class OkResult:
    output: str
    elapsed_ms: int
    peeled: Optional[dict] = None
    analysis: Optional[dict] = None
    warnings: list = field(default_factory=list)
    engine: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)


@dataclass
class HealthResult:
    evalhook_available: bool
    phpdeobf: str
```

`deobfuscate(...)` gains `options: Optional[dict] = None`; the JSON body includes `"options": options` only when `options` is not None; the ok branch fills the new fields with `body.get("peeled")`, `body.get("analysis")`, `list(body.get("warnings") or [])`, `dict(body.get("engine") or {})`, `dict(body.get("options") or {})`. Add:

```python
def health(*, base_url: str, timeout: float = 5.0):
    try:
        resp = requests.get(f"{base_url.rstrip('/')}/health", timeout=timeout)
    except requests.RequestException as exc:
        return UnavailableResult(detail=f"transport error: {exc}")
    if resp.status_code != 200:
        return UnavailableResult(detail=f"sidecar HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError:
        return UnavailableResult(detail="sidecar returned non-JSON body")
    return HealthResult(
        evalhook_available=bool(body.get("evalhook_available", False)),
        phpdeobf=str(body.get("phpdeobf", "unknown")),
    )
```

(`from dataclasses import dataclass, field`; `from typing import Optional, Union`.)

- [ ] **Step 4: Run, lint, commit**

```bash
cd docker/plugins/phpdeobf && ../../../.venv/bin/python -m pytest tests/test_client.py -v && cd ../../.. && .venv/bin/ruff check --fix docker/plugins/phpdeobf && .venv/bin/ruff format docker/plugins/phpdeobf
git add docker/plugins/phpdeobf/client.py docker/plugins/phpdeobf/tests/test_client.py
git commit -m "phpdeobf plugin: client sends options and parses peeled output, analysis, warnings and health

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Plugin resource — presets, storage, attributes, health proxy

**Files:**
- Create: `docker/plugins/phpdeobf/presets.py`, `docker/plugins/phpdeobf/attributes.py`
- Modify: `docker/plugins/phpdeobf/resource.py`, `docker/plugins/phpdeobf/__init__.py`
- Test: `docker/plugins/phpdeobf/tests/test_presets.py`, `docker/plugins/phpdeobf/tests/test_resource.py`

**Interfaces:**
- Produces: `presets.OPTION_KEYS`, `presets.resolve(body: dict | None, sample: bytes) -> dict` (raises `ValueError` on a bad preset/option); `attributes.ATTRIBUTE_KEY = "phpdeobf_sink"`, `attributes.ensure_attribute_definition()`; `PhpDeobfResource.post` returning `{status, blob_id, created, peeled_blob_id, peeled_created, analysis_blob_id, analysis_created, analysis_summary, warnings, engine, options, elapsed_ms}`; `PhpDeobfHealthResource.get` at `/phpdeobf/health`.

- [ ] **Step 1: presets — failing tests**

`tests/test_presets.py`:

```python
import pytest

from phpdeobf.presets import OPTION_KEYS, resolve

ALL_ON = {k: True for k in OPTION_KEYS}


def test_default_is_full_and_sniffs_eval():
    assert resolve(None, b"<?php eval($x);") == ALL_ON
    assert resolve({}, b"<?php EVAL($x);")["eval_peel"] is True
    no_eval = resolve({"preset": "full"}, b"<?php echo 1;")
    assert no_eval == {**ALL_ON, "eval_peel": False}


def test_static_is_all_off():
    assert resolve({"preset": "static"}, b"<?php eval($x);") == {k: False for k in OPTION_KEYS}


def test_custom_takes_explicit_map_with_false_defaults():
    r = resolve({"preset": "custom", "options": {"analyze": True}}, b"<?php eval($x);")
    assert r == {**{k: False for k in OPTION_KEYS}, "analyze": True}


@pytest.mark.parametrize("body", [
    {"preset": "nope"},
    {"preset": "custom", "options": {"analyze": "yes"}},
    {"preset": "custom", "options": {"unknown": True}},
    {"preset": "custom", "options": "x"},
])
def test_bad_bodies_raise(body):
    with pytest.raises(ValueError):
        resolve(body, b"")
```

- [ ] **Step 2: Implement `presets.py`**

```python
"""Map the tab's preset / options body onto the sidecar's option set."""

OPTION_KEYS = ("strip_comments", "execute_pure", "eval_peel", "remove_dead_code", "rename_vars", "analyze")
PRESETS = ("full", "static", "custom")


def resolve(body, sample: bytes) -> dict:
    body = body or {}
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    preset = body.get("preset", "full")
    if preset not in PRESETS:
        raise ValueError(f"preset must be one of {', '.join(PRESETS)}")
    if preset == "full":
        opts = {k: True for k in OPTION_KEYS}
        opts["eval_peel"] = b"eval" in sample.lower()
        return opts
    if preset == "static":
        return {k: False for k in OPTION_KEYS}
    given = body.get("options", {})
    if not isinstance(given, dict):
        raise ValueError("options must be an object")
    opts = {k: False for k in OPTION_KEYS}
    for key, value in given.items():
        if key not in OPTION_KEYS:
            raise ValueError(f"unknown option {key!r}")
        if not isinstance(value, bool):
            raise ValueError(f"option {key!r} must be a boolean")
        opts[key] = value
    return opts
```

Run `tests/test_presets.py`: all pass.

- [ ] **Step 3: `attributes.py`**

```python
"""The phpdeobf_sink attribute definition the resource writes sink categories to."""

from __future__ import annotations

import logging

ATTRIBUTE_KEY = "phpdeobf_sink"
logger = logging.getLogger("mwdb.plugin.phpdeobf")


def ensure_attribute_definition() -> None:
    from mwdb.model import db
    from mwdb.model.attribute import AttributeDefinition

    existing = (
        db.session.query(AttributeDefinition)
        .filter(AttributeDefinition.key == ATTRIBUTE_KEY)
        .first()
    )
    if existing:
        return
    db.session.add(
        AttributeDefinition(
            key=ATTRIBUTE_KEY,
            label="PHP sink category",
            description="Dangerous-sink categories found by the PHP deobfuscator's analysis (managed by phpdeobf)",
            url_template="",
            rich_template="",
            example_value="os_exec",
        )
    )
    db.session.commit()
    logger.info("phpdeobf: created attribute definition %s", ATTRIBUTE_KEY)
```

In `__init__.py`'s `entrypoint`, before registering resources:

```python
    from . import attributes

    try:
        attributes.ensure_attribute_definition()
    except Exception as e:  # schema may not exist yet during `mwdb-core configure`
        logger.warning("phpdeobf: deferred attribute setup until first request: %s", e)
        try:
            from mwdb.model import db

            db.session.rollback()
        except Exception:
            pass
```

and register the health resource: `app_context.register_resource(PhpDeobfHealthResource, "/phpdeobf/health")` (register it BEFORE the `<hash64:identifier>` route so `health` is never parsed as a hash; `hash64` only matches hex digests or `root`, but keep the order explicit).

The test conftest stubs `mwdb.model.attribute`? Check `tests/conftest.py`; if `mwdb.model.attribute` is not stubbed, add the same `MagicMock()` stub the other stubs use, otherwise `attributes.py` import fails under tests.

- [ ] **Step 4: resource tests — failing first**

Append to `tests/test_resource.py` (extend the `app` fixture: register `PhpDeobfHealthResource` at `/api/phpdeobf/health`, expose `app.fake_client.health`, stub the sample's `add_attribute` on the fake file and expose `fake_file.dhash = "ab" * 32`):

```python
def _set_sidecar_full(app, categories=("os_exec", "code_exec"), with_peel=True):
    from phpdeobf.client import OkResult
    app.fake_client.deobfuscate.return_value = OkResult(
        output="<?php\n\necho 3;", elapsed_ms=7,
        peeled={"output": "<?php echo 1;", "layers": 2, "secs": 0.5} if with_peel else None,
        analysis={"version": 1, "sources": [], "sinks": [], "errors": [],
                  "summary": {"source_count": 1, "sink_count": 2, "auto_exec_count": 1,
                              "categories_present": list(categories)},
                  "analyzed": "peeled" if with_peel else "static"},
        warnings=[{"code": "eval_peel_no_layers", "message": "n"}] if not with_peel else [],
        engine={"phpdeobf": "abc123", "evalhook_available": True},
        options={"analyze": True},
    )


def _blobs(app):
    """Make get_or_create return a distinct blob per blob_type, all created."""
    def _make(content, blob_name, blob_type, **kw):
        b = MagicMock(); b.dhash = (blob_type + "0" * 64)[:64]; return (b, True)
    app.fake_textblob.get_or_create.side_effect = _make


def test_full_preset_stores_three_blobs_and_attributes(app, client):
    fake_file = _set_sample(app, b"<?php eval($x);")
    _set_sidecar_full(app)
    _blobs(app)
    resp = client.post("/api/phpdeobf/abc123", json={})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["blob_id"].startswith("deobfuscated-php0")
    assert body["peeled_blob_id"].startswith("deobfuscated-php-evalpeel")
    assert body["analysis_blob_id"].startswith("phpdeobf-analysis")
    assert body["analysis_summary"]["categories_present"] == ["os_exec", "code_exec"]
    assert body["engine"]["phpdeobf"] == "abc123"
    # options sent to the sidecar came from the full preset with eval sniffed on
    sent = app.fake_client.deobfuscate.call_args.kwargs["options"]
    assert sent["eval_peel"] is True and sent["analyze"] is True
    # three get_or_create calls with the right names/types
    calls = app.fake_textblob.get_or_create.call_args_list
    names = [(c.args[1], c.args[2]) for c in calls]
    assert ("deobfuscated-abc123.php", "deobfuscated-php") in names
    assert ("deobfuscated-abc123.evalpeel.php", "deobfuscated-php-evalpeel") in names
    assert ("analysis-abc123.json", "phpdeobf-analysis") in names
    # attributes added for each category, permission check off
    added = [c.args[:2] for c in fake_file.add_attribute.call_args_list]
    assert added == [("phpdeobf_sink", "os_exec"), ("phpdeobf_sink", "code_exec")]
    for c in fake_file.add_attribute.call_args_list:
        assert c.kwargs["check_permissions"] is False
    assert app.fake_hooks.on_created_text_blob.call_count == 3


def test_static_preset_sends_all_off_and_stores_only_output(app, client):
    fake_file = _set_sample(app, b"<?php eval($x);")
    _set_sidecar_ok(app, output="<?php\n\necho 3;")
    fake_blob = MagicMock(); fake_blob.dhash = "cafebabe" * 8
    app.fake_textblob.get_or_create.return_value = (fake_blob, True)
    resp = client.post("/api/phpdeobf/abc123", json={"preset": "static"})
    body = resp.get_json()
    assert body["peeled_blob_id"] is None and body["analysis_blob_id"] is None
    assert body["analysis_summary"] is None
    assert all(v is False for v in app.fake_client.deobfuscate.call_args.kwargs["options"].values())
    fake_file.add_attribute.assert_not_called()


def test_bad_preset_body_is_400(app, client):
    _set_sample(app, b"<?php")
    assert client.post("/api/phpdeobf/abc123", json={"preset": "nope"}).status_code == 400
    assert client.post("/api/phpdeobf/abc123", json={"preset": "custom", "options": {"analyze": 1}}).status_code == 400


def test_warnings_pass_through_when_peel_captured_nothing(app, client):
    _set_sample(app, b"<?php eval($x);")
    _set_sidecar_full(app, with_peel=False)
    _blobs(app)
    body = client.post("/api/phpdeobf/abc123", json={}).get_json()
    assert body["peeled_blob_id"] is None
    assert body["warnings"][0]["code"] == "eval_peel_no_layers"
    assert body["analysis_summary"]["categories_present"] == ["os_exec", "code_exec"]


def test_health_proxy(app, client):
    from phpdeobf.client import HealthResult, UnavailableResult
    app.fake_client.health.return_value = HealthResult(evalhook_available=True, phpdeobf="abc123")
    resp = client.get("/api/phpdeobf/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "evalhook_available": True, "phpdeobf": "abc123"}
    app.fake_client.health.return_value = UnavailableResult(detail="down")
    assert client.get("/api/phpdeobf/health").status_code == 503
```

Keep the two existing blob tests passing: with no body they now run the `full` preset, so `_set_sidecar_ok` (no peeled/analysis) must still yield exactly one `get_or_create` call and `blob_id`/`created` as before. Update `test_ok_creates_blob_and_returns_id` only if its `commit.assert_called_once()` needs to become `assert_called()` (one commit per blob is acceptable; prefer a single commit after all blobs).

- [ ] **Step 5: Implement in `resource.py`**

Replace the body of `PhpDeobfResource.post` after the size check with:

```python
        sample_bytes = sample.read()
        try:
            options = presets.resolve(request.get_json(silent=True), sample_bytes)
        except ValueError as e:
            raise BadRequest(str(e))
        source = sample_bytes.decode("utf-8", errors="replace")

        result = client.deobfuscate(
            source, base_url=_sidecar_url(), timeout=SIDECAR_TIMEOUT, options=options
        )
        if isinstance(result, UnavailableResult):
            logger.warning("phpdeobf sidecar unavailable: %s", result.detail)
            raise ServiceUnavailable("PHP deobfuscator backend unavailable")
        if isinstance(result, ErrorResult):
            logger.info("phpdeobf eval sample=%s status=error code=%s", identifier, result.code)
            return jsonify({"status": "error", "code": result.code, "message": result.message})
        assert isinstance(result, OkResult)

        share_3rd_party = bool(getattr(sample, "share_3rd_party", False))
        share_with = [grp for grp in g.auth_user.groups if grp.workspace]

        def store(content, name, blob_type):
            blob, is_new = _mwdb_model.TextBlob.get_or_create(
                content, name, blob_type,
                share_3rd_party=share_3rd_party, parent=sample, share_with=share_with,
            )
            return blob, is_new

        stored = []
        blob, created = store(result.output, f"deobfuscated-{identifier}.php", BLOB_TYPE)
        stored.append((blob, created))
        peeled_blob = peeled_created = None
        if result.peeled and result.peeled.get("output"):
            peeled_blob, peeled_created = store(
                result.peeled["output"], f"deobfuscated-{identifier}.evalpeel.php", PEELED_BLOB_TYPE
            )
            stored.append((peeled_blob, peeled_created))
        analysis_blob = analysis_created = None
        summary = None
        if result.analysis:
            analysis_blob, analysis_created = store(
                json.dumps(result.analysis, indent=1, sort_keys=True),
                f"analysis-{identifier}.json", ANALYSIS_BLOB_TYPE,
            )
            stored.append((analysis_blob, analysis_created))
            summary = result.analysis.get("summary") or None
            for category in (summary or {}).get("categories_present", []):
                sample.add_attribute(ATTRIBUTE_KEY, str(category), commit=False, check_permissions=False)

        _mwdb_model.db.session.commit()
        for b, is_new in stored:
            if is_new:
                hooks.on_created_text_blob(b)
            else:
                hooks.on_reuploaded_text_blob(b)

        logger.info("phpdeobf eval sample=%s elapsed_ms=%d status=ok blobs=%d", identifier, result.elapsed_ms, len(stored))
        return jsonify({
            "status": "ok",
            "blob_id": blob.dhash, "created": bool(created),
            "peeled_blob_id": peeled_blob.dhash if peeled_blob else None,
            "peeled_created": bool(peeled_created) if peeled_blob else None,
            "analysis_blob_id": analysis_blob.dhash if analysis_blob else None,
            "analysis_created": bool(analysis_created) if analysis_blob else None,
            "analysis_summary": summary,
            "warnings": result.warnings,
            "engine": result.engine,
            "options": options,
            "elapsed_ms": result.elapsed_ms,
        })
```

with module constants `PEELED_BLOB_TYPE = "deobfuscated-php-evalpeel"`, `ANALYSIS_BLOB_TYPE = "phpdeobf-analysis"`, `SIDECAR_TIMEOUT = 90.0`, imports `import json`, `from flask import request`, `from werkzeug.exceptions import BadRequest`, `from . import presets`, `from .attributes import ATTRIBUTE_KEY`, and:

```python
class PhpDeobfHealthResource(Resource):
    @requires_authorization
    def get(self):
        """
        ---
        summary: Sidecar health (engine version, eval-hook availability)
        security:
            - bearerAuth: []
        tags:
            - phpdeobf
        """
        h = client.health(base_url=_sidecar_url())
        if isinstance(h, UnavailableResult):
            raise ServiceUnavailable("PHP deobfuscator backend unavailable")
        return jsonify({"status": "ok", "evalhook_available": h.evalhook_available, "phpdeobf": h.phpdeobf})
```

Update the docstring of `post` (OpenAPI) to describe the body and the new response fields.

- [ ] **Step 6: Run, lint, commit**

```bash
cd docker/plugins/phpdeobf && ../../../.venv/bin/python -m pytest tests/ -v && cd ../../.. && .venv/bin/ruff check --fix docker/plugins/phpdeobf && .venv/bin/ruff format docker/plugins/phpdeobf
git add docker/plugins/phpdeobf
git commit -m "phpdeobf plugin: presets, peeled and analysis blobs, phpdeobf_sink attributes, health proxy

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: The tab — preset button, advanced panel, analysis view

**Files:**
- Modify: `docker/plugins/phpdeobf/api.ts`, `docker/plugins/phpdeobf/components/PhpDeobfTab.tsx`
- Create: `docker/plugins/phpdeobf/components/OptionsPanel.tsx`, `docker/plugins/phpdeobf/components/AnalysisPanel.tsx`

**Interfaces:**
- `api.ts`: `type DeobfOptions = Record<OptionKey, boolean>` with `OPTION_KEYS`, `OPTION_LABELS: Record<OptionKey, {label: string; help: string}>`; `DeobfOk` gains `peeled_blob_id: string | null; peeled_created: boolean | null; analysis_blob_id: string | null; analysis_created: boolean | null; analysis_summary: {source_count: number; sink_count: number; auto_exec_count: number; categories_present: string[]} | null; warnings: {code: string; message: string}[]; engine: {phpdeobf: string; evalhook_available: boolean}; options: DeobfOptions`; `deobfuscate(api, sampleId, body: {preset: "full"} | {preset: "static"} | {preset: "custom"; options: DeobfOptions}, signal?)`; `health(api, signal?) -> Promise<{status: "ok"; evalhook_available: boolean; phpdeobf: string}>`; `fullPreset(sampleHead: string): DeobfOptions` (client-side mirror used only to pre-fill the panel: all true, `eval_peel` = head contains `eval`, case-insensitive).

- [ ] **Step 1: `api.ts`**

Extend the types as above; `deobfuscate` posts `body` as JSON; `health` does `api.get("/phpdeobf/health")`. Define:

```ts
export const OPTION_KEYS = ["strip_comments", "execute_pure", "eval_peel", "remove_dead_code", "rename_vars", "analyze"] as const;
export type OptionKey = (typeof OPTION_KEYS)[number];
export const OPTION_LABELS: Record<OptionKey, { label: string; help: string }> = {
    strip_comments: { label: "Strip comments", help: "Remove all comments from the input before reducing." },
    execute_pure: { label: "Execute pure decoder functions", help: "Run provably pure user functions in a sandboxed subprocess to resolve decoder calls the static reducer cannot." },
    eval_peel: { label: "Peel eval() layers (sandbox)", help: "Run the sample under the php-eval-hook extension in a hardened sandbox and capture each eval() layer." },
    remove_dead_code: { label: "Remove dead code", help: "Drop assignments to variables the reduced code never reads." },
    rename_vars: { label: "Rename obfuscated variables", help: "Give obfuscated-looking variables readable names when the code proves their role." },
    analyze: { label: "Security analysis", help: "List dangerous sources and sinks in the result and store them as searchable attributes." },
};
```

- [ ] **Step 2: `OptionsPanel.tsx`**

```tsx
import { OPTION_KEYS, OPTION_LABELS } from "../api";
import type { DeobfOptions, OptionKey } from "../api";

type Props = {
    value: DeobfOptions;
    onChange: (next: DeobfOptions) => void;
    evalhookAvailable: boolean;
    disabled: boolean;
    onRun: () => void;
};

export function OptionsPanel({ value, onChange, evalhookAvailable, disabled, onRun }: Props) {
    return (
        <div className="card mt-2">
            <div className="card-body">
                {OPTION_KEYS.map((key: OptionKey) => {
                    const blocked = key === "eval_peel" && !evalhookAvailable;
                    return (
                        <div className="form-check mb-1" key={key} title={blocked ? "The eval-hook extension is not available in this sidecar" : undefined}>
                            <input className="form-check-input" type="checkbox" id={`opt-${key}`}
                                checked={value[key] && !blocked} disabled={disabled || blocked}
                                onChange={(ev) => onChange({ ...value, [key]: ev.target.checked })} />
                            <label className="form-check-label" htmlFor={`opt-${key}`}>
                                {OPTION_LABELS[key].label}
                                <small className="d-block text-muted">{OPTION_LABELS[key].help}</small>
                            </label>
                        </div>
                    );
                })}
                <button type="button" className="btn btn-outline-primary btn-sm mt-2" disabled={disabled} onClick={onRun}>
                    Run with these options
                </button>
            </div>
        </div>
    );
}
```

- [ ] **Step 3: `AnalysisPanel.tsx`**

```tsx
import { Link } from "react-router-dom";
import { makeSearchLink } from "@mwdb-web/commons/helpers";
import type { DeobfOk } from "../api";

export function AnalysisPanel({ result }: { result: DeobfOk }) {
    const s = result.analysis_summary;
    if (!s) return null;
    return (
        <div className="card mt-3">
            <div className="card-header">Security analysis</div>
            <div className="card-body">
                <div className="mb-2">
                    <strong>{s.source_count}</strong> source(s), <strong>{s.sink_count}</strong> sink(s),{" "}
                    <strong>{s.auto_exec_count}</strong> auto-executing
                </div>
                <div className="mb-2">
                    {s.categories_present.length === 0 && <span className="text-muted">No dangerous sinks found.</span>}
                    {s.categories_present.map((c) => (
                        <Link key={c} className="badge badge-danger mr-1"
                            to={makeSearchLink({ field: "attribute.phpdeobf_sink", value: c, pathname: "/" })}
                            title={`Search all samples with sink ${c}`}>
                            {c}
                        </Link>
                    ))}
                </div>
                {result.analysis_blob_id && (
                    <small>Full report: <Link to={`/blob/${result.analysis_blob_id}`}>{result.analysis_blob_id.slice(0, 12)}…</Link></small>
                )}
            </div>
        </div>
    );
}
```

Confirm the exact query field for attribute searches in `mwdb/web/src/components/ShowObject/common/AttributeValue.tsx` (it builds a search link for attribute values); use the same `field` string it uses.

- [ ] **Step 4: Rework `PhpDeobfTab.tsx`**

Keep the heuristic, size guard and "run anyway"; add state `health` (from `health()` on mount, `null` while loading, `"unavailable"` on error), `advanced: boolean`, `options: DeobfOptions` (initialised with `fullPreset(sampleHead)` once the head is known, `eval_peel` forced off when health says unavailable). The primary button sends `{preset: "full"}`; an "Advanced" toggle (`btn btn-link btn-sm`) shows `OptionsPanel`, whose run sends `{preset: "custom", options}`. Under the heading render the engine badge: `Engine {health.phpdeobf} · eval-hook {available ? "available" : "unavailable"}`. Result view (replace the green box):

- summary strip: `elapsed_ms`, "static output" (always), "peeled output (N layers)" when `peeled_blob_id`, "analysis" when `analysis_blob_id`;
- warnings as `alert alert-warning` lines mapped to plain text: `eval_peel_unavailable` → "Peeling skipped: the eval-hook extension is not available in this sidecar."; `eval_peel_no_layers` → "Peeling captured no eval() layers; showing static output."; `eval_peel_timeout` → "Peeling timed out; showing static output."; `eval_peel_error` → the message;
- blob links: static (`/blob/<blob_id>`), peeled (`/blob/<peeled_blob_id>`, "N layer(s)"), each with created / already-existing wording as today;
- `<AnalysisPanel result={...} />`.

Keep the `onRun` abort logic; add a `runWith(body)` used by both buttons.

- [ ] **Step 5: Verify in the dev stack**

```bash
cd mwdb/web && npx prettier --config package.json --write "../../docker/plugins/phpdeobf/**/*.{ts,tsx}" && cd ../..
./compose.sh --with dev --with plugins up --build -d mwdb-web
docker compose logs mwdb-web | grep -iE 'error' | tail -5
```

Browser: open a PHP sample, the tab shows the engine badge with `eval-hook available`; "Deobfuscate" produces the summary strip, blob links and the analysis panel; a sink badge navigates to the search page with `attribute.phpdeobf_sink:<category>` and lists the sample; "Advanced" shows six switches; unticking everything but "Security analysis" and running produces only the static blob plus the report. On a sample with `eval(base64_decode(...))` the peeled blob link appears with a layer count.

- [ ] **Step 6: Commit**

```bash
git add docker/plugins/phpdeobf/api.ts docker/plugins/phpdeobf/components
git commit -m "phpdeobf tab: one-click preset, advanced options, peeled output and analysis panel

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: e2e test, docs, final verification

**Files:**
- Modify: `tests/backend/test_phpdeobf.py`, `docker/plugins/phpdeobf/README.md`

- [ ] **Step 1: Extend the e2e test**

Add after the existing test:

```python
def test_full_preset_creates_analysis_and_sink_attribute(admin_session):
    source = b"<?php $c = $_GET['c']; eval(base64_decode('ZWNobyAxOw==')); system($c);"
    sample = admin_session.add_sample(filename="phpdeobf_full.php", content=source)
    sid = sample["id"]
    resp = admin_session.session.post(admin_session.mwdb_url + f"/phpdeobf/{sid}", json={"preset": "full"})
    if resp.status_code == 503:
        pytest.skip("phpdeobf sidecar unavailable")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok", body
    assert body["analysis_blob_id"], body
    assert "os_exec" in body["analysis_summary"]["categories_present"], body
    assert body["engine"]["phpdeobf"] != "unknown"
    attrs = admin_session.get_attributes(sid)["attributes"]
    assert any(a["key"] == "phpdeobf_sink" and a["value"] == "os_exec" for a in attrs), attrs
    blob = admin_session.get_blob(body["analysis_blob_id"])
    assert blob["blob_type"] == "phpdeobf-analysis"
    if body["engine"]["evalhook_available"]:
        assert body["peeled_blob_id"], body
        assert admin_session.get_blob(body["peeled_blob_id"])["blob_type"] == "deobfuscated-php-evalpeel"

    # second run: nothing new
    body2 = admin_session.session.post(admin_session.mwdb_url + f"/phpdeobf/{sid}", json={"preset": "full"}).json()
    assert body2["created"] is False and body2["analysis_created"] is False


def test_health_endpoint(admin_session):
    resp = admin_session.session.get(admin_session.mwdb_url + "/phpdeobf/health")
    if resp.status_code == 503:
        pytest.skip("phpdeobf sidecar unavailable")
    assert resp.status_code == 200
    assert isinstance(resp.json()["evalhook_available"], bool)
```

Run against the stack (rebuild `mwdb` first so the plugin code in the image is current):

```bash
./compose.sh --with dev --with plugins up --build -d mwdb mwdb-web phpdeobf
cd tests/backend && export MWDB_ADMIN_LOGIN=admin MWDB_URL=http://127.0.0.1/api MWDB_ADMIN_PASSWORD=$(grep MWDB_ADMIN_PASSWORD ../../mwdb-vars.env | cut -d= -f2) && ../../.venv/bin/python -m pytest test_phpdeobf.py -v
```

Expected: 3 passed, no skips.

- [ ] **Step 2: Docs**

Rewrite `docker/plugins/phpdeobf/README.md` to cover: what the tab does; the presets; the sidecar API (`POST /deobfuscate` request/response with every field, warning codes, `GET /health`); the storage table (blob names/types, `phpdeobf_sink`); env vars (`MAX_INPUT_BYTES`, `PHPDEOBF_PEEL_TIMEOUT`, `PHPDEOBF_MEMORY_LIMIT`, `PHPDEOBF_EVALHOOK`) and the note that the pure-function timeout is fixed at 10 s in the engine; the container hardening list and the internal network; the two sync scripts and `VERSION`/`evalhook/SOURCE`; the test commands (`docker run --rm --entrypoint php <image> tests/server_test.php`, plugin unit tests, e2e). Mention that `docker/phpdeobf/README.md` is the engine's own README (synced) and must not be edited.

- [ ] **Step 3: Full verification pass**

```bash
cd docker/plugins/phpdeobf && ../../../.venv/bin/python -m pytest tests/ -q && cd ../../..
.venv/bin/ruff check docker/plugins/phpdeobf && .venv/bin/ruff format --check docker/plugins/phpdeobf
cd mwdb/web && npx prettier --config package.json --check "../../docker/plugins/phpdeobf/**/*.{ts,tsx}" && cd ../..
docker run --rm --entrypoint php threatlib-phpdeobf tests/server_test.php | tail -3
cd docker/plugins/threatlib && ../../../.venv/bin/python -m pytest tests/ -q -W error::Warning
```

Expected: all green; `ALL PASSED` from the sidecar harness with the peel case captured.

- [ ] **Step 4: Commit**

```bash
git add tests/backend/test_phpdeobf.py docker/plugins/phpdeobf/README.md
git commit -m "phpdeobf: e2e coverage for the full preset and health, plugin docs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec coverage.** §4 image and hardening → Tasks 3, 5. §5 API → Task 4 (Pipeline extraction is Task 1). §6 plugin → Tasks 6, 7. §7 tab → Task 8. §8 vendoring and sync → Task 2. §9 testing → every task plus Task 9. §10 rollout → prod steps are in the spec; nothing to build. The one deviation is `PHPDEOBF_PURE_TIMEOUT` (dropped; documented in Task 9's README).
- **Type consistency.** `OkResult` fields (`peeled`, `analysis`, `warnings`, `engine`, `options`) are read by Task 7's resource with the same names; the response keys listed in Task 7's Interfaces match `api.ts` in Task 8 and the e2e assertions in Task 9; blob names/types are the Global Constraints' strings everywhere; `presets.resolve` is the only place option semantics live in Python, `fullPreset` mirrors it in TS for pre-filling only.
- **Risk gate.** Task 3 stops the plan if the extension does not compile on PHP 8.5, before any API work is committed.
