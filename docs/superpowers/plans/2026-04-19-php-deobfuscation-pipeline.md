# PHP Deobfuscation & Normalization Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Batch CLI command that sends all PHP samples in mwdb to the deobfuscation sandbox, stores normalized code as child TextBlobs, and computes normalized-TLSH for semantic similarity matching.

**Architecture:** A new `mwdb-core normalize-php <sandbox-url>` Click subcommand in `mwdb/cli/` that queries PHP files from the database, POSTs each to the sandbox's HTTP API (variable deobfuscation with beautify fallback), creates a child TextBlob with the normalized code, and stores a `normalized_tlsh` attribute on the original File. The sandbox runs as a Docker service via a compose override file.

**Tech Stack:** Python 3.12, Click, Flask app context, SQLAlchemy 1.4, `requests` (HTTP client — already a dependency), `io.BytesIO` + `calc_tlsh` (from subsystem 1), mwdb model layer (`TextBlob.get_or_create`, `Object.add_attribute`, `File.open`). Sandbox: PHP 8.2 + Apache Docker image with nikic/php-parser.

**Companion spec:** `docs/superpowers/specs/2026-04-19-php-deobfuscation-pipeline-design.md` (commit `4232fa3`).

---

## File structure

### New files
- `mwdb/cli/normalize_php.py` — the Click subcommand (~150 lines)
- `docker-compose-sandbox.yml` — compose override to run the sandbox as a local service

### Modified files
- `mwdb/cli/cli.py` — two lines: import + `cli.add_command()`

---

## Critical API detail

Both sandbox endpoints (`/var_deobfuscate_web.php` and `/beautify.php`) read `$_POST['phpCode']` — this means the HTTP request must be **form-encoded** (`data={"phpCode": code}`), NOT JSON (`json={"phpCode": code}`). The response is JSON.

- Deobfuscate success: `{"success": true, "deobfuscated_code": "<?php\n...", ...}`
- Beautify success: `{"success": true, "beautified_code": "<?php\n...", ...}`
- Failure: `{"success": false, "error": "..."}`

---

## Task 1: Create the docker-compose sandbox override

**Files:**
- Create: `docker-compose-sandbox.yml`

- [ ] **Step 1.1: Create the compose override file**

Create `docker-compose-sandbox.yml` in the repo root with exactly this content:

```yaml
services:
  sandbox:
    build:
      context: ../waffle-makers-tooling/security-research-malware-sandbox
    environment:
      - PHP_MEMORY_LIMIT=512M
      - PHP_MAX_EXECUTION_TIME=300
      - PHP_UPLOAD_MAX_FILESIZE=50M
      - PHP_POST_MAX_SIZE=50M
```

This is intentionally minimal — no Traefik, no custom network, no security hardening (those are for the production deployment at `sandbox.wafflemakers.xyz`). The service joins the default network of the mwdb dev compose, making it reachable as `http://sandbox/` (Apache on port 80) from the mwdb container.

The `context` path is relative to the repo root — `../waffle-makers-tooling/security-research-malware-sandbox` sits alongside the `mwdb-core-wafflemakers` directory.

- [ ] **Step 1.2: Start the sandbox and verify it responds**

```bash
docker compose -f docker-compose-dev.yml -f docker-compose-sandbox.yml up -d sandbox
```

Wait for the container to start (should be fast — the sandbox Dockerfile is simple):

```bash
docker compose -f docker-compose-dev.yml -f docker-compose-sandbox.yml ps sandbox
```

Expected: `sandbox` service is `Up` or `running`.

Verify the sandbox responds to a health check from inside the mwdb container:

```bash
docker compose -f docker-compose-dev.yml -f docker-compose-sandbox.yml run --rm \
  --entrypoint "" mwdb python -c "
import requests
r = requests.post('http://sandbox/var_deobfuscate_web.php', data={'phpCode': '<?php echo 1+1; ?>'})
print(r.status_code, r.json().get('success'))
"
```

Expected: `200 True`. If this fails with connection refused, the sandbox may take a few seconds to start Apache — retry once.

If the `build` step fails (missing Dockerfile, context path wrong), verify that `../waffle-makers-tooling/security-research-malware-sandbox/Dockerfile` exists relative to the mwdb repo root.

- [ ] **Step 1.3: Commit**

