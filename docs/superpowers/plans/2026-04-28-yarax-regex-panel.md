# YARA-X Regex Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new sample tab `YARA-X Regex` to the MWDB sample detail page that gives the team a live regex playground against the currently-viewed sample, backed by a Flask plugin endpoint that runs yara-x server-side.

**Architecture:** A single in-tree plugin under `docker/plugins/yarax_regex/` that is **both** a Python package (autoloaded by MWDB via `local_plugins_autodiscover=1`, registers a Flask-RESTful `Resource` at `POST /api/yarax/regex`) and an npm package (`@mwdb-web/plugin-yarax-regex`, registers a new `ObjectTab` via the existing `<Extendable ident="sampleTabs">` slot). Sample bytes are read server-side via `File.access(identifier)` + `File.read()` — no upload, no client-side bytes round-trip. Regex evaluation goes through a synthetic YARA rule compiled and scanned via the `yara-x` PyPI package's `Compiler` + `Scanner` API.

**Tech Stack:** Python 3.12, Flask + Flask-RESTful, `yara-x==1.15.0` (PyPI; Rust → Python via maturin, ships musl wheels), React 18 + TypeScript 5 + Vite, Bootstrap 4 (matches the rest of MWDB), `react-ace` for the syntax-highlighted sample view (already a project dependency).

**Companion spec:** `docs/superpowers/specs/2026-04-28-yarax-regex-panel-design.md` (commit `90568ea`).

---

## Pinned items (resolved during plan writing)

The spec's section 9 listed six items to pin by reading existing fork code. Resolved as follows:

1. **Frontend plugin source layout** — Same directory as the backend plugin (`docker/plugins/yarax_regex/`). The Dockerfiles `deploy/docker/Dockerfile` (line 32-35) and `deploy/docker/Dockerfile-web-dev` (line 7-10) both scan `/app/plugins` for `pyproject.toml`/`package.json` respectively and install whatever they find. One directory holds both halves of the plugin; the team's plugin docs (`docs/integration-guide.rst:240-250`) document this exact pattern.
2. **Backend plugin registration** — Python package with `__init__.py` exporting `__plugin_entrypoint__(app_context)`. Inside that, call `app_context.register_resource(YaraXRegexResource, "/yarax/regex")`. The `Resource` base class is `mwdb.core.service.Resource` (extends `flask.views.MethodView`). Auth via `@requires_authorization` decorator imported from `mwdb.resources`.
3. **Internal sample-read API** — `File.access(identifier)` returns the `File` object or `None` (auth-aware). `file.read()` returns full bytes (loads into memory; acceptable for the 50 MB cap). No de-obfuscation needed server-side — the XOR is only applied by `iterate_obfuscated()` for browser transit; storage and `.read()` are plaintext. Sample size is exposed as `file.file_size` (indexed `db.Integer` column at `mwdb/model/file.py:37`), not `file.size`.
4. **yara-x Python binding diagnostic surface** — Verified against `yara-x==1.15.0`:
    - `yara_x.Compiler()` → `.add_source(src)` raises `yara_x.CompileError` on syntax failure (formatted multi-line error message in `str(e)`, including `line:col` and a caret indicator).
    - `compiler.warnings()` returns a `list` of strings; on the regex flavors v0 cares about it is **often empty** in 1.15.0 (most warnings are gated behind feature flags not exposed via the Python binding).
    - `yara_x.Scanner(rules)` exposes `.set_timeout(seconds: int)`, `.max_matches_per_pattern: int`, `.scan(bytes)` returning `ScanResults` with `.matching_rules[].patterns[].matches[].{offset, length}`.
    - **Atoms are NOT exposed** through the Python binding (CLI-only). The endpoint's `atoms` field ships as an empty list in v0; spec already accounts for this.
