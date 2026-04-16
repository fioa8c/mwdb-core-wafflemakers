# TLSH Hashing Support in mwdb-core — Design

**Status:** approved, ready for implementation planning
**Date:** 2026-04-15
**Fork:** `mwdb-core-wafflemakers` (diverged from upstream `CERT-Polska/mwdb-core`)
**Subsystem:** 1 of 3 for the jetpack-threat-library integration

## 1. Goal

Compute, persist, and expose the **TLSH** (Trend Micro Locality Sensitive Hash) of every uploaded file, alongside the existing hashes (md5, sha1, sha256, sha512, ssdeep, crc32). TLSH is specifically required because the upcoming jetpack-threat-library corpus consists mostly of small PHP files where ssdeep's sensitivity is limited, and the fork will use mwdb as the source of truth for that corpus.

## 2. Scope & non-goals

### In scope

- Compute TLSH on file upload and persist it in a new indexed column on the `object` table (the physical table shared by `File`/`Config`/`TextBlob` since migration `6db157d09a30` merged them via single-table inheritance — the `File.tlsh` class attribute resolves to `object.tlsh`).
- Expose `tlsh` in the file detail API response.
- Make `tlsh:<value>` searchable via the existing Luqum-based search grammar as an exact-match `StringField`.
- Render the hash on the sample details page in the SPA, with a "not computed" fallback for files too small/low-entropy for TLSH.
- Add the `tlsh` field to the query-suggestion dictionary so it autocompletes alongside the other hash fields.

### Explicitly deferred (phase 2 — jetpack-threat-library integration spec)

- Similar-files / nearest-neighbor endpoint (`GET /file/<id>/similar?algorithm=tlsh&threshold=N`).
- TLSH distance operator in the search DSL (e.g. `tlsh:T1ABC...~40`).
- `/file/<tlsh>` identifier lookup via the URL converter (follows the ssdeep precedent: not lookup-able).
- Backfill of TLSH for files already in the database. New uploads only; existing rows stay `NULL`.

### Explicitly NOT added (YAGNI)

- No `enable_tlsh` config flag. TLSH is always on in this fork. The existing `enable_ssdeep` flag exists only because libfuzzy is a ctypes-loaded system library with a real runtime "library missing" failure mode in dev environments; `py-tlsh` is a pinned pypi dependency, so that failure mode does not apply.
- No `try/except ImportError` around `import tlsh`. Hard dep — `uv.lock` pins it, `uv sync` installs it. If it's missing, the server should fail loudly at startup, which is the correct behavior.
- No unit-test harness for `mwdb/core/*` utilities. The project does not have one today and adding it is out of scope.

## 3. Architecture overview

```
┌───────────────────────────┐     py-tlsh 4.12.1
│ mwdb/core/tlsh.py         │ ←── (pypi dependency; compiled from source
│   calc_tlsh(stream) -> str│      in builder stage via g++/musl-dev)
└────────────┬──────────────┘
             │
             ▼
┌───────────────────────────┐
│ mwdb/core/util.py         │   re-exports calc_tlsh alongside calc_ssdeep
└────────────┬──────────────┘
             │
             ▼
┌───────────────────────────┐
│ File.get_or_create()      │   called during upload; computed inline
│  (mwdb/model/file.py)     │   with the other hashes; result stored in
└────────────┬──────────────┘   File.tlsh (nullable, String(72))
             │
             ├──────────────────────────┐
             ▼                          ▼
┌───────────────────────────┐  ┌───────────────────────────┐
│ FileItemResponseSchema    │  │ search mappings           │
│ adds `tlsh` field         │  │ "tlsh": StringField(...)  │
│ (mwdb/schema/file.py)     │  │ (mwdb/core/search/...)    │
└────────────┬──────────────┘  └───────────────────────────┘
             │
             ▼
┌───────────────────────────┐
│ SPA SampleDetails.tsx     │   new row; "not computed" fallback when null
│ useQuerySuggestions.ts    │   adds `tlsh` entry to file suggestions
│ types.ts                  │   `tlsh: string | null`
└───────────────────────────┘
```

**Design choices that drove the shape:**

