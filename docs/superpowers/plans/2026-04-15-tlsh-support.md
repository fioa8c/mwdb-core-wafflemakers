# TLSH Hashing Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add TLSH as a new indexed hash column on `File`, computed on upload alongside md5/sha1/sha256/sha512/ssdeep/crc32, and expose it via the file detail API, the Luqum search grammar (exact match), and the SPA sample details page.

**Architecture:** One new pypi dependency (`py-tlsh==4.12.1`), one new thin wrapper module (`mwdb/core/tlsh.py`) mirroring the structure of `mwdb/core/ssdeep.py`, one new nullable indexed `String(72)` column on the `file` table via Alembic migration, and additive-only edits to the schema/search/frontend layers that already expose ssdeep. No config flag (TLSH is always on), no backfill of existing rows, no similar-files endpoint in this subsystem.

**Tech Stack:** Python 3.12, Flask 3 + Flask-RESTful + Flask-Migrate/Alembic, SQLAlchemy 1.4, Marshmallow 3, Luqum-based search DSL, React 18 + TypeScript 5 + Vite, `py-tlsh` 4.12.1 C++ extension (compiled under musl in the Alpine builder stage).

**Companion spec:** `docs/superpowers/specs/2026-04-15-tlsh-support-design.md` (commit `5b34d68`).

---

## File structure

### New files
- `mwdb/core/tlsh.py` — thin wrapper exposing `calc_tlsh(stream) -> str | None`. Analogous to `mwdb/core/ssdeep.py` but without the ctypes/config-flag ceremony because `py-tlsh` is a pinned pypi dependency.
- `mwdb/model/migrations/versions/<new>_add_tlsh_column_to_file.py` — Alembic migration that adds the `tlsh` column and `ix_file_tlsh` index. `down_revision = "01dea56ffaf7"`.

### Modified files

**Backend:**
- `pyproject.toml` — add `py-tlsh==4.12.1` to the dependency list.
- `uv.lock` — regenerated via `uv lock`.
- `deploy/docker/Dockerfile` — add `g++ musl-dev python3-dev` to the builder stage so `uv sync` can compile py-tlsh from source under musl.
- `mwdb/core/util.py` — re-export `calc_tlsh` from `mwdb.core.tlsh`, mirroring how `calc_ssdeep` is imported.
- `mwdb/model/file.py` — new `tlsh` column on `File`; wire `calc_tlsh(file_stream)` into `File.get_or_create()`.
- `mwdb/schema/file.py` — add `tlsh` to `FileItemResponseSchema` only (NOT `FileListItemResponseSchema`).
- `mwdb/core/search/mappings.py` — register `"tlsh": StringField(File.tlsh)` in the file field mapping.
- `tests/backend/test_api.py` — two new integration tests (hash present on normal upload, hash null on tiny file).
- `tests/backend/test_search.py` — one new integration test (search round-trip).

**Frontend:**
- `mwdb/web/src/types/types.ts` — add `tlsh: string | null` to the file type.
- `mwdb/web/src/components/SampleDetails.tsx` — new table row mirroring the ssdeep row, with a `not computed` fallback when `object.tlsh` is null.
- `mwdb/web/src/components/RecentView/common/useQuerySuggestions.ts` — new `tlsh` entry in the file query-suggestions dictionary.

---

## Note on TDD cadence for this plan

The `tests/backend/` suite is HTTP integration tests against a live docker-compose stack, not unit tests. A "write failing test, run to confirm it fails, implement, run to confirm it passes, commit" cycle per task would require `docker compose up --build` and a 2–5 minute rebuild per step — that's hostile to bite-sized granularity.

Realistic cadence for this plan:
- **Code tasks (1–9, 12–14):** static validation per task via `ruff check` / `ruff format --check` / `npx tsc` / `npx prettier --check`. These are the per-task "did I break syntax" gates.
- **Test authoring (task 10):** test code is written but not executed until task 11.
- **Integration verification (task 11):** one dedicated task that runs the full e2e backend suite against the rebuilt stack. This is where the backend changes get their actual test pass.
- **Manual UI verification (task 16):** the spec's §8.3 checklist, driven by hand in a browser — there is no automated frontend test bar for SampleDetails to meet.

This matches how the CI pipeline in `.github/workflows/build.yml` is structured: lint stages run in isolation, e2e tests run against a built image, frontend unit tests run in a third image. This plan's cadence mirrors that reality.

Each task ends with a commit. Commits are small and independently buildable so the tree stays green between tasks, which matters if execution is subagent-driven (the next subagent starts from the last good commit).

---

## Task 1: Add py-tlsh dependency

**Files:**
- Modify: `pyproject.toml` (dependencies list, lines 22-51)
- Modify: `uv.lock` (regenerated)

- [ ] **Step 1.1: Add the dependency in alphabetical position**

Open `pyproject.toml`. Find the existing `pyjwt` line:

```toml
    "pyjwt==2.12.1",
    "python-dateutil==2.8.2",
```

Insert `py-tlsh==4.12.1` between them:

```toml
    "pyjwt==2.12.1",
    "py-tlsh==4.12.1",
    "python-dateutil==2.8.2",
```

(Alphabetically, `py-tlsh` sorts between `pyjwt` and `python-dateutil` because `-` < letters in ASCII.)

- [ ] **Step 1.2: Refresh the lockfile**

Run: `uv lock`

Expected: `uv.lock` updates in-place; `git diff uv.lock` shows a new entry for `py-tlsh` 4.12.1 with its sha hashes. No changes to other packages.

- [ ] **Step 1.3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "$(cat <<'EOF'
Add py-tlsh dependency for TLSH hashing support

py-tlsh 4.12.1 supersedes the older python-tlsh package and is
maintained by the TLSH project authors. Both import as `tlsh`.

See docs/superpowers/specs/2026-04-15-tlsh-support-design.md

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add build toolchain to Dockerfile builder stage

**Files:**
- Modify: `deploy/docker/Dockerfile:1-27` (builder stage)

- [ ] **Step 2.1: Add apk build dependencies before `uv sync`**

`py-tlsh` has no musl wheels on PyPI, so `uv sync` will compile it from source under Alpine. The builder stage is `ghcr.io/astral-sh/uv:python3.12-alpine` which does not ship a C++ toolchain.

Open `deploy/docker/Dockerfile`. The current lines 5-17 are:

```dockerfile
WORKDIR /app

# Omit development dependencies
ENV UV_NO_DEV=1

# Disable Python downloads, because we want to use the system interpreter
# across both images. If using a managed Python version, it needs to be
# copied from the build image into the final image; see `standalone.Dockerfile`
# for an example.
ENV UV_PYTHON_DOWNLOADS=0
# No hardlinks supported
ENV UV_LINK_MODE=copy

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project
```

Insert a new `RUN apk add` line between `ENV UV_LINK_MODE=copy` and the `RUN --mount=...uv sync` block, so the toolchain is in place when `uv sync` fires:

```dockerfile
WORKDIR /app

# Omit development dependencies
ENV UV_NO_DEV=1

# Disable Python downloads, because we want to use the system interpreter
# across both images. If using a managed Python version, it needs to be
# copied from the build image into the final image; see `standalone.Dockerfile`
# for an example.
ENV UV_PYTHON_DOWNLOADS=0
# No hardlinks supported
ENV UV_LINK_MODE=copy

# Build toolchain for C/C++ extensions compiled from source by uv sync
# (py-tlsh has no musl wheels on PyPI).
RUN apk add --no-cache g++ musl-dev python3-dev

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project
```