5. **Stale-state UX (regex doesn't compile)** — Match list and highlight overlay continue showing the previous successful result, dimmed to 50% opacity. The lint strip carries the prominent error message. No striking-through, no hiding the data — just visually de-emphasizing.
6. **Empty regex** — Frontend does not send the request when the input is empty. The backend handler defensively rejects empty regex with HTTP 400 (`{"status": "bad_request", "message": "regex must not be empty"}`) — guards against direct API calls.

---

## File structure

### New files

**Plugin root** (under `docker/plugins/yarax_regex/`):
- `__init__.py` — Backend plugin entrypoint. Exports `__author__`, `__version__`, `__doc__`, and `__plugin_entrypoint__` that registers the resource.
- `pyproject.toml` — Python package metadata. Declares `yara-x==1.15.0` as a runtime dep so the Dockerfile installs it.
- `package.json` — npm package metadata. `name: "@mwdb-web/plugin-yarax-regex"`, `main: "./index.tsx"`.
- `tsconfig.json` — TypeScript config for the plugin (extends parent web tsconfig).
- `runner.py` — Pure-Python wrapper around yara-x: synthesize rule, compile, scan, normalize results to a dict shape that maps directly to the endpoint response. Pure (no Flask), so it's unit-testable without a server.
- `resource.py` — Flask-RESTful `Resource` class for `POST /api/yarax/regex`. Reads sample via `File.access`, calls `runner.run`, returns JSON.
- `index.tsx` — Frontend plugin entrypoint. Default export = function returning `{ sampleTabsAfter: [() => <ObjectTab .../>] }`.
- `api.ts` — Typed fetch wrapper for the new endpoint, plus the request/response types.
- `components/YaraXRegexTab.tsx` — Top-level tab component. Owns regex draft state, debounce, in-flight tracking.
- `components/RegexInput.tsx` — Single-line monospace input with localStorage draft persistence.
- `components/LintStrip.tsx` — Renders compile errors and warnings.
- `components/MatchList.tsx` — Sidebar of matches with click-to-jump.
- `components/SampleWithHighlights.tsx` — Sample content viewer with overlay highlights.
- `tests/__init__.py` — Empty.
- `tests/test_runner.py` — pytest unit tests for `runner.py`. yara-x is installed in the dev env, so these can run on the host without Docker.
- `tests/test_resource.py` — pytest integration tests using Flask test client + a stubbed `File.access`.
- `tests/conftest.py` — Pytest fixtures (test app, mocked sample).

### Modified files
- None (the plugin is self-contained; no upstream MWDB code is patched).

---

## Note on TDD cadence

The backend plugin's `runner.py` module is pure Python — no Flask, no DB, no docker stack. It can be developed with strict red-green-refactor TDD on the host (just `pip install yara-x pytest` in your dev shell, run pytest directly against `docker/plugins/yarax_regex/tests/test_runner.py`). Use that loop for tasks 3.

The Flask resource (`resource.py`) needs the MWDB Flask app context for its decorators and the `File.access` model. Its tests run against an in-process Flask test client with `File.access` mocked — no docker rebuild needed for the unit tests. Use that loop for task 4.

The smoke test (task 5) and the manual UI verification (task 11+12) require a docker rebuild — those are batched into dedicated tasks rather than per-step gates.

The frontend has no Jest tests in this plan: the existing `mwdb/web/jest.config.js` only discovers tests under `mwdb/web/src/__tests__/...` and our plugin lives outside that tree. Setting up a separate Jest config for plugin code is out of scope for v0; matches the same "no unit-test harness for X" reasoning the TLSH plan applied to `mwdb/core/`. Frontend correctness is verified by the manual end-to-end pass.

Each task ends with a commit so the tree stays green between tasks (matters for subagent-driven execution).

---

## Task 1: Create plugin skeleton

**Files:**
- Create: `docker/plugins/yarax_regex/__init__.py`
- Create: `docker/plugins/yarax_regex/pyproject.toml`
- Create: `docker/plugins/yarax_regex/package.json`
- Create: `docker/plugins/yarax_regex/tsconfig.json`

- [ ] **Step 1.1: Create the backend package init**

Create `docker/plugins/yarax_regex/__init__.py` with exactly this content:

```python
"""YARA-X live regex playground for the MWDB sample detail page."""
import logging

from mwdb.core.plugins import PluginAppContext

__author__ = "Waffle Makers"
__version__ = "0.1.0"
__doc__ = "YARA-X live regex playground for the MWDB sample detail page."


logger = logging.getLogger("mwdb.plugin.yarax_regex")


def entrypoint(app_context: PluginAppContext) -> None:
    """Plugin entrypoint — wires up the Flask resource.

    Importing the resource module is deferred to avoid a circular import
    when this package is loaded before the main MWDB app finishes booting.
    """
    from .resource import YaraXRegexResource

    app_context.register_resource(YaraXRegexResource, "/yarax/regex")
    logger.info("Registered POST /api/yarax/regex")


__plugin_entrypoint__ = entrypoint
```

- [ ] **Step 1.2: Create the backend pyproject.toml**

Create `docker/plugins/yarax_regex/pyproject.toml`:

```toml
[project]
name = "yarax-regex"
version = "0.1.0"
description = "YARA-X live regex playground for the MWDB sample detail page"
requires-python = ">=3.12"
dependencies = [
    "yara-x==1.15.0",
]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["yarax_regex"]
package-dir = {"yarax_regex" = "."}
```

The `package-dir` mapping is what makes `import yarax_regex` resolve to this directory after `uv pip install`. The Dockerfile (`deploy/docker/Dockerfile:32-35`) finds the `pyproject.toml` and installs the package into the venv.

- [ ] **Step 1.3: Create the npm package.json**

Create `docker/plugins/yarax_regex/package.json`:

```json
{
    "name": "@mwdb-web/plugin-yarax-regex",
    "version": "0.1.0",
    "description": "YARA-X live regex playground for the MWDB sample detail page",
    "main": "./index.tsx",
    "private": true
}
```

`main` points at the TSX entrypoint; Vite will resolve and transpile it during build. The `@mwdb-web/plugin-` prefix is required by `mwdb/web/vite.config.ts:8` (regex `^plugin-[a-zA-Z0-9\-_]+$` against the directory under `node_modules/@mwdb-web/`).

- [ ] **Step 1.4: Create tsconfig.json**

Create `docker/plugins/yarax_regex/tsconfig.json`:

```json
{
    "extends": "../../mwdb/web/tsconfig.json",
    "include": ["**/*.ts", "**/*.tsx"],
    "exclude": ["node_modules"]
}
```

Inheriting from the web app's tsconfig keeps target/jsx/lib/paths consistent.

- [ ] **Step 1.5: Verify the plugin directory shape**

Run: `ls docker/plugins/yarax_regex/`

Expected output (alphabetical):
```
__init__.py
package.json
pyproject.toml
tsconfig.json
```

- [ ] **Step 1.6: Commit**

```bash
git add docker/plugins/yarax_regex/
git commit -m "$(cat <<'EOF'
Plugin: skeleton for yarax_regex

Empty plugin package with both Python (pyproject.toml +
__init__.py + entrypoint) and npm (package.json) scaffolding.
The entrypoint references a not-yet-existing resource module —
that gets added in the next tasks. Plugin will not load yet
because the resource import will fail; loading is verified in
task 5 after the resource exists.

See docs/superpowers/specs/2026-04-28-yarax-regex-panel-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add yara-x to backend Docker build

**Files:**
- (none modified — confirms the existing Dockerfile picks up the plugin's pyproject.toml automatically)

The Dockerfile at `deploy/docker/Dockerfile:32-35` already has:

```dockerfile
ARG plugins
RUN --mount=type=cache,target=/root/.cache/uv \
    for plugin in $plugins $(find /app/plugins \( -name 'setup.py' -o -name 'pyproject.toml' \) -exec dirname {} \; | sort -u);  \
    do uv pip --no-cache-dir install $plugin; done
```

This means `yara-x==1.15.0` (declared in `docker/plugins/yarax_regex/pyproject.toml`) gets installed into the backend venv at build time. No Dockerfile changes needed.

- [ ] **Step 2.1: Verify yara-x ships musl-compatible wheels**

Run on the host:
```bash
python3 -m pip download yara-x==1.15.0 --platform musllinux_1_2_x86_64 --only-binary=:all: --no-deps -d /tmp/yarax-musl-check
```

Expected: a wheel like `yara_x-1.15.0-cp38-abi3-musllinux_1_2_x86_64.whl` is downloaded. If this fails with "no compatible wheels", we'd need to add `gcc` / `cargo` to the builder stage to compile from source — investigate before proceeding.

- [ ] **Step 2.2: Build the mwdb backend image with the plugin**

Run from the repo root:
```bash
docker compose -f docker-compose-dev.yml build mwdb
```

Expected: build succeeds. The `RUN ... uv pip install $plugin` line in the Dockerfile installs `yarax-regex` and pulls in `yara-x==1.15.0`. Look for a line near the end of the build like:
```
+ uv pip install /app/plugins/yarax_regex
```
followed by `Installed yarax-regex-0.1.0` and `Installed yara-x-1.15.0` (or similar).

If the build fails because `yara-x` cannot be installed under musl, fall back to adding `cargo` + `rustc` to the apk packages in the builder stage:
```dockerfile
RUN apk add --no-cache g++ musl-dev python3-dev cargo rust
```
Pin this fallback in a follow-up commit only if the wheel install fails.

- [ ] **Step 2.3: Verify the package is importable inside the container**

Run:
```bash
docker compose -f docker-compose-dev.yml run --rm --entrypoint "" mwdb \
    python -c "import yara_x; print(yara_x.__name__, dir(yara_x))"
```

Expected: prints `yara_x ['CompileError', 'Compiler', 'Formatter', 'Match', 'MetaType', 'Module', 'Pattern', 'Rule', 'Rules', 'ScanError', 'ScanOptions', 'ScanResults', 'Scanner', 'TimeoutError', ...]`.

- [ ] **Step 2.4: Commit (only if step 2.2 needed the cargo fallback)**

If step 2.2 succeeded without changes, skip this commit — there's nothing modified. Otherwise:

```bash
git add deploy/docker/Dockerfile
git commit -m "$(cat <<'EOF'
Docker: add cargo/rust to backend builder for yara-x

yara-x does not ship musl wheels for some platforms. Add
cargo + rust to the apk install in the builder stage so
uv pip install can compile yara-x from source under musl.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Implement and test the runner module

**Files:**
- Create: `docker/plugins/yarax_regex/runner.py`
- Create: `docker/plugins/yarax_regex/tests/__init__.py` (empty)
- Create: `docker/plugins/yarax_regex/tests/conftest.py`
- Create: `docker/plugins/yarax_regex/tests/test_runner.py`

The runner is a pure-Python module with no Flask dependency. It takes `(regex: str, sample_bytes: bytes)` and returns a result dict shaped like the endpoint response. All the yara-x logic lives here so the resource layer stays thin.

- [ ] **Step 3.1: Install yara-x in your host dev env**

```bash
python3 -m pip install yara-x==1.15.0 pytest
```

Expected: both packages install cleanly. yara-x ships an arm64 macOS wheel and an x86_64 Linux musl wheel.

- [ ] **Step 3.2: Create the empty tests package**

```bash
touch docker/plugins/yarax_regex/tests/__init__.py
```

- [ ] **Step 3.3: Create conftest.py with shared fixtures**

Create `docker/plugins/yarax_regex/tests/conftest.py`:

```python
"""Shared pytest fixtures for the yarax_regex plugin."""
import sys
from pathlib import Path

# Ensure the plugin package is importable when tests run from the repo root.
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))
```

This lets `from yarax_regex import runner` work when running `pytest docker/plugins/yarax_regex/tests/` from the repo root, without relying on the package being pip-installed.

- [ ] **Step 3.4: Write the failing tests**

Create `docker/plugins/yarax_regex/tests/test_runner.py`:

```python
"""Unit tests for yarax_regex.runner."""
import pytest

from yarax_regex import runner


def test_simple_match():
    result = runner.run(regex=r"foo[0-9]+", sample_bytes=b"hello foo123 world")
    assert result["status"] == "ok"
    assert len(result["matches"]) == 1
    m = result["matches"][0]
    assert m["offset"] == 6
    assert m["length"] == 6
    assert m["text"] == "foo123"


def test_no_matches():
    result = runner.run(regex=r"zzzz", sample_bytes=b"hello world")
    assert result["status"] == "ok"
    assert result["matches"] == []


def test_multiple_matches():
    result = runner.run(regex=r"a", sample_bytes=b"banana")
    assert result["status"] == "ok"
    offsets = [m["offset"] for m in result["matches"]]
    assert offsets == [1, 3, 5]


def test_compile_error_unclosed_class():
    result = runner.run(regex=r"[abc", sample_bytes=b"abc")
    assert result["status"] == "compile_error"
    assert len(result["diagnostics"]) >= 1
    diag = result["diagnostics"][0]
    assert diag["severity"] == "error"
    assert "regular expression" in diag["message"].lower() or \
           "regex" in diag["message"].lower() or \
           "character class" in diag["message"].lower()


def test_empty_regex_rejected():
    """Caller (resource layer) should reject empty regex before calling
    runner; runner is defensive and treats empty as compile_error."""
    result = runner.run(regex="", sample_bytes=b"anything")
    assert result["status"] == "compile_error"


def test_slash_in_regex_is_escaped():
    """A literal forward slash in the regex must not break the synthetic
    rule template."""
    result = runner.run(regex=r"a/b", sample_bytes=b"xxa/byy")
    assert result["status"] == "ok"
    assert len(result["matches"]) == 1
    assert result["matches"][0]["text"] == "a/b"


def test_match_text_is_string_not_bytes():
    """Match text must be JSON-serializable (str), not bytes."""
    result = runner.run(regex=r"\$\w+", sample_bytes=b"$payload = 1")
    assert result["status"] == "ok"
    assert isinstance(result["matches"][0]["text"], str)


def test_match_text_with_non_utf8_bytes_uses_replacement():
    """Sample bytes that aren't valid UTF-8 in the matched span should
    still produce a JSON-serializable match.text via replacement decoding."""
    # 0xff is not a valid UTF-8 start byte
    result = runner.run(regex=r".", sample_bytes=b"\xff")
    assert result["status"] == "ok"
    assert isinstance(result["matches"][0]["text"], str)


def test_line_column_offsets():
    sample = b"line1\nline2_$var\nline3"
    result = runner.run(regex=r"\$\w+", sample_bytes=sample)
    assert result["status"] == "ok"
    m = result["matches"][0]
    assert m["line"] == 2
    # column is 1-based; '$var' starts at byte index 12 == column 7 of line 2
    assert m["column"] == 7


def test_elapsed_ms_present():
    result = runner.run(regex=r"foo", sample_bytes=b"foo")
    assert "elapsed_ms" in result
    assert isinstance(result["elapsed_ms"], int)
    assert result["elapsed_ms"] >= 0


def test_atoms_field_present_even_if_empty():
    """v0: yara-x Python binding does not expose atoms; we ship []."""
    result = runner.run(regex=r"foo", sample_bytes=b"foo")
    assert "atoms" in result
    assert isinstance(result["atoms"], list)


def test_diagnostics_list_present_on_success():
    result = runner.run(regex=r"foo", sample_bytes=b"foo")
    assert "diagnostics" in result
    assert isinstance(result["diagnostics"], list)


def test_warnings_propagated_when_yarax_emits_them():
    """If yara-x's compiler.warnings() returns non-empty, they appear
    as warning-severity diagnostics."""
    # yara-x 1.15.0 rarely emits warnings on simple regex; this test
    # just verifies the wiring — if no warnings come back, that's a
    # legitimate engine output.
    result = runner.run(regex=r"foo", sample_bytes=b"foo")
    for diag in result["diagnostics"]:
        assert diag["severity"] in ("error", "warning", "info")
        assert isinstance(diag["message"], str)


def test_scanner_timeout_is_set():
    """Smoke test: a scanner timeout should be configured. Verify by
    checking the runner's exposed timeout constant."""
    assert hasattr(runner, "SCAN_TIMEOUT_SECONDS")
    assert isinstance(runner.SCAN_TIMEOUT_SECONDS, int)
    assert 1 <= runner.SCAN_TIMEOUT_SECONDS <= 30
```

- [ ] **Step 3.5: Run tests to verify they fail**

Run from repo root:
```bash
pytest docker/plugins/yarax_regex/tests/test_runner.py -v
```

Expected: all tests fail with `ModuleNotFoundError: No module named 'yarax_regex.runner'` (or similar — runner.py doesn't exist yet).

- [ ] **Step 3.6: Implement the runner module**

Create `docker/plugins/yarax_regex/runner.py`:

```python
"""Pure-Python wrapper around yara-x for the regex panel.

Synthesizes a single-pattern YARA rule from the user's regex, compiles it,
scans the sample bytes, and normalizes the result into a JSON-friendly dict.
No Flask, no DB — testable in isolation.
"""
import time
from typing import Any

import yara_x

# Maximum wall-clock time for a single scan. The 200 ms client-side debounce
# already protects against keystroke storms; this guards against pathological
# inputs that survive yara-x's compile-time complexity caps.
SCAN_TIMEOUT_SECONDS = 5

# Bound the number of matches returned per pattern. Web samples are small,
# but a regex like `.` against a 5 MB file would produce 5 million matches —
# we want to fail fast, not OOM the response.
MAX_MATCHES_PER_PATTERN = 1000

_RULE_TEMPLATE = "rule _r { strings: $a = /%s/ condition: any of them }"


def _escape_regex_for_template(regex: str) -> str:
    """Escape characters that would break out of the /.../ regex literal.

    The only structural concern is an unescaped `/` inside the regex literal.
    `\\` is part of regex syntax and must NOT be escaped further.
    """
    return regex.replace("/", r"\/")


def _byte_offset_to_line_column(sample_bytes: bytes, offset: int) -> tuple[int, int]:
    """Convert a byte offset into (line, column), both 1-based.

    Newlines are LF (b'\\n'). For samples with CRLF, the column counts the
    CR as a regular byte; this matches how editors display PHP/JS sources.
    """
    if offset <= 0:
        return 1, 1
    prefix = sample_bytes[:offset]
    line = prefix.count(b"\n") + 1
    last_nl = prefix.rfind(b"\n")
    column = offset - (last_nl + 1) + 1
    return line, column


def _decode_match_text(sample_bytes: bytes, offset: int, length: int) -> str:
    """Decode the matched span as UTF-8 with replacement; never raises."""
    return sample_bytes[offset : offset + length].decode("utf-8", errors="replace")


def _diagnostic_from_compile_error(exc: yara_x.CompileError) -> dict[str, Any]:
    """Convert a yara-x CompileError into our diagnostic shape.

    yara-x errors look like:
        error[E014]: invalid regular expression
         --> line:1:25
          |
        1 | rule x { strings: $a = /[/ condition: $a }
          |                         ^ unclosed character class

    We surface the full formatted message in `message` and leave parsing
    of line/column to v1 if needed (the synthetic rule's positions are
    not directly useful to the user anyway — they include our wrapping).
    """
    return {
        "severity": "error",
        "code": "compile_error",
        "message": str(exc),
    }


def _diagnostic_from_warning(text: str) -> dict[str, Any]:
    return {
        "severity": "warning",
        "code": "yarax_warning",
        "message": text,
    }


def run(regex: str, sample_bytes: bytes) -> dict[str, Any]:
    """Compile a synthetic rule wrapping `regex`, scan `sample_bytes`,
    return a JSON-serializable result dict.

    Empty regex is treated as a compile error (the resource layer rejects
    empty regex with HTTP 400 before reaching here; this is defense in depth).
    """
    if regex == "":
        return {
            "status": "compile_error",
            "diagnostics": [
                {
                    "severity": "error",
                    "code": "empty_regex",
                    "message": "regex must not be empty",
                }
            ],
        }

    started = time.perf_counter()

    source = _RULE_TEMPLATE % _escape_regex_for_template(regex)
    compiler = yara_x.Compiler()
    try:
        compiler.add_source(source)
    except yara_x.CompileError as exc:
        return {
            "status": "compile_error",
            "diagnostics": [_diagnostic_from_compile_error(exc)],
        }

    rules = compiler.build()
    warnings = list(compiler.warnings())

    scanner = yara_x.Scanner(rules)
    scanner.set_timeout(SCAN_TIMEOUT_SECONDS)
    scanner.max_matches_per_pattern = MAX_MATCHES_PER_PATTERN

    try:
        scan_results = scanner.scan(sample_bytes)
    except yara_x.TimeoutError:
        return {
            "status": "scan_timeout",
            "diagnostics": [
                {
                    "severity": "error",
                    "code": "scan_timeout",
                    "message": (
                        f"scan exceeded {SCAN_TIMEOUT_SECONDS}s; the regex is "
                        "likely catastrophically backtracking — consider "
                        "anchoring or restricting quantifiers"
                    ),
                }
            ],
        }

    matches: list[dict[str, Any]] = []
    for matched_rule in scan_results.matching_rules:
        for pattern in matched_rule.patterns:
            for match in pattern.matches:
                line, column = _byte_offset_to_line_column(sample_bytes, match.offset)
                matches.append(
                    {
                        "offset": match.offset,
                        "length": match.length,
                        "line": line,
                        "column": column,
                        "text": _decode_match_text(
                            sample_bytes, match.offset, match.length
                        ),
                    }
                )

    elapsed_ms = int((time.perf_counter() - started) * 1000)

    return {
        "status": "ok",
        "matches": matches,
        "diagnostics": [_diagnostic_from_warning(w) for w in warnings],
        "atoms": [],  # yara-x Python binding does not expose atoms in v1.15.0
        "elapsed_ms": elapsed_ms,
    }
```

- [ ] **Step 3.7: Run tests to verify they pass**

Run from repo root:
```bash
pytest docker/plugins/yarax_regex/tests/test_runner.py -v
```

Expected: all 14 tests pass. If `test_compile_error_unclosed_class` fails because the message format changes between yara-x versions, relax the assertion to check for `diag["severity"] == "error"` and a non-empty `diag["message"]`.

- [ ] **Step 3.8: Commit**

```bash
git add docker/plugins/yarax_regex/runner.py docker/plugins/yarax_regex/tests/
git commit -m "$(cat <<'EOF'
Plugin yarax_regex: add runner module with unit tests

Pure-Python wrapper around yara-x: synthesizes a single-pattern
rule from the user's regex, compiles, scans the sample, returns a
JSON-friendly dict. Handles compile errors, scan timeouts, slash-
in-regex escaping, and non-UTF8 match spans.

Atoms are not exposed in yara-x 1.15.0's Python binding, so the
'atoms' field is always [] in v0; spec acknowledges this.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Implement and test the Flask resource

**Files:**
- Create: `docker/plugins/yarax_regex/resource.py`
- Create: `docker/plugins/yarax_regex/tests/test_resource.py`

The resource is a thin Flask-RESTful `Resource` that validates the request body, fetches the sample via `File.access`, calls `runner.run`, and returns JSON. All the yara-x logic is in the runner — this layer is just plumbing + auth + sample fetch.

- [ ] **Step 4.1: Write the failing tests**

Create `docker/plugins/yarax_regex/tests/test_resource.py`:

```python
"""Integration tests for yarax_regex.resource using a Flask test client.

The MWDB Flask app is large and slow to construct in tests. Instead, we
build a minimal Flask app with our resource registered and a stubbed
File.access function injected via monkeypatch — same pattern used by
upstream MWDB's lighter resource tests.
"""
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from flask.views import MethodView


@pytest.fixture
def app(monkeypatch):
    """Minimal Flask app with the yarax_regex resource registered.

    Stubs out the @requires_authorization decorator (treats every request
    as authenticated) and the File.access model lookup.
    """
    # Stub requires_authorization to a no-op decorator BEFORE importing
    # the resource module — the decorator is applied at class-body time.
    import functools

    def noop_decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            return f(*args, **kwargs)

        return wrapper

    import mwdb.resources
    monkeypatch.setattr(mwdb.resources, "requires_authorization", noop_decorator)

    # Stub File.access — return a MagicMock with .read() per test
    fake_file_access = MagicMock()
    monkeypatch.setattr("mwdb.model.File.access", fake_file_access)

    # Re-import the resource module so it picks up the patched decorator.
    import importlib
    if "yarax_regex.resource" in list(__import__("sys").modules):
        del __import__("sys").modules["yarax_regex.resource"]
    from yarax_regex.resource import YaraXRegexResource

    flask_app = Flask(__name__)
    flask_app.add_url_rule(
        "/api/yarax/regex",
        view_func=YaraXRegexResource.as_view("yarax_regex"),
    )
    flask_app.fake_file_access = fake_file_access
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _set_sample(app, sample_bytes: bytes):
    fake_file = MagicMock()
    fake_file.read.return_value = sample_bytes
    fake_file.file_size = len(sample_bytes)
    app.fake_file_access.return_value = fake_file


def test_post_with_valid_regex_returns_matches(app, client):
    _set_sample(app, b"hello foo123 world")
    resp = client.post(
        "/api/yarax/regex",
        json={"sample_id": "abc", "regex": r"foo[0-9]+"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert len(body["matches"]) == 1
    assert body["matches"][0]["text"] == "foo123"


def test_post_with_invalid_regex_returns_compile_error_200(app, client):
    _set_sample(app, b"abc")
    resp = client.post(
        "/api/yarax/regex",
        json={"sample_id": "abc", "regex": r"[abc"},
    )
    # compile_error is still 200 — the request was well-formed
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "compile_error"


def test_missing_sample_id_returns_400(client):
    resp = client.post("/api/yarax/regex", json={"regex": r"foo"})
    assert resp.status_code == 400


def test_missing_regex_returns_400(client):
    resp = client.post("/api/yarax/regex", json={"sample_id": "abc"})
    assert resp.status_code == 400


def test_empty_regex_returns_400(client):
    resp = client.post(
        "/api/yarax/regex", json={"sample_id": "abc", "regex": ""}
    )
    assert resp.status_code == 400


def test_regex_over_4kb_returns_413(client):
    huge = "a" * 4097
    resp = client.post(
        "/api/yarax/regex", json={"sample_id": "abc", "regex": huge}
    )
    assert resp.status_code == 413


def test_sample_not_found_returns_404(app, client):
    app.fake_file_access.return_value = None  # File.access returns None
    resp = client.post(
        "/api/yarax/regex", json={"sample_id": "missing", "regex": r"foo"}
    )
    assert resp.status_code == 404


def test_sample_too_large_returns_sample_too_large_status(app, client):
    fake_file = MagicMock()
    fake_file.file_size = 100 * 1024 * 1024  # 100 MB > 50 MB cap
    app.fake_file_access.return_value = fake_file
    resp = client.post(
        "/api/yarax/regex", json={"sample_id": "huge", "regex": r"foo"}
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "sample_too_large"


def test_non_json_body_returns_400(client):
    resp = client.post(
        "/api/yarax/regex",
        data="not json",
        content_type="application/octet-stream",
    )
    assert resp.status_code == 400
```

- [ ] **Step 4.2: Run tests to verify they fail**

```bash
pytest docker/plugins/yarax_regex/tests/test_resource.py -v
```

Expected: tests fail with `ModuleNotFoundError: No module named 'yarax_regex.resource'`. (mwdb modules will import fine because the host dev env has mwdb-core installed via `uv sync` from the repo's `pyproject.toml`. If they don't, run `uv sync --dev` first.)

If `mwdb.resources` import fails (the host dev env doesn't have mwdb-core installed), use the docker workflow instead — run pytest inside the mwdb container after task 5 is complete and skip the host-side TDD for this task. The runner tests (task 3) will still have run cleanly on the host.

- [ ] **Step 4.3: Implement the resource module**

Create `docker/plugins/yarax_regex/resource.py`:

```python
"""Flask-RESTful resource for POST /api/yarax/regex."""
from flask import jsonify, request
from werkzeug.exceptions import BadRequest, NotFound, RequestEntityTooLarge

from mwdb.core.service import Resource
from mwdb.model import File
from mwdb.resources import requires_authorization

from . import runner


# Maximum regex length accepted at the endpoint. Real regexes are sub-200
# chars; this is a defensive cap, not a real-world boundary.
MAX_REGEX_LENGTH = 4096

# Maximum sample size we attempt to scan. Web-threat samples are KB-MB; the
# cap is defense against a researcher pointing the panel at a very large
# blob (e.g. a tar.gz). Above this we return a soft sample_too_large.
MAX_SAMPLE_SIZE = 50 * 1024 * 1024  # 50 MB


class YaraXRegexResource(Resource):
    @requires_authorization
    def post(self):
        """
        ---
        summary: Evaluate a regex against a sample using yara-x
        description: |
            Compiles the user's regex into a synthetic single-pattern YARA
            rule, scans the referenced sample, returns matches and any
            compile diagnostics.

            On compile failure the response is HTTP 200 with
            `status: "compile_error"` — the request itself is well-formed,
            only the user's regex is invalid.
        security:
            - bearerAuth: []
        tags:
            - yarax
        requestBody:
            required: true
            content:
                application/json:
                    schema:
                        type: object
                        required: [sample_id, regex]
                        properties:
                            sample_id:
                                type: string
                                description: SHA256/MD5/SHA1/SHA512 of the sample
                            regex:
                                type: string
                                description: User regex pattern (yara-x flavor)
        responses:
            200:
                description: Either matches or compile_error / sample_too_large
            400:
                description: Body malformed or regex empty
            404:
                description: Sample not found or unauthorized
            413:
                description: Regex exceeds length cap
        """
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise BadRequest("Request body must be a JSON object")

        sample_id = body.get("sample_id")
        regex = body.get("regex")

        if not isinstance(sample_id, str) or not sample_id:
            raise BadRequest("sample_id is required and must be a string")
        if not isinstance(regex, str):
            raise BadRequest("regex is required and must be a string")
        if regex == "":
            raise BadRequest("regex must not be empty")
        if len(regex) > MAX_REGEX_LENGTH:
            raise RequestEntityTooLarge(
                f"regex exceeds maximum length of {MAX_REGEX_LENGTH} characters"
            )

        sample = File.access(sample_id)
        if sample is None:
            raise NotFound("Sample not found or you don't have access to it")

        sample_size = getattr(sample, "file_size", None)
        if sample_size is not None and sample_size > MAX_SAMPLE_SIZE:
            return jsonify(
                {
                    "status": "sample_too_large",
                    "diagnostics": [
                        {
                            "severity": "error",
                            "code": "sample_too_large",
                            "message": (
                                f"sample is {sample_size} bytes; the live "
                                f"panel only scans samples up to "
                                f"{MAX_SAMPLE_SIZE} bytes"
                            ),
                        }
                    ],
                }
            )

        sample_bytes = sample.read()
        result = runner.run(regex=regex, sample_bytes=sample_bytes)
        return jsonify(result)
```

- [ ] **Step 4.4: Run tests to verify they pass**

```bash
pytest docker/plugins/yarax_regex/tests/test_resource.py -v
```

Expected: all 9 tests pass.

If they don't run on the host because mwdb-core isn't installed, that's fine — they will run inside the container after task 5. Move forward and verify there.

- [ ] **Step 4.5: Commit**

```bash
git add docker/plugins/yarax_regex/resource.py docker/plugins/yarax_regex/tests/test_resource.py
git commit -m "$(cat <<'EOF'
Plugin yarax_regex: add Flask resource with integration tests

POST /api/yarax/regex — auth-checked, 4 KB regex cap, 50 MB
sample cap, calls runner.run for the yara-x work. compile_error
remains a 200 response since the request body itself is valid;
only the user's regex is wrong.

Tests use a minimal Flask app + monkeypatched File.access and
requires_authorization to keep the test setup decoupled from the
full MWDB app context.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Wire backend, rebuild stack, smoke test endpoint

This task brings up the full dev stack, confirms the plugin loads, and hits the endpoint with curl against a real sample. Required before any frontend work — confirms the contract end-to-end.

- [ ] **Step 5.1: Generate the dev env vars (if not already done)**

```bash
./gen_vars.sh
```

Expected: `mwdb-vars.env` and `postgres-vars.env` are written if they didn't exist. If they're already present, the script no-ops.

- [ ] **Step 5.2: Build and start the dev stack**

```bash
docker compose -f docker-compose-dev.yml build mwdb
docker compose -f docker-compose-dev.yml up -d
```

Expected: all services start (postgres, redis, minio, mwdb, mwdb-web, mailhog).

- [ ] **Step 5.3: Confirm the plugin loaded**

```bash
docker compose -f docker-compose-dev.yml logs mwdb 2>&1 | grep -i "yarax\|plugin"
```

Expected output includes:
```
[INFO] ... plugins.load_plugins:106 - Loaded plugin 'yarax_regex'
[INFO] mwdb.plugin.yarax_regex - Registered POST /api/yarax/regex
```

If you see `Failed to load 'yarax_regex' plugin` followed by a traceback, fix the import error and rebuild. Common cause: `from .resource import` fails because the plugin package isn't installed correctly — verify with:
```bash
docker compose -f docker-compose-dev.yml run --rm --entrypoint "" mwdb \
    python -c "import yarax_regex; print(yarax_regex.__file__)"
```

- [ ] **Step 5.4: Run the backend tests inside the container**

```bash
docker compose -f docker-compose-dev.yml run --rm --entrypoint "" mwdb \
    pytest /app/plugins/yarax_regex/tests/ -v
```

Expected: all tests from tasks 3 and 4 pass. If task 4's tests were skipped on the host due to missing mwdb-core, this is the run that confirms them.

- [ ] **Step 5.5: Get an auth token for a test user**

If you don't have a test user, register one through the UI at http://localhost/. Then get a token:

```bash
TOKEN=$(curl -s -X POST http://localhost/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"login":"<your-user>","password":"<your-pw>"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['token'])")
echo "$TOKEN" | head -c 20
```

Expected: a string of ~200 chars starting with the JWT prefix.

- [ ] **Step 5.6: Upload a small PHP sample**

```bash
cat > /tmp/test-shell.php <<'PHP'
<?php
$payload = "ev"."al";
$cmd = $_POST['c'];
@$payload(base64_decode($cmd));
PHP

SAMPLE_ID=$(curl -s -X POST http://localhost/api/file \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/tmp/test-shell.php" \
  -F 'options={"upload_as":"*"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
echo "Sample id: $SAMPLE_ID"
```

Expected: `Sample id: <64-hex-char SHA256>`.

- [ ] **Step 5.7: Hit the regex endpoint**

The regex contains a literal `$` which bash will try to expand inside double quotes. Build the JSON body in Python (which gets shell-quoting right) and pipe to curl:

```bash
python3 -c '
import json, sys
print(json.dumps({"sample_id": sys.argv[1], "regex": r"\$[a-z_]\w*"}))
' "$SAMPLE_ID" | curl -s -X POST http://localhost/api/yarax/regex \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary @- \
  | python3 -m json.tool
```

Expected: a JSON response like:
```json
{
    "status": "ok",
    "matches": [
        {"offset": 7,  "length": 8, "line": 2, "column": 1, "text": "$payload"},
        {"offset": 32, "length": 4, "line": 3, "column": 1, "text": "$cmd"},
        ...
    ],
    "diagnostics": [],
    "atoms": [],
    "elapsed_ms": 5
}
```

Exact offsets depend on the sample bytes; the important checks are: `status: "ok"` and 3-4 matches with `text` containing `$<word>`.

- [ ] **Step 5.8: Hit it with an invalid regex**

```bash
python3 -c '
import json, sys
print(json.dumps({"sample_id": sys.argv[1], "regex": "[abc"}))
' "$SAMPLE_ID" | curl -s -X POST http://localhost/api/yarax/regex \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary @- \
  | python3 -m json.tool
```

Expected: HTTP 200 with body:
```json
{
    "status": "compile_error",
    "diagnostics": [
        {"severity": "error", "code": "compile_error", "message": "error[E014]: invalid regular expression\n --> ..."}
    ]
}
```

- [ ] **Step 5.9: Commit (no code changes — this task is verification only)**

Skip the commit if no files changed. The deliverable here is "the endpoint works end-to-end against the dev stack." Move to frontend tasks.

---

## Task 6: Frontend API client and types

**Files:**
- Create: `docker/plugins/yarax_regex/api.ts`

A small typed wrapper that talks to `/api/yarax/regex` using axios with the existing `APIContext`. Centralizing the call here keeps the React components free of fetch boilerplate and gives a single place to change the contract if the endpoint evolves.

- [ ] **Step 6.1: Implement api.ts**

Create `docker/plugins/yarax_regex/api.ts`:

```typescript
import axios, { AxiosInstance } from "axios";

export type Match = {
    offset: number;
    length: number;
    line: number;
    column: number;
    text: string;
};

export type Diagnostic = {
    severity: "error" | "warning" | "info";
    code: string;
    message: string;
};

export type RegexResult =
    | {
          status: "ok";
          matches: Match[];
          diagnostics: Diagnostic[];
          atoms: string[];
          elapsed_ms: number;
      }
    | {
          status: "compile_error";
          diagnostics: Diagnostic[];
      }
    | {
          status: "sample_too_large";
          diagnostics: Diagnostic[];
      }
    | {
          status: "scan_timeout";
          diagnostics: Diagnostic[];
      };

export type RegexRequest = {
    sample_id: string;
    regex: string;
};

/**
 * Evaluate `regex` against the sample identified by `sample_id` via the
 * MWDB yarax-regex plugin endpoint.
 *
 * Pass an `AbortSignal` from the caller's debounce loop so stale requests
 * (regex value changed before this one returned) can be cancelled.
 */
export async function evalRegex(
    api: AxiosInstance,
    req: RegexRequest,
    signal?: AbortSignal,
): Promise<RegexResult> {
    const response = await api.post<RegexResult>("/yarax/regex", req, { signal });
    return response.data;
}

/**
 * True if the result has matches/atoms/elapsed_ms (i.e. status was "ok").
 * Narrows the union for TS consumers.
 */
export function isOk(
    r: RegexResult,
): r is Extract<RegexResult, { status: "ok" }> {
    return r.status === "ok";
}
```

- [ ] **Step 6.2: Commit**

```bash
git add docker/plugins/yarax_regex/api.ts
git commit -m "$(cat <<'EOF'
Plugin yarax_regex: add typed api wrapper

Single-call evalRegex() against /api/yarax/regex, plus the
shared types (Match, Diagnostic, RegexResult union, RegexRequest)
used by the components in subsequent tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: RegexInput + LintStrip components

**Files:**
- Create: `docker/plugins/yarax_regex/components/RegexInput.tsx`
- Create: `docker/plugins/yarax_regex/components/LintStrip.tsx`

These are the simplest two components and pair naturally — the input writes to localStorage, the strip renders diagnostics. Both are presentational; no fetching here.

- [ ] **Step 7.1: Implement RegexInput**

Create `docker/plugins/yarax_regex/components/RegexInput.tsx`:

```tsx
import { useEffect, useRef } from "react";

type Props = {
    sampleId: string;
    value: string;
    onChange: (next: string) => void;
};

const STORAGE_KEY_PREFIX = "yarax-regex-draft:";

/**
 * Single-line monospace regex input with per-sample localStorage draft.
 *
 * On mount: hydrates `value` from localStorage if the parent is still at
 * its initial empty state. (Parent owns the value; this component is a
 * controlled input with a side effect for persistence.)
 *
 * On change: writes to localStorage on every keystroke. The 200ms
 * debounce lives at the parent level on the network call — local
 * persistence is fast enough to do eagerly.
 */
export function RegexInput({ sampleId, value, onChange }: Props) {
    const inputRef = useRef<HTMLInputElement>(null);
    const hydrated = useRef(false);

    useEffect(() => {
        // One-shot hydration on first render, only if parent is still empty
        // and we haven't already hydrated for this sampleId.
        if (hydrated.current) return;
        hydrated.current = true;
        if (value === "") {
            const saved = localStorage.getItem(STORAGE_KEY_PREFIX + sampleId);
            if (saved !== null && saved !== "") {
                onChange(saved);
            }
        }
        inputRef.current?.focus();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [sampleId]);

    useEffect(() => {
        // Persist on every change, but skip the very first render to avoid
        // overwriting saved state with the initial empty parent value.
        if (!hydrated.current) return;
        if (value === "") {
            localStorage.removeItem(STORAGE_KEY_PREFIX + sampleId);
        } else {
            localStorage.setItem(STORAGE_KEY_PREFIX + sampleId, value);
        }
    }, [sampleId, value]);

    return (
        <input
            ref={inputRef}
            type="text"
            spellCheck={false}
            autoComplete="off"
            className="form-control"
            style={{
                fontFamily:
                    'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
                fontSize: "13px",
            }}
            placeholder="YARA-X regex (e.g. \\$[a-z_\\x80-\\xff][a-z0-9_\\x80-\\xff]*)"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            aria-label="YARA-X regex pattern"
        />
    );
}
```

- [ ] **Step 7.2: Implement LintStrip**

Create `docker/plugins/yarax_regex/components/LintStrip.tsx`:

```tsx
import type { Diagnostic } from "../api";

type Props = {
    diagnostics: Diagnostic[];
    inFlight: boolean;
    matchCount: number | null; // null = no result yet
};

/**
 * Strip directly under the regex input. Shows engine diagnostics, an
 * "evaluating..." indicator while a request is in flight, and a match
 * count when the last result was successful.
 */
export function LintStrip({ diagnostics, inFlight, matchCount }: Props) {
    const hasError = diagnostics.some((d) => d.severity === "error");

    return (
        <div
            className="d-flex flex-wrap align-items-start"
            style={{
                gap: "12px",
                padding: "6px 12px",
                fontSize: "12px",
                background: "#fffbe6",
                borderBottom: "1px solid #ffe7a0",
                fontFamily:
                    'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
            }}
        >
            {!hasError && matchCount !== null && (
                <span style={{ color: "#28a745" }}>
                    ✓ valid · {matchCount} match{matchCount === 1 ? "" : "es"}
                </span>
            )}
            {inFlight && (
                <span style={{ color: "#888" }}>evaluating…</span>
            )}
            {diagnostics.map((d, i) => {
                const color =
                    d.severity === "error"
                        ? "#dc3545"
                        : d.severity === "warning"
                          ? "#b8860b"
                          : "#0c5460";
                const icon =
                    d.severity === "error"
                        ? "✗"
                        : d.severity === "warning"
                          ? "⚠"
                          : "ⓘ";
                return (
                    <span
                        key={i}
                        style={{ color, whiteSpace: "pre-wrap" }}
                        title={d.code}
                    >
                        {icon} {d.message}
                    </span>
                );
            })}
        </div>
    );
}
```

- [ ] **Step 7.3: Commit**

```bash
git add docker/plugins/yarax_regex/components/RegexInput.tsx \
        docker/plugins/yarax_regex/components/LintStrip.tsx
git commit -m "$(cat <<'EOF'
Plugin yarax_regex: add RegexInput and LintStrip components

RegexInput: controlled monospace input, autofocus, per-sample
localStorage draft persistence (key yarax-regex-draft:<id>).

LintStrip: renders engine diagnostics with severity colors,
'evaluating...' indicator when in flight, and a match count
when there's a successful result with no errors.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: MatchList component

**Files:**
- Create: `docker/plugins/yarax_regex/components/MatchList.tsx`

Sidebar component listing matches with click-to-jump. Click handler is provided by the parent — `MatchList` doesn't know about scrolling.

- [ ] **Step 8.1: Implement MatchList**

Create `docker/plugins/yarax_regex/components/MatchList.tsx`:

```tsx
import type { Match } from "../api";

type Props = {
    matches: Match[];
    dimmed: boolean; // true when showing a stale result (regex broke)
    onJump: (match: Match) => void;
};

const MAX_TEXT_PREVIEW = 40;

function truncate(s: string): string {
    if (s.length <= MAX_TEXT_PREVIEW) return s;
    return s.slice(0, MAX_TEXT_PREVIEW - 1) + "…";
}

/**
 * Sidebar list of matches. Click a row → parent scrolls/jumps the
 * sample view to that line. When `dimmed` is true (stale result), the
 * whole list renders at 50% opacity to signal the data is from a
 * previous successful eval.
 */
export function MatchList({ matches, dimmed, onJump }: Props) {
    const wrapperStyle: React.CSSProperties = {
        opacity: dimmed ? 0.5 : 1,
        transition: "opacity 100ms ease",
        overflow: "auto",
        flex: 1,
    };

    if (matches.length === 0) {
        return (
            <div style={wrapperStyle}>
                <div
                    style={{
                        padding: "12px",
                        color: "#888",
                        fontSize: "12px",
                    }}
                >
                    No matches.
                </div>
            </div>
        );
    }

    return (
        <div style={wrapperStyle}>
            {matches.map((m, i) => (
                <button
                    key={`${m.offset}-${i}`}
                    type="button"
                    onClick={() => onJump(m)}
                    title={m.text}
                    style={{
                        display: "block",
                        width: "100%",
                        textAlign: "left",
                        padding: "5px 12px",
                        border: "none",
                        borderBottom: "1px solid #eee",
                        background: "transparent",
                        fontFamily:
                            'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
                        fontSize: "11px",
                        cursor: "pointer",
                    }}
                    onMouseOver={(e) =>
                        (e.currentTarget.style.background = "#fff3a3")
                    }
                    onMouseOut={(e) =>
                        (e.currentTarget.style.background = "transparent")
                    }
                >
                    <span style={{ color: "#888", marginRight: "10px" }}>
                        L{m.line}:{m.column}
                    </span>
                    <span style={{ color: "#b8860b", fontWeight: "bold" }}>
                        {truncate(m.text)}
                    </span>
                </button>
            ))}
        </div>
    );
}
```

- [ ] **Step 8.2: Commit**

```bash
git add docker/plugins/yarax_regex/components/MatchList.tsx
git commit -m "$(cat <<'EOF'
Plugin yarax_regex: add MatchList component

Sidebar list of matches with offset (Lℓ:c) and matched substring.
Click → onJump callback (parent owns scrolling). 'dimmed' prop
renders the list at 50% opacity for the stale-state UX (when the
current regex doesn't compile but we're still showing the previous
successful result).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: SampleWithHighlights component

**Files:**
- Create: `docker/plugins/yarax_regex/components/SampleWithHighlights.tsx`

This is the most involved component. It renders the sample text and overlays a highlight layer based on match offsets. For v0 we render the sample as a simple `<pre>` block with inline `<mark>` spans rather than embedding `react-ace` — Ace markers are fiddly to compute from byte offsets, and a simple HTML approach gives us match→DOM-node mapping for free. Syntax highlighting is a v1 nice-to-have; v0 prioritizes the highlight overlay correctness.

- [ ] **Step 9.1: Implement SampleWithHighlights**

Create `docker/plugins/yarax_regex/components/SampleWithHighlights.tsx`:

```tsx
import { forwardRef, useImperativeHandle, useMemo, useRef } from "react";

import type { Match } from "../api";

type Props = {
    sampleText: string;
    matches: Match[];
    dimmed: boolean;
};

export type SampleWithHighlightsHandle = {
    /** Scroll the given match into view and pulse it briefly. */
    jumpTo: (match: Match) => void;
};

type Segment =
    | { kind: "text"; text: string }
    | { kind: "match"; text: string; matchIndex: number };

/**
 * Walk the sample once and produce alternating text/match segments.
 * Overlapping or touching matches are merged into one highlight span;
 * the click target still maps to the first contributing match.
 */
function buildSegments(sampleText: string, matches: Match[]): Segment[] {
    if (matches.length === 0) return [{ kind: "text", text: sampleText }];

    // Convert byte offsets to character offsets. The sample is a JS string
    // (decoded from UTF-8 bytes by the API layer); a multi-byte UTF-8
    // codepoint occupies 1 char in JS but multiple bytes in the offset.
    // For v0 we approximate: assume ASCII (web threats are usually ASCII
    // or UTF-8 ASCII-compatible). Non-ASCII shifts are a v1 concern; flag
    // it here as a known limitation.
    const sorted = [...matches]
        .map((m, i) => ({ ...m, _i: i }))
        .sort((a, b) => a.offset - b.offset);

    const segments: Segment[] = [];
    let cursor = 0;
    for (const m of sorted) {
        if (m.offset < cursor) continue; // overlap with previous; skip
        if (m.offset > cursor) {
            segments.push({
                kind: "text",
                text: sampleText.slice(cursor, m.offset),
            });
        }
        segments.push({
            kind: "match",
            text: sampleText.slice(m.offset, m.offset + m.length),
            matchIndex: m._i,
        });
        cursor = m.offset + m.length;
    }
    if (cursor < sampleText.length) {
        segments.push({ kind: "text", text: sampleText.slice(cursor) });
    }
    return segments;
}

export const SampleWithHighlights = forwardRef<
    SampleWithHighlightsHandle,
    Props
>(function SampleWithHighlights({ sampleText, matches, dimmed }, ref) {
    const containerRef = useRef<HTMLPreElement>(null);
    const markRefs = useRef<Map<number, HTMLElement>>(new Map());

    const segments = useMemo(
        () => buildSegments(sampleText, matches),
        [sampleText, matches],
    );

    useImperativeHandle(ref, () => ({
        jumpTo: (match: Match) => {
            const idx = matches.findIndex(
                (m) => m.offset === match.offset && m.length === match.length,
            );
            if (idx === -1) return;
            const el = markRefs.current.get(idx);
            if (!el) return;
            el.scrollIntoView({ behavior: "smooth", block: "center" });
            // Pulse: toggle a class for ~700ms
            el.classList.add("yarax-pulse");
            window.setTimeout(() => el.classList.remove("yarax-pulse"), 700);
        },
    }));

    return (
        <>
            <style>{`
                .yarax-mark {
                    background: #fff3a3;
                    border-bottom: 2px solid #b8860b;
                    padding: 1px 0;
                }
                .yarax-mark.yarax-pulse {
                    animation: yarax-pulse-kf 700ms ease;
                }
                @keyframes yarax-pulse-kf {
                    0%   { background: #ffd700; }
                    100% { background: #fff3a3; }
                }
            `}</style>
            <pre
                ref={containerRef}
                style={{
                    margin: 0,
                    padding: "10px 14px",
                    fontFamily:
                        'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
                    fontSize: "12px",
                    lineHeight: "1.6",
                    background: "#fafafa",
                    color: dimmed ? "rgba(34,34,34,0.5)" : "#222",
                    flex: 1,
                    overflow: "auto",
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-all",
                    transition: "color 100ms ease",
                }}
            >
                {segments.map((seg, i) =>
                    seg.kind === "text" ? (
                        <span key={i}>{seg.text}</span>
                    ) : (
                        <mark
                            key={i}
                            ref={(el) => {
                                if (el) markRefs.current.set(seg.matchIndex, el);
                                else markRefs.current.delete(seg.matchIndex);
                            }}
                            className="yarax-mark"
                            style={{ opacity: dimmed ? 0.5 : 1 }}
                        >
                            {seg.text}
                        </mark>
                    ),
                )}
            </pre>
        </>
    );
});
```

- [ ] **Step 9.2: Commit**

```bash
git add docker/plugins/yarax_regex/components/SampleWithHighlights.tsx
git commit -m "$(cat <<'EOF'
Plugin yarax_regex: add SampleWithHighlights component

Renders the sample text in a <pre> with <mark> overlays for each
match. Exposes a jumpTo(match) imperative handle the parent calls
when the user clicks a row in MatchList — scrolls into view and
pulses briefly.

v0 limitation: byte offsets are treated as char offsets. For pure-
ASCII web samples this is correct; for samples with multi-byte
UTF-8 the highlight position shifts. Acceptable for v0; addressed
in v1 alongside corpus eval.

No react-ace integration yet — Ace markers are fiddly with byte
offsets and v0 prioritizes overlay correctness over syntax color.
Syntax highlighting is a v1 nice-to-have.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: YaraXRegexTab top-level component

**Files:**
- Create: `docker/plugins/yarax_regex/components/YaraXRegexTab.tsx`

The top-level component that owns the regex draft, the in-flight tracking, debouncing the network call, and wiring all four child components together. It reads `sample_id` from MWDB's `ObjectContext` and fetches sample bytes the same way `SamplePreview` does (via `api.downloadFile` + `negateBuffer`).

- [ ] **Step 10.1: Implement YaraXRegexTab**

Create `docker/plugins/yarax_regex/components/YaraXRegexTab.tsx`:

```tsx
import { useContext, useEffect, useRef, useState } from "react";

import { APIContext } from "@mwdb-web/commons/api";
import { negateBuffer } from "@mwdb-web/commons/helpers";
import { ObjectContext } from "@mwdb-web/components/ShowObject";
import type { ObjectData } from "@mwdb-web/types/types";

import {
    Diagnostic,
    Match,
    RegexResult,
    evalRegex,
    isOk,
} from "../api";
import { LintStrip } from "./LintStrip";
import { MatchList } from "./MatchList";
import { RegexInput } from "./RegexInput";
import {
    SampleWithHighlights,
    SampleWithHighlightsHandle,
} from "./SampleWithHighlights";

const DEBOUNCE_MS = 200;

/**
 * Top-level YARA-X regex tab. Renders inside an MWDB ObjectTab.
 *
 * Owns:
 *   - regex draft (initialized empty; RegexInput hydrates from localStorage)
 *   - last successful result (matches + diagnostics from last 200 OK ok-status)
 *   - last full result (last 200 OK regardless of status; drives lint strip)
 *   - in-flight tracking + debounce timer + AbortController for request cancellation
 *   - sample bytes (fetched once on mount)
 */
export function YaraXRegexTab() {
    const api = useContext(APIContext);
    const objectContext = useContext(ObjectContext);
    const object = objectContext?.object as ObjectData | undefined;
    const sampleId = object?.id ?? "";

    const [regex, setRegex] = useState("");
    const [lastResult, setLastResult] = useState<RegexResult | null>(null);
    // The "successful" result is the last one whose status was "ok" — drives
    // the dimmed-stale UX when the current regex breaks.
    const [lastOkResult, setLastOkResult] = useState<
        Extract<RegexResult, { status: "ok" }> | null
    >(null);
    const [inFlight, setInFlight] = useState(false);

    const [sampleText, setSampleText] = useState<string>("");
    const [sampleError, setSampleError] = useState<string | null>(null);

    const debounceRef = useRef<number | null>(null);
    const abortRef = useRef<AbortController | null>(null);
    const sampleRef = useRef<SampleWithHighlightsHandle>(null);

    // Fetch the sample bytes once on mount (or when sample_id changes).
    useEffect(() => {
        if (!sampleId) return;
        let cancelled = false;
        (async () => {
            try {
                const resp = await api.downloadFile(sampleId, 1);
                const bytes = negateBuffer(resp.data);
                const text = new TextDecoder("utf-8", { fatal: false }).decode(
                    bytes,
                );
                if (!cancelled) {
                    setSampleText(text);
                    setSampleError(null);
                }
            } catch (e) {
                if (!cancelled) {
                    setSampleError("Failed to load sample.");
                }
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [api, sampleId]);

    // Debounced eval on regex change.
    useEffect(() => {
        if (!sampleId) return;
        if (regex === "") {
            // No call. Clear in-flight state. Keep lastOkResult so the user
            // can briefly clear the input and come back without losing
            // their previous highlights.
            setLastResult(null);
            return;
        }

        if (debounceRef.current !== null) {
            window.clearTimeout(debounceRef.current);
        }
        debounceRef.current = window.setTimeout(async () => {
            if (abortRef.current) abortRef.current.abort();
            const ctrl = new AbortController();
            abortRef.current = ctrl;
            setInFlight(true);
            try {
                const result = await evalRegex(
                    api.axios,
                    { sample_id: sampleId, regex },
                    ctrl.signal,
                );
                setLastResult(result);
                if (isOk(result)) {
                    setLastOkResult(result);
                }
            } catch (e: any) {
                if (e?.name === "CanceledError" || e?.name === "AbortError") {
                    return; // superseded by a newer regex
                }
                setLastResult({
                    status: "compile_error",
                    diagnostics: [
                        {
                            severity: "error",
                            code: "request_failed",
                            message:
                                e?.response?.data?.message ||
                                e?.message ||
                                "Request failed.",
                        },
                    ],
                });
            } finally {
                if (abortRef.current === ctrl) setInFlight(false);
            }
        }, DEBOUNCE_MS);

        return () => {
            if (debounceRef.current !== null) {
                window.clearTimeout(debounceRef.current);
            }
        };
    }, [api, regex, sampleId]);

    const isStale =
        lastResult !== null &&
        lastResult.status !== "ok" &&
        lastOkResult !== null;
    const matches: Match[] =
        lastResult && isOk(lastResult)
            ? lastResult.matches
            : isStale && lastOkResult
              ? lastOkResult.matches
              : [];
    const diagnostics: Diagnostic[] = lastResult
        ? lastResult.diagnostics
        : [];
    const matchCount: number | null =
        lastResult && isOk(lastResult) ? lastResult.matches.length : null;

    if (sampleError) {
        return (
            <div style={{ padding: "20px", color: "#dc3545" }}>
                {sampleError}
            </div>
        );
    }

    return (
        <div
            style={{
                display: "flex",
                flexDirection: "column",
                height: "70vh",
            }}
        >
            <div style={{ padding: "10px 14px", background: "#fafafa" }}>
                <RegexInput
                    sampleId={sampleId}
                    value={regex}
                    onChange={setRegex}
                />
            </div>
            <LintStrip
                diagnostics={diagnostics}
                inFlight={inFlight}
                matchCount={matchCount}
            />
            <div
                style={{
                    display: "flex",
                    flex: 1,
                    minHeight: 0,
                    borderTop: "1px solid #eee",
                }}
            >
                <div style={{ flex: 1, display: "flex", minWidth: 0 }}>
                    <SampleWithHighlights
                        ref={sampleRef}
                        sampleText={sampleText}
                        matches={matches}
                        dimmed={isStale}
                    />
                </div>
                <div
                    style={{
                        width: "240px",
                        background: "#f8f9fa",
                        borderLeft: "1px solid #eee",
                        display: "flex",
                        flexDirection: "column",
                    }}
                >
                    <h4
                        style={{
                            fontSize: "10px",
                            textTransform: "uppercase",
                            color: "#888",
                            letterSpacing: "1px",
                            margin: "10px 14px 6px",
                        }}
                    >
                        Matches{" "}
                        {matches.length > 0 && (
                            <span style={{ color: "#444" }}>
                                ({matches.length})
                            </span>
                        )}
                    </h4>
                    <MatchList
                        matches={matches}
                        dimmed={isStale}
                        onJump={(m) => sampleRef.current?.jumpTo(m)}
                    />
                </div>
            </div>
        </div>
    );
}
```

- [ ] **Step 10.2: Commit**

```bash
git add docker/plugins/yarax_regex/components/YaraXRegexTab.tsx
git commit -m "$(cat <<'EOF'
Plugin yarax_regex: add YaraXRegexTab top-level component

Owns the regex draft, debounced (200ms) network call with
AbortController-based cancellation of stale requests, sample
bytes fetched once via api.downloadFile + negateBuffer, and
the dimmed-stale UX when the current regex doesn't compile.

The 'lastOkResult' state lets matches + highlights stay visible
(at 50% opacity) when the user is mid-edit on an invalid regex,
so they don't lose their place.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Wire the entrypoint, build the web image, manually verify

**Files:**
- Create: `docker/plugins/yarax_regex/index.tsx`

Final wiring task: the plugin entrypoint registers a new `ObjectTab` via the `sampleTabsAfter` extension. After this, rebuild the web image and verify the tab renders against a real sample in the browser.

- [ ] **Step 11.1: Implement index.tsx**

Create `docker/plugins/yarax_regex/index.tsx`:

```tsx
import { faMagnifyingGlassChart } from "@fortawesome/free-solid-svg-icons";

import { ObjectTab } from "@mwdb-web/components/ShowObject";

import { YaraXRegexTab } from "./components/YaraXRegexTab";

export default () => ({
    sampleTabsAfter: [
        () => (
            <ObjectTab
                tab="yarax-regex"
                label="YARA-X Regex"
                icon={faMagnifyingGlassChart}
                component={YaraXRegexTab}
            />
        ),
    ],
});
```

- [ ] **Step 11.2: Rebuild the web image**

```bash
docker compose -f docker-compose-dev.yml build mwdb-web
```

Expected: the build runs `npm install` against `/app/plugins/yarax_regex` (resolved via `find /app/plugins -name 'package.json'`). Look for `+ @mwdb-web/plugin-yarax-regex@0.1.0` in the install output, then a successful Vite dev build.

If the build fails with a TypeScript error about missing types from `@mwdb-web/components` or `@mwdb-web/commons`, that means the plugin's TS resolution can't find the alias. Inspect the parent web tsconfig — those aliases come from the main app's `vite.config.ts` and `tsconfig.json`. If the plugin's tsconfig is correctly extending the parent's, both resolution paths should work because Vite owns the alias resolution at build time, and the plugin's TS setup is for IDE feedback only.

- [ ] **Step 11.3: Restart the web container**

```bash
docker compose -f docker-compose-dev.yml up -d mwdb-web
```

Expected: container starts, dev server is reachable at http://localhost/.

- [ ] **Step 11.4: Manually verify the tab loads**

Open http://localhost/ in a browser. Log in. Navigate to the sample uploaded in task 5.6 (`/file/<sample_id>`). Confirm:

1. A new tab labeled **YARA-X Regex** is present alongside Details, Relations, Preview, Static config.
2. Clicking it shows: regex input on top, lint strip beneath, sample text on the left, "Matches" sidebar on the right.
3. Sample text is the PHP shell content from task 5.6.

- [ ] **Step 11.5: Manually verify live regex eval works**

Type `\$\w+` in the regex input. Within ~250 ms after you stop typing:

1. Lint strip shows `✓ valid · 4 matches` (or similar count).
2. The four `$payload` / `$cmd` occurrences in the sample text are highlighted in yellow.
3. Match list sidebar shows four rows with `Lℓ:c` offsets.
4. Clicking a row scrolls the sample to that line and pulses the highlight.

- [ ] **Step 11.6: Manually verify the stale-state UX**

Edit the regex to something invalid like `\$[`. Confirm:

1. Lint strip shows the yara-x error message in red.
2. Sample text and match list go dim (50% opacity) but still show the previous matches.
3. Restoring the regex to `\$\w+` un-dims and updates.

- [ ] **Step 11.7: Manually verify draft persistence**

Type a regex. Reload the page. Open the YARA-X Regex tab again. Confirm the regex is still there.

Open a *different* sample. Confirm the regex input is empty (drafts are per-sample).

- [ ] **Step 11.8: Commit**

```bash
git add docker/plugins/yarax_regex/index.tsx
git commit -m "$(cat <<'EOF'
Plugin yarax_regex: wire entrypoint to register the sample tab

Registers a new ObjectTab via sampleTabsAfter extension. With this
commit the v0 panel is end-to-end working: sample tab appears in
MWDB, regex input is live, sample bytes are fetched and rendered
with overlay highlights, and the match list jumps to lines.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Manual end-to-end checklist

This task has no code. It's the spec's §8.3 checklist run by hand against the dev stack. Mark each item only after you have observed it with your own eyes.

- [ ] **Step 12.1: Auth — unauthenticated request rejected**

```bash
curl -i -X POST http://localhost/api/yarax/regex \
  -H "Content-Type: application/json" \
  -d '{"sample_id":"abc","regex":"foo"}'
```

Expected: HTTP 401. Without the auth header MWDB rejects before reaching our handler.

- [ ] **Step 12.2: Auth — unauthorized sample returns 404**

Upload a second sample as a *different* user, get its sample_id. As the first user, hit the regex endpoint with that sample_id. Expected: HTTP 404 ("Sample not found or you don't have access to it"). MWDB does not differentiate "doesn't exist" from "no access" by design — that's the existing convention.

- [ ] **Step 12.3: Cross-language samples render**

Upload a JS sample, an HTML sample, and a SQL sample (each a few lines). For each: open the YARA-X Regex tab, type a simple regex, verify highlights appear. The sample text rendering is plain `<pre>` for v0 (no syntax highlighting); the highlights should still position correctly.

For SQL: language detection in `SamplePreview.tsx` doesn't map `.sql` — but our v0 doesn't use that detection (we render as `<pre>`). This is fine for v0; revisit when we add syntax highlighting in v1.

- [ ] **Step 12.4: Multi-byte UTF-8 sample (known limitation flagged)**

Upload a sample containing non-ASCII bytes (e.g. a PHP file with a `// 日本語` comment). Run a regex that matches characters before and after the non-ASCII span. Observe whether the highlight is shifted off-by-N from the visual match.

If shifted: that's the v0 limitation called out in `SampleWithHighlights.tsx` task 9 — byte offsets vs char offsets. Document the observed offset error in the v1 backlog. Don't fix in v0.

- [ ] **Step 12.5: Performance — typical sample**

Open a 50–500 KB PHP shell sample. Type a moderately complex regex (`\$_(GET|POST|REQUEST|COOKIE)\[`). Watch the lint strip's `evaluating…` indicator. Expected: the indicator appears briefly (under 350 ms total from last keystroke to result rendered).

- [ ] **Step 12.6: Performance — large sample (cap)**

If a 50+ MB sample is available in the corpus, open it in the panel. Expected: lint strip shows `✗ sample is N bytes; the live panel only scans samples up to 52428800 bytes`. Match list and highlights stay empty.

If no sample at that size exists, skip — the unit test in task 4 covers this path.

- [ ] **Step 12.7: Logging — opt-in regex content**

Confirm default backend logs do NOT contain the regex content:
```bash
docker compose -f docker-compose-dev.yml logs mwdb 2>&1 | grep -i yarax | tail -20
```

Expected: any per-request log lines reference `sample_id` and `regex_length` but NOT the regex text. (This is implicit in `runner.py` which never logs; verify by inspection.)

If you want regex content in logs for a debugging session, MWDB uses standard Python logging — set the `mwdb.plugin.yarax_regex` logger to DEBUG via the existing logging config. v0 does not implement debug-level regex content logging itself; if a future task wants that, add it explicitly with a code change, not by twiddling a config.

- [ ] **Step 12.8: Wrap-up**

If all the above succeeded, the v0 plan is complete. If any step failed:
- File the failure as an issue in the team's tracker
- Decide if it's a v0 blocker or a v1 backlog item
- For v0 blockers: fix in a new commit on this branch before declaring done

---

## Out-of-scope reminders (matches spec §2 and §10)

These are NOT part of v0. If you find yourself adding any of them while implementing the plan, stop and verify with the spec author:

- Snippet library / autocomplete for vetted character classes
- Team-specific lints (`\w → [a-z_\x80-\xff]…` family)
- Mining `.yara` repo or the malwareresearchuniversity blog
- Decoded view for `eval(base64_decode(...))` chains
- Concatenation-aware matching
- Multi-sample / corpus eval; hit-miss matrix
- Full YARA rule editing (multi-string, conditions)
- Regression testing against historical hits
- Goodware / negative corpus checks
- AI features
- Server-side regex storage / history / sharing
- Syntax highlighting (PHP/JS/SQL/HTML) for the sample view
- Cypress E2E coverage
- Rate limiting beyond client debounce