```bash
git add docker-compose-sandbox.yml
git commit -m "$(cat <<'EOF'
Add docker-compose-sandbox.yml for PHP deobfuscation service

Compose override that runs the PHP malware sandbox from
../waffle-makers-tooling/security-research-malware-sandbox as a
local Docker service. Used by the normalize-php CLI command for
batch deobfuscation. No Traefik or security hardening — this is
for local development only.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Create the normalize-php CLI module

**Files:**
- Create: `mwdb/cli/normalize_php.py`

- [ ] **Step 2.1: Create the module**

Create `mwdb/cli/normalize_php.py` with exactly this content:

```python
import io
import os
import time

import click
import requests
from flask import g
from flask.cli import with_appcontext

from mwdb.core.config import app_config
from mwdb.core.tlsh import calc_tlsh

PHP_EXTENSIONS = {".php", ".phtml", ".php5", ".php7", ".inc"}
ERROR_THRESHOLD = 100


def _is_php_file(file_name):
    if not file_name:
        return False
    _, ext = os.path.splitext(file_name.lower())
    return ext in PHP_EXTENSIONS


def _normalize_via_sandbox(sandbox_url, php_code):
    try:
        r = requests.post(
            f"{sandbox_url}/var_deobfuscate_web.php",
            data={"phpCode": php_code},
            timeout=60,
        )
        if r.ok:
            data = r.json()
            if data.get("success") and data.get("deobfuscated_code"):
                return data["deobfuscated_code"]
    except (requests.RequestException, ValueError):
        pass

    try:
        r = requests.post(
            f"{sandbox_url}/beautify.php",
            data={"phpCode": php_code},
            timeout=60,
        )
        if r.ok:
            data = r.json()
            if data.get("success") and data.get("beautified_code"):
                return data["beautified_code"]
    except (requests.RequestException, ValueError):
        pass

    return None


def _ensure_attribute_definition(key):
    from mwdb.model import db
    from mwdb.model.attribute import AttributeDefinition

    existing = (
        db.session.query(AttributeDefinition)
        .filter(AttributeDefinition.key == key)
        .first()
    )
    if existing:
        return
    defn = AttributeDefinition(
        key=key,
        label=key,
        description="TLSH hash of deobfuscated/normalized PHP code",
        url_template="",
        rich_template="",
        example_value="",
    )
    db.session.add(defn)
    db.session.commit()
    click.echo(f"Created attribute definition: {key}")


@click.command("normalize-php")
@with_appcontext
@click.argument("sandbox_url")
def normalize_php(sandbox_url):
    """Batch-normalize PHP samples via the deobfuscation sandbox."""
    from mwdb.model import db
    from mwdb.model.attribute import Attribute
    from mwdb.model.blob import TextBlob
    from mwdb.model.file import File

    g.auth_user = None

    _ensure_attribute_definition("normalized_tlsh")

    sandbox_url = sandbox_url.rstrip("/")

    click.echo("Querying PHP files...")
    all_files = db.session.query(File).all()
    php_files = [f for f in all_files if _is_php_file(f.file_name)]
    click.echo(f"Found {len(php_files)} PHP files (of {len(all_files)} total).")

    already_done = 0
    to_process = []
    for f in php_files:
        has_attr = (
            db.session.query(Attribute)
            .filter(Attribute.object_id == f.id, Attribute.key == "normalized_tlsh")
            .first()
        )
        if has_attr:
            already_done += 1
        else:
            to_process.append(f)

    click.echo(
        f"  Already normalized: {already_done}, to process: {len(to_process)}"
    )

    if not to_process:
        click.echo("Nothing to process.")
        return

    processed = 0
    skipped_no_result = 0
    errors = 0
    start_time = time.time()
    total = len(to_process)

    for i, file_obj in enumerate(to_process, 1):
        try:
            fh = file_obj.open()
            try:
                php_code = fh.read().decode("utf-8", errors="replace")
            finally:
                file_obj.close(fh)

            normalized = _normalize_via_sandbox(sandbox_url, php_code)
            if not normalized:
                click.echo(
                    f"[{i}/{total}] Skipped (sandbox returned no result) "
                    f"{file_obj.file_name}"
                )
                skipped_no_result += 1
                continue

            blob_obj, _ = TextBlob.get_or_create(
                content=normalized,
                blob_name=f"{file_obj.file_name}.normalized",
                blob_type="normalized-php",
                share_3rd_party=False,
                parent=file_obj,
            )

            tlsh_hash = calc_tlsh(
                io.BytesIO(normalized.encode("utf-8"))
            )

            if tlsh_hash:
                file_obj.add_attribute(
                    "normalized_tlsh",
                    tlsh_hash,
                    commit=False,
                    check_permissions=False,
                )

            db.session.commit()

            if hasattr(g, "scheduled_hooks"):
                g.scheduled_hooks = []

            tlsh_display = tlsh_hash or "n/a"
            click.echo(
                f"[{i}/{total}] Normalized {file_obj.file_name} "
                f"(normalized_tlsh: {tlsh_display})"
            )
            processed += 1

        except Exception as e:
            db.session.rollback()
            click.echo(
                f"[{i}/{total}] ERROR {file_obj.file_name}: {e}",
                err=True,
            )
            errors += 1
            if errors >= ERROR_THRESHOLD:
                click.echo(
                    f"Error threshold ({ERROR_THRESHOLD}) reached, aborting.",
                    err=True,
                )
                break

    elapsed = time.time() - start_time
    click.echo("\nSummary:")
    click.echo(f"  Normalized: {processed}")
    click.echo(f"  Already done (skipped): {already_done}")
    click.echo(f"  Sandbox returned no result: {skipped_no_result}")
    click.echo(f"  Errors: {errors}")
    click.echo(f"  Wall-clock: {elapsed:.0f}s")