**Do not modify the runtime stage** (line 33 onwards). The compiled `.so` travels into the runtime image via the existing `COPY --from=builder` at line 40. `libfuzzy2` stays on the runtime stage (it is ssdeep's ctypes target, unrelated to TLSH).

- [ ] **Step 2.2: Commit**

```bash
git add deploy/docker/Dockerfile
git commit -m "$(cat <<'EOF'
Add C++ build toolchain to Dockerfile builder stage

py-tlsh is a C++ extension with no musl wheels on PyPI, so uv sync
compiles it from source. g++/musl-dev/python3-dev must be available
in the builder stage; the runtime stage is unchanged.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Verify the Docker image builds

This task exists specifically to catch the "py-tlsh musl build" risk identified in spec §10 before any Python code changes land. If it fails, the whole plan is blocked and we need to pivot (e.g. switch the runtime base image from Alpine to Debian slim, which is a scope change that must be escalated to the user before continuing).

- [ ] **Step 3.1: Build the mwdb image**

Run: `docker compose -f docker-compose-dev.yml build mwdb`

Expected: full successful build, ending with something like `naming to docker.io/library/mwdb-core-wafflemakers-mwdb`. The `uv sync` step in the builder stage should compile py-tlsh 4.12.1 from source (visible in the output as `Building py-tlsh==4.12.1`) and finish without error.

- [ ] **Step 3.2: Sanity-check the installed module**

Run: `docker compose -f docker-compose-dev.yml run --rm --entrypoint "" mwdb python -c "import tlsh; print(tlsh.__file__)"`

Expected: prints a path ending in `site-packages/tlsh*.so` without an `ImportError`. This confirms the compiled extension made it into the runtime image.

- [ ] **Step 3.3: No commit**

No file changes in this task — it's pure verification. If steps 3.1 and 3.2 succeed, proceed to task 4. If either fails, stop and escalate.

---

## Task 4: Create the `mwdb/core/tlsh.py` wrapper module

**Files:**
- Create: `mwdb/core/tlsh.py`

- [ ] **Step 4.1: Write the wrapper module**

Create `mwdb/core/tlsh.py` with exactly this content:

```python
import tlsh

from mwdb.core.log import getLogger

logger = getLogger()


def calc_tlsh(stream) -> str | None:
    """
    Compute TLSH hash of a file stream. Returns None if TLSH cannot produce
    a valid hash (e.g. file smaller than the ~50-byte floor or insufficient
    byte diversity).
    """
    stream.seek(0)
    hasher = tlsh.Tlsh()
    while chunk := stream.read(1024 * 256):
        hasher.update(chunk)
    try:
        hasher.final()
        digest = hasher.hexdigest()
    except Exception:
        logger.debug("TLSH computation failed for stream", exc_info=True)
        return None
    if not digest or digest == "TNULL":
        return None
    return digest
```

Design notes reinforced from the spec:
- No class wrapper — `py-tlsh`'s `Tlsh()` is already clean enough.
- `force=True` is NOT used. Standard mode; small files return `None`.
- The `except Exception` + `"TNULL"` + empty-string check is belt-and-suspenders because py-tlsh's "refused to hash" signal varies across versions.
- `logger.debug` (not `warning`) because small-file failures are expected, not exceptional.

- [ ] **Step 4.2: Verify the module imports cleanly via ruff**

Run: `uv run ruff check mwdb/core/tlsh.py && uv run ruff format --check mwdb/core/tlsh.py`

Expected: `All checks passed!` and `1 file already formatted`. If formatting fails, run `uv run ruff format mwdb/core/tlsh.py` and re-run the check.

- [ ] **Step 4.3: Commit**

```bash
git add mwdb/core/tlsh.py
git commit -m "$(cat <<'EOF'
Add mwdb/core/tlsh.py wrapper for py-tlsh

Thin wrapper exposing calc_tlsh(stream) -> str | None. Mirrors the
structural role of mwdb/core/ssdeep.py but without the ctypes/config-
flag ceremony because py-tlsh is a pinned pypi dependency.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Re-export `calc_tlsh` from `mwdb/core/util.py`

**Files:**
- Modify: `mwdb/core/util.py:26` (import block near `SsdeepHash`)

- [ ] **Step 5.1: Add the import**

Open `mwdb/core/util.py`. Near the top, the existing line 26 is:

```python
from .ssdeep import SsdeepHash
```

Add one line directly below it:

```python
from .ssdeep import SsdeepHash
from .tlsh import calc_tlsh
```

This lets `mwdb.model.file` do `from mwdb.core.util import ..., calc_tlsh` in the same import statement as `calc_ssdeep`.

- [ ] **Step 5.2: Verify ruff**

Run: `uv run ruff check mwdb/core/util.py && uv run ruff format --check mwdb/core/util.py`

Expected: `All checks passed!` and `1 file already formatted`. If ruff's import-ordering rule (`I`) flags the new line, run `uv run ruff check --select I --fix mwdb/core/util.py` and re-verify.

- [ ] **Step 5.3: Commit**

```bash
git add mwdb/core/util.py
git commit -m "$(cat <<'EOF'
Re-export calc_tlsh from mwdb.core.util

Lets mwdb.model.file import calc_tlsh alongside calc_ssdeep in one
import statement, matching the existing utility-module pattern.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Write the Alembic migration

**Files:**
- Create: `mwdb/model/migrations/versions/<generated>_add_tlsh_column_to_file.py`

- [ ] **Step 6.1: Generate the migration skeleton**

Run: `docker compose -f docker-compose-dev.yml run --rm --entrypoint "" mwdb flask db revision -m "add tlsh column to file"`

(Use `flask db revision` — plain skeleton without autogenerate — because we want an explicit hand-written migration that exactly matches the spec. Autogenerate would work, but the spec requires a specific index name (`ix_file_tlsh`) and the exact column declaration, and hand-writing is more reliable than editing autogenerated output.)

Expected: prints a line like `Generating /app/mwdb/model/migrations/versions/<new-rev-id>_add_tlsh_column_to_file.py ... done`.

The new revision ID is random (e.g. `a1b2c3d4e5f6`). The `down_revision` in the file will be `01dea56ffaf7` (the current head — already verified before writing this plan).

- [ ] **Step 6.2: Rewrite the migration body**

Open the generated file. Replace the `upgrade()` and `downgrade()` stubs so the full file matches exactly:

```python
"""add tlsh column to file

Revision ID: <keep whatever was generated in step 6.1>
Revises: 01dea56ffaf7
Create Date: <keep whatever was generated>

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "<keep whatever was generated>"
down_revision = "01dea56ffaf7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "file",
        sa.Column("tlsh", sa.String(length=72, collation="C"), nullable=True),
    )
    op.create_index("ix_file_tlsh", "file", ["tlsh"], unique=False)


def downgrade():
    op.drop_index("ix_file_tlsh", table_name="file")
    op.drop_column("file", "tlsh")
```

Do NOT rename the file or change the revision ID. Only replace the body.

- [ ] **Step 6.3: Verify the migration applies to a fresh database**

Run:
```bash
docker compose -f docker-compose-dev.yml down -v
docker compose -f docker-compose-dev.yml up -d postgres redis
docker compose -f docker-compose-dev.yml run --rm --entrypoint "" mwdb flask db upgrade
```

Expected output ends with something like `Running upgrade 01dea56ffaf7 -> <new-rev-id>, add tlsh column to file`. No tracebacks.

Verify the column exists:
```bash
docker compose -f docker-compose-dev.yml exec postgres psql -U mwdb -d mwdb -c "\d file" | grep tlsh
```

Expected: one line showing `tlsh | character varying(72) | ... |` (collation shown separately) and an index line `"ix_file_tlsh" btree (tlsh)`.

- [ ] **Step 6.4: Verify `downgrade()` works**

Run: `docker compose -f docker-compose-dev.yml run --rm --entrypoint "" mwdb flask db downgrade -1`

Expected: `Running downgrade <new-rev-id> -> 01dea56ffaf7, add tlsh column to file`.

Re-upgrade: `docker compose -f docker-compose-dev.yml run --rm --entrypoint "" mwdb flask db upgrade`

- [ ] **Step 6.5: Commit**

```bash
git add mwdb/model/migrations/versions/
git commit -m "$(cat <<'EOF'
Add Alembic migration for file.tlsh column

Nullable String(72) column with ix_file_tlsh btree index. Existing
rows receive NULL; no backfill. Reversible via downgrade.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Wire the `tlsh` column into the File model

**Files:**
- Modify: `mwdb/model/file.py:37` (column declaration), `mwdb/model/file.py:94-106` (`File.get_or_create`)

- [ ] **Step 7.1: Import `calc_tlsh`**

Open `mwdb/model/file.py`. The existing line 17 is:

```python
from mwdb.core.util import calc_crc32, calc_hash, calc_magic, calc_ssdeep, get_s3_client
```

Replace with:

```python
from mwdb.core.util import (
    calc_crc32,
    calc_hash,
    calc_magic,
    calc_ssdeep,
    calc_tlsh,
    get_s3_client,
)
```

(Wrap to multi-line because the line is now too long for ruff's line-length limit.)

- [ ] **Step 7.2: Add the column**

The existing class body at lines 32-38 of `mwdb/model/file.py` is:

```python
    md5 = db.Column(db.String(32, collation="C"), index=True)
    crc32 = db.Column(db.String(8, collation="C"), index=True)
    sha1 = db.Column(db.String(40, collation="C"), index=True)
    sha256 = db.Column(db.String(64, collation="C"), index=True, unique=True)
    sha512 = db.Column(db.String(128, collation="C"), index=True)
    ssdeep = db.Column(db.String(255, collation="C"), index=True)
```

Insert a new line directly below `ssdeep`:

```python
    sha512 = db.Column(db.String(128, collation="C"), index=True)
    ssdeep = db.Column(db.String(255, collation="C"), index=True)
    tlsh = db.Column(db.String(72, collation="C"), index=True)
```

- [ ] **Step 7.3: Wire the call into `get_or_create`**

In the same file, find the existing `File(...)` construction at lines 94-106:

```python
        file_obj = File(
            dhash=sha256,
            file_name=secure_filename(file_name),
            file_size=file_size,
            file_type=calc_magic(file_stream),
            crc32=calc_crc32(file_stream),
            md5=calc_hash(file_stream, hashlib.md5(), lambda h: h.hexdigest()),
            sha1=calc_hash(file_stream, hashlib.sha1(), lambda h: h.hexdigest()),
            sha256=sha256,
            sha512=calc_hash(file_stream, hashlib.sha512(), lambda h: h.hexdigest()),
            ssdeep=calc_ssdeep(file_stream),
            share_3rd_party=share_3rd_party,
        )
```

Insert `tlsh=calc_tlsh(file_stream),` between the `ssdeep=...` line and the `share_3rd_party=...` line:

```python
        file_obj = File(
            dhash=sha256,
            file_name=secure_filename(file_name),
            file_size=file_size,
            file_type=calc_magic(file_stream),
            crc32=calc_crc32(file_stream),
            md5=calc_hash(file_stream, hashlib.md5(), lambda h: h.hexdigest()),
            sha1=calc_hash(file_stream, hashlib.sha1(), lambda h: h.hexdigest()),
            sha256=sha256,
            sha512=calc_hash(file_stream, hashlib.sha512(), lambda h: h.hexdigest()),
            ssdeep=calc_ssdeep(file_stream),
            tlsh=calc_tlsh(file_stream),
            share_3rd_party=share_3rd_party,
        )
```

**Do not modify `File.get()`** at lines 48-61. TLSH is not a lookup identifier — this is a deliberate spec decision (follows the ssdeep precedent).

- [ ] **Step 7.4: Verify ruff**

Run: `uv run ruff check mwdb/model/file.py && uv run ruff format --check mwdb/model/file.py`

Expected: `All checks passed!` and `1 file already formatted`.

- [ ] **Step 7.5: Commit**

```bash
git add mwdb/model/file.py
git commit -m "$(cat <<'EOF'
Add tlsh column to File model and wire into get_or_create

String(72) nullable indexed column populated from calc_tlsh on every
upload. File.get() is deliberately unchanged — TLSH is not a lookup
identifier (matches the ssdeep precedent).

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Expose `tlsh` in the file response schema

**Files:**
- Modify: `mwdb/schema/file.py:67` (`FileItemResponseSchema`)

- [ ] **Step 8.1: Add the field**

Open `mwdb/schema/file.py`. The existing lines 56-71 define `FileItemResponseSchema`:

```python
class FileItemResponseSchema(ObjectItemResponseSchema):
    file_name = fields.Str(required=True, allow_none=False)
    alt_names = fields.List(fields.Str(required=True, allow_none=False))
    file_size = fields.Int(required=True, allow_none=False)
    file_type = fields.Str(required=True, allow_none=False)

    md5 = fields.Str(required=True, allow_none=False)
    sha1 = fields.Str(required=True, allow_none=False)
    sha256 = fields.Str(required=True, allow_none=False)
    sha512 = fields.Str(required=True, allow_none=False)
    crc32 = fields.Str(required=True, allow_none=False)
    ssdeep = fields.Str(required=True, allow_none=True)

    latest_config = fields.Nested(
        ConfigLatestItemResponseSchema, required=True, allow_none=True
    )
```

Insert one line directly below `ssdeep`:

```python
class FileItemResponseSchema(ObjectItemResponseSchema):
    file_name = fields.Str(required=True, allow_none=False)
    alt_names = fields.List(fields.Str(required=True, allow_none=False))
    file_size = fields.Int(required=True, allow_none=False)
    file_type = fields.Str(required=True, allow_none=False)

    md5 = fields.Str(required=True, allow_none=False)
    sha1 = fields.Str(required=True, allow_none=False)
    sha256 = fields.Str(required=True, allow_none=False)
    sha512 = fields.Str(required=True, allow_none=False)
    crc32 = fields.Str(required=True, allow_none=False)
    ssdeep = fields.Str(required=True, allow_none=True)
    tlsh = fields.Str(required=True, allow_none=True)

    latest_config = fields.Nested(
        ConfigLatestItemResponseSchema, required=True, allow_none=True
    )
```

**Do not add `tlsh` to `FileListItemResponseSchema`** (lines 42-50). That schema intentionally exposes only md5/sha1/sha256 — the identifying hashes. ssdeep is not in the list view either, and TLSH follows that precedent.

- [ ] **Step 8.2: Verify ruff**

Run: `uv run ruff check mwdb/schema/file.py && uv run ruff format --check mwdb/schema/file.py`

Expected: `All checks passed!` and `1 file already formatted`.

- [ ] **Step 8.3: Commit**

```bash
git add mwdb/schema/file.py
git commit -m "$(cat <<'EOF'
Expose tlsh in FileItemResponseSchema

Detail-view only — list view intentionally unchanged (matches the
ssdeep precedent: list view carries only identifying hashes).

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Register the `tlsh` search field

**Files:**
- Modify: `mwdb/core/search/mappings.py:72` (`File.__name__` dict in `field_mapping`)

- [ ] **Step 9.1: Add the mapping entry**

Open `mwdb/core/search/mappings.py`. The existing lines 64-75 are:

```python
    File.__name__: {
        "name": FileNameField(),
        "size": SizeField(File.file_size),
        "type": StringField(File.file_type),
        "md5": StringField(File.md5),
        "sha1": StringField(File.sha1),
        "sha256": StringField(File.sha256),
        "sha512": StringField(File.sha512),
        "ssdeep": StringField(File.ssdeep),
        "crc32": StringField(File.crc32),
        "multi": MultiFileField(),
    },
```

Insert one line directly below `ssdeep`:

```python
    File.__name__: {
        "name": FileNameField(),
        "size": SizeField(File.file_size),
        "type": StringField(File.file_type),
        "md5": StringField(File.md5),
        "sha1": StringField(File.sha1),
        "sha256": StringField(File.sha256),
        "sha512": StringField(File.sha512),
        "ssdeep": StringField(File.ssdeep),
        "tlsh": StringField(File.tlsh),
        "crc32": StringField(File.crc32),
        "multi": MultiFileField(),
    },
```

No new import required — `StringField` and `File` are already imported in this file.

- [ ] **Step 9.2: Verify ruff**

Run: `uv run ruff check mwdb/core/search/mappings.py && uv run ruff format --check mwdb/core/search/mappings.py`

Expected: `All checks passed!` and `1 file already formatted`.

- [ ] **Step 9.3: Commit**

```bash
git add mwdb/core/search/mappings.py
git commit -m "$(cat <<'EOF'
Register tlsh as a searchable StringField

Exact-match only, hits the ix_file_tlsh index. No Luqum operator
extension — similar-files / distance queries are deferred to the
jetpack-library phase 2 spec.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Add backend integration tests

**Files:**
- Modify: `tests/backend/test_api.py` (append two tests)
- Modify: `tests/backend/test_search.py` (append one test)

Note: these tests are authored here but executed in task 11 against a rebuilt stack. Don't run them yet.

- [ ] **Step 10.1: Add upload assertions to `test_api.py`**

Open `tests/backend/test_api.py`. Append these two tests to the end of the file (after the last test in the file — no need to group with other hash tests, since the file is flat):

```python
def test_tlsh_computed_on_upload(admin_session):
    # > 512 bytes of varied content so TLSH can produce a hash
    content = ("A" * 256 + "B" * 256 + rand_string(512))
    sample = admin_session.add_sample(rand_string(), content)
    assert sample["tlsh"] is not None
    assert sample["tlsh"].startswith("T1")
    assert len(sample["tlsh"]) == 72


def test_tlsh_skipped_for_tiny_file(admin_session):
    sample = admin_session.add_sample(rand_string(), "short")
    assert sample["tlsh"] is None
```

Note: `rand_string` is already imported from `.utils` at line 9 of `test_api.py`. It accepts an optional length argument (see `tests/backend/utils.py`). The content is passed as a `str` because `admin_session.add_sample` feeds it to `requests.post(...files=...)` which accepts both `str` and `bytes`.

- [ ] **Step 10.2: Add the search round-trip test to `test_search.py`**

Open `tests/backend/test_search.py`. Append this test to the end of the file:

```python
def test_search_by_tlsh(admin_session):
    test = admin_session

    content = ("A" * 256 + "B" * 256 + rand_string(512))
    sample = test.add_sample(rand_string(), content)
    assert sample["tlsh"] is not None  # preconditional — if tlsh failed, so will search

    found_objs = test.search(f'file.tlsh:{sample["tlsh"]}')

    assert len(found_objs) > 0
    assert any(obj["id"] == sample["id"] for obj in found_objs)
```

`rand_string` is already imported at line 7 of `test_search.py`. The query uses `file.tlsh:...` (with the `file.` type prefix) to match the style of sibling tests that use `file.name:...` and `file.size:...`.

- [ ] **Step 10.3: Verify test files parse**

These tests will be excluded from `ruff` (the spec's `tool.ruff.extend-exclude` in `pyproject.toml` excludes `tests/`), so ruff won't catch syntax issues in them. Do a quick parse check with Python:

Run: `python3 -c "import ast; ast.parse(open('tests/backend/test_api.py').read()); ast.parse(open('tests/backend/test_search.py').read()); print('OK')"`

Expected: `OK`.

- [ ] **Step 10.4: Commit**

```bash
git add tests/backend/test_api.py tests/backend/test_search.py
git commit -m "$(cat <<'EOF'
Add backend integration tests for TLSH

Covers: hash computed on upload, hash null on tiny file, search
round-trip via file.tlsh: query field.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Run the backend e2e test suite

This is the single "run everything" gate for the backend work. All of tasks 1–10 get their integration-level verification here.

- [ ] **Step 11.1: Rebuild and bring up the test stack**

Run:
```bash
./gen_vars.sh test
docker compose -f docker-compose-dev.yml down -v
docker compose -f docker-compose-dev.yml up -d --build
```

Expected: all services come up healthy. This takes a few minutes on first build because py-tlsh compiles from source in the builder stage.

Wait for the mwdb container to finish starting and running migrations. Poll:
```bash
until curl -sf http://127.0.0.1/api/ping; do sleep 2; done
```

Expected: eventually `{"status": "ok"}` or similar non-error response from the ping endpoint.

- [ ] **Step 11.2: Export the admin credentials**

```bash
export MWDB_ADMIN_LOGIN=admin
export MWDB_ADMIN_PASSWORD=$(grep ^MWDB_ADMIN_PASSWORD= mwdb-vars.env | cut -d= -f2)
export MWDB_URL=http://127.0.0.1/api
```

- [ ] **Step 11.3: Run the new TLSH tests directly**

Run:
```bash
cd tests/backend && uv run pytest -v \
    test_api.py::test_tlsh_computed_on_upload \
    test_api.py::test_tlsh_skipped_for_tiny_file \
    test_search.py::test_search_by_tlsh
```

Expected: all three pass.

If `test_tlsh_skipped_for_tiny_file` fails (`sample["tlsh"]` is not None for a 5-byte input), py-tlsh 4.12.1's behavior on tiny inputs may differ from what `calc_tlsh` assumes. Inspect the actual return value with a quick REPL check inside the container:

```bash
docker compose -f docker-compose-dev.yml run --rm --entrypoint "" mwdb python -c "
import tlsh
h = tlsh.Tlsh()
h.update(b'short')
try:
    h.final()
    print('hexdigest:', repr(h.hexdigest()))
except Exception as e:
    print('raised:', type(e).__name__, e)
"
```

Use the output to tighten `mwdb/core/tlsh.py`'s refused-hash check in a follow-up commit. The existing check covers `"TNULL"`, empty string, and exceptions — if py-tlsh returns something else (e.g. all-zeros or a different sentinel), extend the check.

- [ ] **Step 11.4: Run the full backend test suite**

Run:
```bash
cd tests/backend && uv run pytest -v
```

Expected: the entire suite passes. If any pre-existing test now fails because of the TLSH change (unlikely — the changes are additive), investigate before proceeding. The most plausible failure mode is a test that asserts an exact set of keys on a file response dict and now sees an extra `tlsh` key; if so, update that test to include `tlsh`.

- [ ] **Step 11.5: No commit**

Pure verification task, no code changes. If any fix was needed in step 11.3, that fix is its own small commit using the pattern from task 4.

---

## Task 12: Frontend — add `tlsh` to the file type

**Files:**
- Modify: `mwdb/web/src/types/types.ts:124`

- [ ] **Step 12.1: Add the field**

Open `mwdb/web/src/types/types.ts`. The existing line 124 (inside the file type) is:

```ts
    ssdeep: string;
```

Insert one line directly below it:

```ts
    ssdeep: string;
    tlsh: string | null;
```

`string | null` (not just `string`) because the backend returns `null` for small files. The existing `ssdeep: string` is a pre-existing typing bug — **do not fix it** in this task. If `npx tsc` in step 12.2 or task 15 complains about the narrower type on `tlsh` interacting with code that uses `object.ssdeep`, that's a separate issue, but since we're only reading `object.tlsh` in new code, there should be no interaction.

- [ ] **Step 12.2: Verify types**

Run: `cd mwdb/web && npx tsc --noEmit`

Expected: no errors. (This is the same check CI runs in the `lint_web` job at `.github/workflows/build.yml:31`.)

- [ ] **Step 12.3: Commit**

```bash
git add mwdb/web/src/types/types.ts
git commit -m "$(cat <<'EOF'
Web: Add tlsh field to the file type

Typed as string | null because the backend schema uses allow_none=True
for tlsh (small files return null).

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Frontend — render the `tlsh` row in SampleDetails

**Files:**
- Modify: `mwdb/web/src/components/SampleDetails.tsx` around line 178-195 (existing ssdeep row)

- [ ] **Step 13.1: Read the surrounding context to confirm imports**

Before editing, read `mwdb/web/src/components/SampleDetails.tsx` lines 1-30 to confirm which of `Link`, `makeSearchLink`, and `ActionCopyToClipboard` are already imported. All three are used by the existing ssdeep row at lines 179-196, so all three are definitely in scope — no new imports are needed. Confirming before editing just avoids surprises.

Run: `head -40 mwdb/web/src/components/SampleDetails.tsx`

Expected: an import block containing (among others) `Link` from react-router, `makeSearchLink` from some helpers module, and `ActionCopyToClipboard` from some UI module.

- [ ] **Step 13.2: Insert the new row**

The existing ssdeep row occupies approximately lines 178-196. It looks roughly like:

```tsx
<tr className="flickerable">
    <th>ssdeep</th>
    <td id="ssdeep" className="text-monospace">
        <Link
            to={makeSearchLink({
                field: "ssdeep",
                value: object.ssdeep,
                pathname: "/",
            })}
        >
            {object.ssdeep}
        </Link>
        <span className="ml-2">
            <ActionCopyToClipboard
                text={object.ssdeep}
                tooltipMessage="Copy ssdeep to clipboard"
            />
        </span>
    </td>
</tr>
```

Insert a new `<tr>` directly after this one, matching this structure but with a `not computed` fallback for the null case:

```tsx
<tr className="flickerable">
    <th>tlsh</th>
    <td id="tlsh" className="text-monospace">
        {object.tlsh ? (
            <>
                <Link
                    to={makeSearchLink({
                        field: "tlsh",
                        value: object.tlsh,
                        pathname: "/",
                    })}
                >
                    {object.tlsh}
                </Link>
                <span className="ml-2">
                    <ActionCopyToClipboard
                        text={object.tlsh}
                        tooltipMessage="Copy tlsh to clipboard"
                    />
                </span>
            </>
        ) : (
            <span className="text-muted">not computed</span>
        )}
    </td>
</tr>
```

If the exact attributes on the ssdeep row in the real file differ slightly from what's reproduced above (e.g. an extra className, a different Link import shape), match the real file exactly for the fields that are NOT the new null-check — the null-check is the only intentional divergence.

- [ ] **Step 13.3: Verify types**

Run: `cd mwdb/web && npx tsc --noEmit`

Expected: no errors. This is the main gate — TypeScript must be happy with `object.tlsh ?`'s narrowing.

- [ ] **Step 13.4: Verify prettier formatting**

Run: `cd mwdb/web && npx prettier --check src/components/SampleDetails.tsx`

Expected: `All matched files use Prettier code style!`. If not, run `npx prettier --write src/components/SampleDetails.tsx` and re-verify.

- [ ] **Step 13.5: Commit**

```bash
git add mwdb/web/src/components/SampleDetails.tsx
git commit -m "$(cat <<'EOF'
Web: Render tlsh row on SampleDetails with null fallback

New row mirrors the ssdeep row but shows "not computed" when
object.tlsh is null (small files below TLSH's byte floor).

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Frontend — add `tlsh` to query suggestions

**Files:**
- Modify: `mwdb/web/src/components/RecentView/common/useQuerySuggestions.ts:107` (ssdeep entry)

- [ ] **Step 14.1: Add the suggestion entry**

Open `mwdb/web/src/components/RecentView/common/useQuerySuggestions.ts`. The existing lines 107-109 are:

```ts
        ssdeep: {
            description: "Query for file having a provided ssdeep hash",
        },
```

Insert directly after:

```ts
        ssdeep: {
            description: "Query for file having a provided ssdeep hash",
        },
        tlsh: {
            description: "Query for file having a provided TLSH hash",
        },
```

- [ ] **Step 14.2: Verify types & formatting**

Run:
```bash
cd mwdb/web && npx tsc --noEmit && npx prettier --check src/components/RecentView/common/useQuerySuggestions.ts
```

Expected: no errors, prettier reports formatted.

- [ ] **Step 14.3: Commit**

```bash
git add mwdb/web/src/components/RecentView/common/useQuerySuggestions.ts
git commit -m "$(cat <<'EOF'
Web: Add tlsh to file query suggestions

Makes tlsh: autocomplete alongside the other hash fields in the
search bar's suggestion dropdown.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: Full lint/format gate

Run the same checks CI runs, against the full tree, to make sure nothing unrelated broke.

- [ ] **Step 15.1: Run ruff check**

Run: `uv run ruff check --select I .`

Expected: `All checks passed!`. Matches the CI command at `.github/workflows/build.yml:16`.

- [ ] **Step 15.2: Run ruff format --check**

Run: `uv run ruff format --check --diff .`

Expected: all files already formatted. Matches `.github/workflows/build.yml:17`.

- [ ] **Step 15.3: Run prettier --check on the web tree**

Run: `cd mwdb/web && npx prettier --check src`

Expected: `All matched files use Prettier code style!`. Matches `.github/workflows/build.yml:29`.

- [ ] **Step 15.4: Run tsc on the web tree**

Run: `cd mwdb/web && npx tsc`

Expected: no errors. Matches `.github/workflows/build.yml:32`.

- [ ] **Step 15.5: No commit**

Pure verification. If any step fails, fix the underlying file and commit that fix using the same message style as the originating task.

---

## Task 16: Manual verification in the browser

This covers the §8.3 checklist from the spec. There is no automated frontend test for `SampleDetails` today, so these steps genuinely must be done by hand in a browser.

- [ ] **Step 16.1: Bring up the dev stack**

If the stack from task 11 is still up, skip rebuild. Otherwise:

```bash
./gen_vars.sh
docker compose -f docker-compose-dev.yml up -d --build
```

Expected: stack healthy, `http://127.0.0.1/` serves the SPA, `http://127.0.0.1/api/ping` returns ok.

- [ ] **Step 16.2: Log in as admin**

Open `http://127.0.0.1/` in a browser. Log in with `admin` and the password from `mwdb-vars.env`.

- [ ] **Step 16.3: Upload a ~1 KB varied file and eyeball the TLSH row**

Create a test file:
```bash
(head -c 256 /dev/urandom; head -c 512 /dev/zero; head -c 256 /dev/urandom) > /tmp/tlsh-test-large.bin
```

Upload it via the SPA's upload button. Navigate to the sample detail page (the page that `SampleDetails.tsx` renders).

Expected: the new `tlsh` row is present directly below `ssdeep`. The value starts with `T1` and is 72 characters. Clicking the hash takes you to a search for `tlsh:<value>`. The copy button copies the hash to the clipboard.

- [ ] **Step 16.4: Upload a tiny file and eyeball the fallback**

Create a test file:
```bash
echo -n "short" > /tmp/tlsh-test-tiny.bin
```

Upload via the SPA. On the sample detail page:

Expected: the `tlsh` row is present and shows `not computed` in muted text. No link, no copy button.

- [ ] **Step 16.5: Verify query-suggestion autocomplete and a manual query**

In the SPA's search bar, type `file.tlsh:` (the `file.` type prefix is required for file-specific fields when querying from the generic search context — see `mwdb/core/search/mappings.py` `get_field_mapper`).

Expected: the suggestion dropdown shows `tlsh` with the description `Query for file having a provided TLSH hash`.

Finish the query with the hash from step 16.3 (`file.tlsh:T1...`) and submit.

Expected: the large sample from step 16.3 appears in the result list.

- [ ] **Step 16.6: Verify search round-trip via URL**

From the large-file sample detail page (step 16.3), click the TLSH hash link. It should navigate to the recent view with query `tlsh:T1...`.

Expected: the large sample appears in the result list, with no unrelated results.

- [ ] **Step 16.7: No commit**

Pure manual verification. If any step fails, stop, diagnose, fix, and re-run from the failing step's task.

---

## Final check before handing off

- [ ] All 16 tasks' checkboxes are ticked.
- [ ] `git log --oneline` shows 12 new commits from tasks 1, 2, 4, 5, 6, 7, 8, 9, 10, 12, 13, 14. Tasks 3, 11, 15, 16 are pure verification and produce no commits. If a verification step surfaced a fix, that fix is its own additional small commit.
- [ ] `git status` is clean.
- [ ] The manual verification checklist in task 16 has been completed in an actual browser, not just mentally rehearsed.