- **Fuzzy-hash exact match only**, mirroring how ssdeep is surfaced in the search DSL today. Nearest-neighbor / distance queries are deferred to a separate endpoint in phase 2 — this keeps the Luqum → SQLAlchemy pipeline untouched and makes TLSH ship in days, not weeks.
- **`py-tlsh` pypi dep, not ctypes on libtlsh.** ssdeep uses raw ctypes because `libfuzzy2` is a system library and the official Python binding has historically been flaky; TLSH has no such problem, and a pypi dep makes the dependency visible in `pyproject.toml`/`uv.lock` instead of hidden in the Dockerfile. This is a deliberate divergence from the ssdeep precedent, permitted by the fork's mandate to change things when it serves the fork's goals.
- **Structural mimicry of ssdeep, not literal mimicry.** `mwdb/core/tlsh.py` is the TLSH equivalent of `mwdb/core/ssdeep.py` (dedicated wrapper module, exported through `util.py`), but without the config flag or the runtime-loading ceremony — those exist in the ssdeep module for reasons that don't apply to a pypi dep.
- **Nullable column, no force-mode.** Small/low-entropy files legitimately can't produce a meaningful TLSH. Storing `None` is safer than storing a low-quality forced hash: when phase 2 starts using TLSH for similarity clustering, the matches we *do* get will be trustworthy, at the cost of coverage on the smallest files. The UI communicates this honestly with "not computed."

## 4. Dependencies & Docker build

### 4.1 Python dependency

Add to `pyproject.toml` in the `dependencies` list, alphabetically between `pyjwt` and `python-dateutil`:

```toml
"py-tlsh==4.12.1",
```

Then refresh the lockfile:

```bash
uv lock
```

The pin is exact (`==`) to match the rest of the dependency list's convention (all deps are pinned).

Why `py-tlsh` and not `python-tlsh`: `py-tlsh` (PyPI, version 4.12.1, released 2026-01-18, maintained by the TLSH project authors) explicitly supersedes `python-tlsh` (PyPI, version 4.5.0, last released 2021-06-15, third-party maintainer, stale). Both import as `tlsh`, so the code is identical either way — the difference is which package gets installed.

### 4.2 Dockerfile

`py-tlsh` is a C++ extension with no musl wheels on PyPI, so `uv sync` will compile it from source. The toolchain must be available in the builder stage.

Edit `deploy/docker/Dockerfile` (currently 53 lines, multi-stage, builder = `ghcr.io/astral-sh/uv:python3.12-alpine`, runtime = `python:3.12-alpine`):