```

Key design decisions in this code:

- **`_normalize_via_sandbox`** tries `/var_deobfuscate_web.php` first (deeper analysis), falls back to `/beautify.php` (lighter). Both use `data={"phpCode": ...}` (form-encoded POST — the PHP endpoints read `$_POST['phpCode']`, not JSON body).
- **60-second timeout per request** — generous for large files; the sandbox's PHP_MAX_EXECUTION_TIME is 300s, so the Python side won't time out before PHP does.
- **`_is_php_file`** filters by extension (`.php`, `.phtml`, `.php5`, `.php7`, `.inc`). Not by libmagic `file_type`, which is unreliable for obfuscated PHP.
- **Idempotency** via checking for existing `normalized_tlsh` attribute before processing. Re-runs skip already-done files.
- **`File.open()` / `File.close(fh)`** reads from mwdb's storage (disk or S3) — this is the canonical way to read file content from the model layer.
- **`TextBlob.get_or_create(..., parent=file_obj)`** creates the child relation. If two different Files normalize to identical code, they share the same TextBlob — this is exactly the "same payload, different obfuscation" detection.
- **`calc_tlsh`** on the normalized bytes reuses the wrapper from subsystem 1. It returns `None` for very short normalized outputs (< ~50 bytes), which is fine — the TextBlob is still created even without a TLSH hash.
- **`g.auth_user = None`** and `g.scheduled_hooks = []` cleanup — same safety pattern as the import tool.

- [ ] **Step 2.2: Verify ruff**

Run: `/Users/fioa8c/Library/Python/3.11/bin/uv run ruff check mwdb/cli/normalize_php.py && /Users/fioa8c/Library/Python/3.11/bin/uv run ruff format --check mwdb/cli/normalize_php.py`

Expected: passes. If formatting issues, run `uv run ruff format mwdb/cli/normalize_php.py` and re-verify.

- [ ] **Step 2.3: Commit**

```bash
git add mwdb/cli/normalize_php.py
git commit -m "$(cat <<'EOF'
Add normalize-php CLI command for batch PHP deobfuscation

Sends PHP files to the sandbox for AST-based deobfuscation, stores
normalized code as child TextBlobs, and computes normalized_tlsh
attribute for semantic similarity matching. Tries variable
deobfuscation first, falls back to beautification.

See docs/superpowers/specs/2026-04-19-php-deobfuscation-pipeline-design.md

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Register the subcommand in cli.py

**Files:**
- Modify: `mwdb/cli/cli.py`

- [ ] **Step 3.1: Add the import**

Open `mwdb/cli/cli.py`. The existing imports at lines 9-12 are:

```python
from .base import AppDefaultGroup, CustomFlaskGroup, create_app, logger
from .configuration import create_configuration
from .database import configure_database
from .import_threat_library import import_jetpack_threat_library
```

Add one line:

```python
from .base import AppDefaultGroup, CustomFlaskGroup, create_app, logger
from .configuration import create_configuration
from .database import configure_database
from .import_threat_library import import_jetpack_threat_library
from .normalize_php import normalize_php
```

- [ ] **Step 3.2: Add the registration**

At the end of the file, the existing last line is:

```python
cli.add_command(import_jetpack_threat_library)
```

Add one line below:

```python
cli.add_command(import_jetpack_threat_library)
cli.add_command(normalize_php)
```

- [ ] **Step 3.3: Verify ruff**

Run: `/Users/fioa8c/Library/Python/3.11/bin/uv run ruff check mwdb/cli/cli.py && /Users/fioa8c/Library/Python/3.11/bin/uv run ruff format --check mwdb/cli/cli.py`

- [ ] **Step 3.4: Commit**

```bash
git add mwdb/cli/cli.py
git commit -m "$(cat <<'EOF'
Register normalize-php in CLI

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Fixture test with a single obfuscated sample

Validates the end-to-end pipeline before running against the full corpus.

- [ ] **Step 4.1: Verify the sandbox is running**

```bash
docker compose -f docker-compose-dev.yml -f docker-compose-sandbox.yml up -d sandbox
docker compose -f docker-compose-dev.yml -f docker-compose-sandbox.yml ps sandbox
```

Expected: sandbox service is running.

- [ ] **Step 4.2: Upload an obfuscated PHP file to mwdb**

Create a test file with clear obfuscation that the var_deobfuscator can resolve:

```bash
cat > /tmp/obfuscated-test.php << 'HEREDOC'
<?php
$a = 'ev';
$b = 'al';
$c = $a . $b;
$d = 'base' . '64_' . 'decode';
$c($d('ZWNobyAiSGVsbG8gV29ybGQiOw=='));
?>
HEREDOC
```

Upload it via the API:

```bash
TOKEN=$(curl -sf -X POST http://127.0.0.1/api/auth/login -H 'Content-Type: application/json' -d "{\"login\":\"admin\",\"password\":\"$(grep ^MWDB_ADMIN_PASSWORD= mwdb-vars.env | cut -d= -f2)\"}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')

curl -sf -H "Authorization: Bearer $TOKEN" http://127.0.0.1/api/file \
  -F "file=@/tmp/obfuscated-test.php;filename=obfuscated-test.php" \
  -F 'options={"upload_as":"*"}' | python3 -c 'import sys,json; d=json.load(sys.stdin); print(f"Uploaded: {d[\"sha256\"][:16]}...")'
```

- [ ] **Step 4.3: Run the normalizer**

```bash
MWDB_ENABLE_HOOKS=0 docker compose -f docker-compose-dev.yml -f docker-compose-sandbox.yml run --rm \
  --entrypoint "" mwdb /app/.venv/bin/mwdb-core normalize-php http://sandbox
```

Expected output (approximately):

```
Created attribute definition: normalized_tlsh
Querying PHP files...
Found NNNNN PHP files (of NNNNN total).
  Already normalized: 0, to process: NNNNN
[1/NNNNN] Normalized obfuscated-test.php (normalized_tlsh: T1... or n/a)
...
```

The command will process ALL PHP files — including the 14,781 imported samples. For this fixture test step, that's fine if you're ready to run the full batch. If you want to test just the one file, temporarily rename or use a smaller mwdb instance. But since the full batch is the goal anyway, letting it run is efficient.

**If you want to test ONLY the fixture file:** add a `--limit 1` flag or just let it run and check the first few lines of output to confirm the obfuscated-test.php was processed correctly. Then Ctrl+C and re-run for the full batch in Task 5.

- [ ] **Step 4.4: Verify the fixture sample's results**

After the normalizer processes `obfuscated-test.php`, verify via the API:

```bash
# Find the obfuscated-test.php sample
SHA=$(curl -sf -H "Authorization: Bearer $TOKEN" http://127.0.0.1/api/search \
  -X POST -H 'Content-Type: application/json' \
  -d '{"query":"file.name:obfuscated-test.php"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)[0]["id"])')

echo "=== Check for child TextBlob ==="
curl -sf -H "Authorization: Bearer $TOKEN" "http://127.0.0.1/api/file/$SHA/relations" | python3 -c '
import sys, json
d = json.load(sys.stdin)
children = d.get("children", [])
print(f"Children: {len(children)}")
for c in children:
    print(f"  type={c.get("type")} name={c.get("blob_name", c.get("file_name", "?"))}")
'

echo "=== Check normalized_tlsh attribute ==="
curl -sf -H "Authorization: Bearer $TOKEN" "http://127.0.0.1/api/file/$SHA/attribute" | python3 -m json.tool
```

Expected:
- At least 1 child of type `text_blob` with a name like `obfuscated-test.php.normalized`
- An attribute with key `normalized_tlsh` and a T1-prefixed value (or no attribute if the normalized code was too small for TLSH — unlikely for this test file)

Optionally, view the TextBlob content in the SPA: navigate to the File, click the child blob, verify the normalized PHP is human-readable (variable references resolved, code formatted).

- [ ] **Step 4.5: No commit**

Pure verification.

---

## Task 5: Run the full batch normalization

- [ ] **Step 5.1: Execute the normalizer against all samples**

If Task 4 ran the normalizer and you let it complete, this step is already done — skip to step 5.2. Otherwise:

```bash
MWDB_ENABLE_HOOKS=0 docker compose -f docker-compose-dev.yml -f docker-compose-sandbox.yml run --rm \
  --entrypoint "" mwdb /app/.venv/bin/mwdb-core normalize-php http://sandbox
```

Expected runtime: 12-15 minutes for ~14,781 PHP samples at ~50ms per sandbox call.

Watch for:
- Progress lines scrolling normally.
- No sustained burst of errors (occasional sandbox failures are expected for deeply malformed PHP).
- `Sandbox returned no result` for some files is normal — some PHP files may be too malformed for even the beautifier.

- [ ] **Step 5.2: Record the summary**

Copy the final summary:

```
Summary:
  Normalized: <number>
  Already done (skipped): <number>
  Sandbox returned no result: <number>
  Errors: <number>
  Wall-clock: <number>s
```

Verify: `Normalized + Already done + Sandbox no result + Errors ≈ total PHP files`.

- [ ] **Step 5.3: No commit**

Operational — data lives in the DB.

---

## Task 6: Post-batch verification

- [ ] **Step 6.1: Check counts in the database**

```bash
docker compose -f docker-compose-dev.yml -f docker-compose-sandbox.yml exec postgres psql -U mwdb -d mwdb -c "
SELECT
  'TextBlobs (normalized-php)' as metric,
  count(*) as value
FROM object
WHERE type = 'text_blob'
UNION ALL
SELECT
  'Files with normalized_tlsh' as metric,
  count(DISTINCT a.object_id) as value
FROM attribute a
WHERE a.key = 'normalized_tlsh';
"
```

Expected: TextBlob count close to the `Normalized` number from the summary. `Files with normalized_tlsh` may be slightly lower (some normalized outputs < 50 bytes won't produce a TLSH hash).

- [ ] **Step 6.2: Spot-check a known obfuscated threat in the SPA**

Open `http://127.0.0.1/` in a browser. Log in with admin credentials.

Find a threat likely to be obfuscated: search `attribute.jpop_threat_name:*` and click through a few results looking for one with a `normalized-php` child TextBlob. Open the TextBlob and verify the code is human-readable.

Alternatively, search `attribute.normalized_tlsh:*` to find any file that has been normalized.

- [ ] **Step 6.3: Test semantic similarity**

Pick two files that you know are variants of the same malware family. Check their `normalized_tlsh` attributes. If the hashes match (or are very similar — first few characters identical), the normalization pipeline is working as intended: different obfuscation, same underlying code, same normalized TLSH.

If you don't have a known variant pair, search for any `normalized_tlsh` value that appears more than once:

```bash
docker compose -f docker-compose-dev.yml -f docker-compose-sandbox.yml exec postgres psql -U mwdb -d mwdb -c "
SELECT a.value as normalized_tlsh, count(*) as files
FROM attribute a
WHERE a.key = 'normalized_tlsh'
GROUP BY a.value
HAVING count(*) > 1
ORDER BY files DESC
LIMIT 10;
"
```

Each row represents a group of files that normalize to TLSH-identical code — these are likely variants of the same malware family with different obfuscation.

- [ ] **Step 6.4: Stop the sandbox**

```bash
docker compose -f docker-compose-dev.yml -f docker-compose-sandbox.yml stop sandbox
```

The sandbox is only needed during normalization runs. Stopping it frees resources.

- [ ] **Step 6.5: No commit**

Operational verification.

---

## Final check

- [ ] `git log --oneline` shows 3 new commits (Tasks 1, 2, 3).
- [ ] `git status` is clean.
- [ ] Normalization summary: `Normalized + Sandbox no result + Errors ≈ total PHP files`.
- [ ] At least one spot-checked TextBlob shows readable deobfuscated PHP.
- [ ] The semantic-similarity query in Task 6.3 found at least one cluster of files sharing a `normalized_tlsh` — confirming the pipeline detects code similarity across obfuscation variants.
- [ ] Sandbox service is stopped.