- **Builder stage:** add `RUN apk add --no-cache g++ musl-dev python3-dev` before the existing `uv sync --frozen --no-install-project` command (currently at line 21). This ensures the toolchain is in place when `uv sync` triggers the py-tlsh source build.
- **Runtime stage:** no change. The compiled `.so` is copied from the builder via the existing `COPY --from=builder` (currently at line 40), so no runtime apk additions are needed. Crucially, `libfuzzy2` stays on the runtime stage (ssdeep's ctypes target, unrelated to TLSH).

The apk package versions are not pinned — the builder image's apk index moves, pinning creates maintenance churn for zero safety gain.

## 5. Backend: compute & persist

### 5.1 New module `mwdb/core/tlsh.py`

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

**Implementation notes:**

- No class wrapper like ssdeep's `SsdeepHash` — `py-tlsh` already exposes a clean `Tlsh()` object, an extra layer would just be noise.
- Reads in 256 KiB chunks, matching `File.iterate()`'s chunk size.
- The `except Exception` + `digest == "TNULL"` double-check is deliberate: py-tlsh's precise "refused to hash" signal varies across versions (sometimes raises `ValueError`, sometimes returns `"TNULL"`, sometimes returns empty). During implementation, verify the actual behavior of py-tlsh 4.12.1 on a ~10-byte input and tighten the check if needed — but the belt-and-suspenders version is robust and the cost of a little extra defensive code here is nil.
- `logger.debug` (not `warning`) because small files failing is expected, not exceptional.

### 5.2 `mwdb/core/util.py`

Add at the top, near the existing ssdeep import:

```python
from .tlsh import calc_tlsh
```

No additional wrapper needed — `calc_tlsh` is re-exported directly. Keeps `mwdb.model.file` able to do `from mwdb.core.util import ..., calc_tlsh` in one import line.

### 5.3 `mwdb/model/file.py` — schema change

Add the column adjacent to `ssdeep` in the `File` class body (currently line 37):

```python
ssdeep = db.Column(db.String(255, collation="C"), index=True)
tlsh = db.Column(db.String(72, collation="C"), index=True)    # new
```

- **Length 72:** TLSH v4 is `T1` + 70 hex chars = exactly 72 bytes. Fixed length, so `String(72)` is tight — not `String(255)` like ssdeep (ssdeep's digest is variable-length).
- **Indexed:** `index=True` creates a btree index. Justification: exact-match search by TLSH is the entire point of this feature, so the query workload is known on day one; this is not speculative. The index is cheap on a 72-char column.
- **Nullable** (no `nullable=False`): small/low-entropy files persist as `NULL`.

Add `tlsh` to the `File(...)` construction in `File.get_or_create()` (currently line 94-106), right after the `ssdeep=calc_ssdeep(file_stream),` line:

```python
ssdeep=calc_ssdeep(file_stream),
tlsh=calc_tlsh(file_stream),      # new
```

No change to `File.get()` (the hash-lookup method on line 48). TLSH is not a lookup identifier — follows the ssdeep precedent.

### 5.4 Alembic migration

Generate via `flask db migrate -m "add tlsh column to file"`, then edit the generated file to exactly:

```python
"""add tlsh column to file

Revision ID: <auto-generated>
Revises: <current head revision>
Create Date: <auto-generated>

"""
import sqlalchemy as sa
from alembic import op

revision = "<auto-generated>"
down_revision = "<current head revision>"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "object",
        sa.Column("tlsh", sa.String(length=72, collation="C"), nullable=True),
    )
    op.create_index("ix_object_tlsh", "object", ["tlsh"], unique=False)


def downgrade():
    op.drop_index("ix_object_tlsh", table_name="object")
    op.drop_column("object", "tlsh")
```

Existing rows receive `NULL` for `tlsh`. No backfill. Phase 2 (the jetpack-library importer) will populate TLSH naturally for the files it uploads; any pre-existing files stay null unless re-uploaded.

## 6. Backend: API schema & search

### 6.1 `mwdb/schema/file.py`

Add to `FileItemResponseSchema`, immediately after the `ssdeep` field (currently line 67):

```python
ssdeep = fields.Str(required=True, allow_none=True)
tlsh = fields.Str(required=True, allow_none=True)    # new
```

- `allow_none=True` because the column is nullable.
- `required=True` matches the surrounding style — the key is always present in the response, its value may be null.
- **Do NOT add to `FileListItemResponseSchema`** (the list-view schema, currently lines 42-49). That schema exposes only `md5/sha1/sha256` — the identifying hashes. ssdeep is not in the list view either; TLSH follows that precedent and is a detail-view field only.

No change to `FileCreateRequestSchema`. TLSH is server-computed on upload and never accepted as client input, same as every other hash today.

### 6.2 `mwdb/core/search/mappings.py`

Add to the `File.__name__` dict, adjacent to the existing `ssdeep` entry (currently line 72):

```python
"ssdeep": StringField(File.ssdeep),
"tlsh": StringField(File.tlsh),    # new
```

`StringField` handles both exact-equality (`tlsh:T1ABC…`) and Luqum wildcard patterns (`tlsh:T1ABC*`). Exact equality hits the `ix_object_tlsh` index. Wildcard patterns fall back to `LIKE`; leading-anchor patterns still use the index, unanchored patterns degrade to a sequential scan — acceptable given the expected workload is full-hash pastes from the jetpack-library phase 2 ingestion.

## 7. Frontend

### 7.1 `mwdb/web/src/types/types.ts`

Add adjacent to the existing `ssdeep` field (currently line 124):

```ts
ssdeep: string;
tlsh: string | null;    // new
```

`string | null` (not just `string`) because the backend schema genuinely returns `null` for small files. The existing `ssdeep: string` declaration may be a pre-existing typing bug, given the backend uses `allow_none=True` — **do not fix it in this spec**, it's out of scope. Leave ssdeep alone and do TLSH correctly.

### 7.2 `mwdb/web/src/components/SampleDetails.tsx`

Add a new row immediately below the existing ssdeep row (currently lines 178-195). Template:

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

- The `object.tlsh ? … : "not computed"` ternary is the one deliberate divergence from the ssdeep row's template. ssdeep currently renders empty when null; TLSH is explicit about "couldn't compute." This is user-facing and honest.
- `Link`, `makeSearchLink`, and `ActionCopyToClipboard` should already be imported in `SampleDetails.tsx` for the ssdeep row — no new imports expected, but verify during implementation.

### 7.3 `mwdb/web/src/components/RecentView/common/useQuerySuggestions.ts`

Add immediately after the ssdeep entry (currently line 107):

```ts
ssdeep: {
    description: "Query for file having a provided ssdeep hash",
},
tlsh: {
    description: "Query for file having a provided TLSH hash",
},
```

Makes `tlsh:` autocomplete alongside the other file hash fields.

## 8. Testing

The `tests/backend/` suite is **HTTP integration tests against a live stack**, not unit tests. Per `CONTRIBUTING.md`, the suite runs against a running docker-compose environment with `MWDB_URL`/`MWDB_ADMIN_*` exported. Current coverage for fuzzy hashes is zero — no `ssdeep` assertions anywhere in `tests/backend/`. This spec matches that bar and adds targeted tests where they're actually meaningful.

### 8.1 Backend integration tests

**Add to `tests/backend/test_api.py`:**

```python
def test_tlsh_computed_on_upload(admin_session):
    # Upload > 512 bytes of varied content so TLSH produces a hash
    content = b"A" * 256 + b"B" * 256 + os.urandom(512)
    sample = admin_session.add_sample(content=content)
    assert sample["tlsh"] is not None
    assert sample["tlsh"].startswith("T1")
    assert len(sample["tlsh"]) == 72


def test_tlsh_skipped_for_tiny_file(admin_session):
    sample = admin_session.add_sample(content=b"short")
    assert sample["tlsh"] is None
```

**Add to `tests/backend/test_search.py`:**

```python
def test_search_by_tlsh(admin_session):
    content = b"A" * 256 + b"B" * 256 + os.urandom(512)
    sample = admin_session.add_sample(content=content)
    results = admin_session.search(f'tlsh:{sample["tlsh"]}')
    assert any(r["sha256"] == sample["sha256"] for r in results)
```

During implementation, verify that `admin_session.add_sample` and `admin_session.search` are the exact method names on the test client — they're the pattern in existing test_api.py but should be grep-confirmed.

### 8.2 What is NOT tested automatically

- **No unit tests for `calc_tlsh` in isolation.** The project has no unit-test harness for `mwdb/core/*`. Adding one is out of scope.
- **No frontend jest test for the new SampleDetails row.** SampleDetails is not currently jest-tested (to be confirmed during implementation by grep of `mwdb/web/src/__tests__/`). Matching that.
- **No test for the search-mapping entry in isolation.** The round-trip test in section 8.1 covers it end-to-end, which is what actually matters.
- **No dedicated migration test.** Alembic migrations are exercised implicitly by every e2e run (fresh DB), per project convention.

### 8.3 Manual verification checklist (part of the implementation work)

These **must** be done before calling the work complete:

1. `docker compose -f docker-compose-dev.yml up --build` — confirm the builder stage successfully compiles py-tlsh from source under musl. If this fails, everything downstream is moot.
2. Upload a ~1 KB file via the SPA, eyeball the new `tlsh` row on the sample details page, confirm the hash is rendered and the copy button works.
3. Upload a ~10-byte file via the SPA, eyeball the "not computed" fallback on the same page.
4. Click the `tlsh` hash link from step 2 and confirm it navigates to a search that returns the same file.
5. Type `tlsh:` in the search bar and confirm the autocomplete suggestion appears.

## 9. Implementation order (for the plan that follows this spec)

Recommended order, one small commit per step, each leaving the tree in a buildable state:

1. Add `py-tlsh` dep + Dockerfile build toolchain (Dockerfile + `pyproject.toml` + `uv.lock`). No Python code yet. Verify the image builds before moving on — this step exists specifically to catch the musl build risk from §10.
2. Create `mwdb/core/tlsh.py` + re-export from `util.py`. No model/schema changes yet.
3. Alembic migration for `file.tlsh` column + index.
4. `mwdb/model/file.py` column declaration + `get_or_create()` wiring.
5. `mwdb/schema/file.py` response field.
6. `mwdb/core/search/mappings.py` search field.
7. Backend integration tests (`test_api.py`, `test_search.py`).
8. Frontend: `types.ts` + `SampleDetails.tsx` + `useQuerySuggestions.ts`.
9. Manual verification checklist (section 8.3).

## 10. Open risks

1. **py-tlsh musl build.** `py-tlsh` publishes no manylinux_musl wheels. If compilation fails under Alpine's musl for any reason (e.g., a missing header that `python3-dev` on Alpine doesn't provide), fallback is either (a) switch the runtime base image from Alpine to Debian slim — significant scope creep — or (b) vendor a pre-built `.so` into the repo. Step 1 of the implementation order exists specifically to catch this risk early.
2. **`hasher.hexdigest()` return value on refused hashes.** Verified by spec-writing only — not by running py-tlsh 4.12.1 locally. The defensive check in `calc_tlsh` (broad `except` + `"TNULL"` + empty string) covers all plausible shapes, but the implementation step should include a one-line local REPL check against the installed library to confirm the real signal.
3. **Existing `ssdeep: string` typing in `types.ts`.** Possibly wrong (backend says nullable). Out of scope to fix, but if the frontend build barfs on `object.ssdeep` being potentially null under stricter TS settings triggered by the new nullable `tlsh` field, a one-line tightening is in scope as an incidental fix.
