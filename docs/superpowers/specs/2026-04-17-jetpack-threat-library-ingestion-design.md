# Jetpack Threat Library Ingestion — Design

**Status:** approved, ready for implementation planning
**Date:** 2026-04-17
**Fork:** `mwdb-core-wafflemakers` (diverged from upstream `CERT-Polska/mwdb-core`)
**Subsystem:** 2 of 3 for the jetpack-threat-library integration
**Depends on:** subsystem 1 (TLSH support, shipped on master as of `32bccb8`)

## 1. Goal

One-shot migration of ~17,000 malware samples from the `jetpack-threat-library` Git repository into mwdb-core, preserving category labels, threat names, and human documentation. After this migration, mwdb becomes the authoritative source for the threat collection; the Git repository will be deprecated. Future samples are added directly to mwdb via the existing upload workflows (SPA or API).

## 2. Source repository

**Path (on the host):** `/Users/fioa8c/WORK/jetpack-threat-library`

**Scoped directories (everything else is excluded):**

| Directory | Layout | Samples | Size | Description |
|---|---|---|---|---|
| `threats/` | Nested | ~10,107 | 453 MB | Hand-curated, named-family threat library |
| `for-later-review/` | Nested | ~6,764 | 265 MB | Triage queue — samples pending classification |
| `webshells/` | Flat | 42 | 3.4 MB | Web shell collection |
| `escalated_issues_samples/` | Nested | ~67 | 2.1 MB | Samples tied to escalated security cases |

**Total:** ~17,000 sample files, ~724 MB.

**Excluded:** `sample-dump/`, `false-positives/`, `binary_or_data_files/`, `JoomlaSamples/`, `MagentoSamples/`, `DrupalSamples/`, `php-backdoors-obfuscated/`, `php-backdoors-deobfuscated/`, `obfuscation/`, `oddSamples/`, `bugged_samples/`, `sample_breaking_parser/`, `hardening/`, `core-modifications/`, `database-threats/`, `mycryptocheckout/`, `new_samples/`, `drupal-9.2.7/`, and all top-level repo files (scripts, logs, package.json, etc.).

### 2.1 Directory layouts

**Nested** (`threats/`, `for-later-review/`, `escalated_issues_samples/`):

```
<category>/<sample-name>/
    README.md        (optional — human documentation)
    <filename>.php   (one or more sample files)
```

Each subdirectory is a named threat. The subdir name (e.g. `_admin_z_init`, `FIO-3079`, `149237664`) is meaningful — often a JPOP issue ID or a semantic family name. Multiple sample files in one subdir share the same README and threat name.

**Sample file definition:** any file that is NOT `README.md` and does NOT start with `.` (excludes `.DS_Store`, `.gitignore`, etc.).

**Flat** (`webshells/`):

```
<category>/
    <filename>.php   (each file is an independent sample)
```

No subdirectories, no per-sample READMEs.

### 2.2 README metadata

READMEs are free-text Markdown. Some contain structured fields:

```markdown
# bendigital2019_00001

* Date added: 2023-03-02
* Original filename: index.txt

This file is from a bulk download of malware files from...
```

Others are purely narrative:

```markdown
# _admin_z_init

Malware found while investigating [this JPOP issue](...).
```

**No parsing of structured fields.** The full README content is stored as a single comment on the sample. If specific fields become important later, a follow-up script can parse existing comments into attributes. YAGNI for now.

## 3. Metadata mapping

| Source | mwdb destination | Details |
|---|---|---|
| Category dir name | **Tag** | Literal dirname used as the tag value: `threats`, `for-later-review`, `webshells`, `escalated_issues_samples`. One tag per sample. No normalization. |
| Sample subdir name | **Attribute** `jpop_threat_name` | The subdir's basename (e.g. `_admin_z_init`, `FIO-3079`). Only for nested-layout dirs; flat-layout samples have no subdir name and skip this. |
| README.md content | **Comment** (author = admin) | Raw markdown, full content. Only when README exists. Multiple samples sharing a subdir all receive the same comment. |
| On-disk filename | `file_name` param to `File.get_or_create()` | mwdb stores the first filename; subsequent uploads of the same sha256 append to `alt_names`. |

### 3.1 Attribute definition prerequisite

mwdb requires attribute keys to be pre-registered as `AttributeDefinition` before assignment. The importer creates the `jpop_threat_name` definition at startup if it doesn't already exist (idempotent check). This is not a manual prerequisite.

### 3.2 Quirks

- **Subdirs with multiple sample files and one README:** all samples get the same tag, attribute, and comment. Correct behavior — they're variants of the same named threat.
- **Subdirs with README but no sample files:** logged at debug level, skipped silently.
- **Duplicate sha256 across subdirs:** mwdb's `File.get_or_create()` handles this natively — the first upload creates the sample, subsequent uploads add the on-disk filename to `alt_names`. Tags and attributes are additive (tag/attribute applied if not already present).

## 4. Architecture

### 4.1 New module: `mwdb/cli/import_threat_library.py`

A Click subcommand registered in `mwdb/cli/cli.py`, following the pattern of existing commands (`configure`, `create_admin`). Invoked as:

```bash
MWDB_ENABLE_HOOKS=0 docker compose -f docker-compose-dev.yml run --rm \
  -v /Users/fioa8c/WORK/jetpack-threat-library:/import \
  mwdb mwdb-core import-jetpack-threat-library /import
```

- **`-v` mount:** makes the threat-library repo visible inside the container at `/import`. One-shot override; no changes to `docker-compose-dev.yml`.
- **`MWDB_ENABLE_HOOKS=0`:** suppresses post-upload hooks (karton dispatch, webhooks) during import. Pure data ingestion; downstream processing can be triggered later if needed.

### 4.2 Core logic flow

```
1. Validate <path> — check the four scoped directories exist
2. Create AttributeDefinition("jpop_threat_name") if not exists
3. For each scoped directory:
   a. Determine layout (nested vs flat)
   b. Discover all sample files (non-README.md, non-hidden files)
   c. Count total for progress display
4. For each sample file:
   a. Open as binary stream
   b. Call File.get_or_create(file_name, file_stream, share_3rd_party=False)
   c. Add tag (category dir name)
   d. If nested: add attribute jpop_threat_name = subdir basename
   e. If README.md exists in subdir: add comment with README content
   f. db.session.commit()
   g. Print progress line
   h. Release file stream (File.release_after_upload)
5. Print summary (imported / skipped / errors / wall-clock)
```

### 4.3 Call into `File.get_or_create()`

The importer calls `File.get_or_create()` directly — the same code path as API uploads. This means:
- All hashes are computed automatically (md5, sha1, sha256, sha512, ssdeep, TLSH, crc32).
- sha256 dedup works natively (`File.get_or_create` returns `(file_obj, is_new)`; when `is_new=False`, the sample already exists and the filename is appended to `alt_names`).
- File storage (disk or S3) is handled by the existing storage provider logic.
- No code duplication for any of the above.

### 4.4 Tag / attribute / comment application

Tags, attributes, and comments are applied using mwdb's existing model methods. The importer checks whether a tag/attribute already exists on the object before adding (idempotent — safe for re-runs after crashes).

## 5. Error handling

- **File-read errors** (permission denied, binary corruption): log a warning with the file path and exception, skip the file, increment error counter, continue.
- **Database errors** (constraint violation, connection drop): roll back the current file's transaction, log, skip, continue.
- **Error threshold:** if cumulative errors exceed 100 (hardcoded default), abort with a summary of what failed. This prevents silent runaway failures.
- **Crash recovery:** re-running the importer after a crash is safe. mwdb's sha256 dedup means already-imported samples become no-ops (with potential alt_name additions). Tags/attributes/comments are checked before adding, so no duplicate metadata. The importer does NOT need explicit checkpoint/resume logic.

## 6. Progress output

```
[1/17000] Imported threats/_admin_z_init/png.db.php (sha256: abc123... tlsh: T1DEF456...)
[2/17000] Imported threats/_admin_z_init/init.php (sha256: def456... tlsh: T1GHI789...)
[3/17000] Skipped (duplicate) threats/149237664/jp-helper.php (sha256: abc123...)
[4/17000] ERROR threats/broken/corrupt.php: Permission denied
...
[17000/17000] Done.

Summary:
  Imported: 16,800
  Skipped (duplicate): 180
  Errors: 20
  Wall-clock: 3m 42s
```

## 7. Invocation and environment

### 7.1 Prerequisites

- mwdb stack is up and healthy (`docker compose up -d`).
- The `gen_vars.sh` (non-test variant) has been run — rate-limit on, hooks on (but overridden to off via `MWDB_ENABLE_HOOKS=0` in the command).
- The `jetpack-threat-library` repo is at `/Users/fioa8c/WORK/jetpack-threat-library`.
- The admin user exists (created by initial `flask db upgrade`).

### 7.2 One-liner

```bash
MWDB_ENABLE_HOOKS=0 docker compose -f docker-compose-dev.yml run --rm \
  -v /Users/fioa8c/WORK/jetpack-threat-library:/import \
  mwdb mwdb-core import-jetpack-threat-library /import
```

### 7.3 Post-import manual verification

Spot-check in the SPA (`http://127.0.0.1/`):

1. Search `tag:threats` — should return ~10K samples.
2. Open a known threat (e.g. search `attribute.jpop_threat_name:_admin_z_init`) — verify tag, attribute, comment present, TLSH hash visible.
3. Search `tag:webshells` — should return 42 samples, none with `jpop_threat_name` attribute.
4. Search `tag:for-later-review` — should return ~6.7K samples.
5. Upload a new sample via the SPA — confirm mwdb is usable as the new source of truth.

## 8. Scope boundaries

### In scope
- CLI subcommand `import-jetpack-threat-library` in `mwdb/cli/`
- Registration in `mwdb/cli/cli.py`
- `jpop_threat_name` attribute definition auto-creation
- Tags, attributes, comments per the mapping in §3
- Progress output and error summary
- Integration test for the subcommand

### Out of scope (YAGNI)
- Parsing structured README fields ("Date added:", "Original filename:") into separate attributes.
- Parent/child relations between samples (even when READMEs mention related files).
- Re-import / sync / git-tracking logic. This is a one-shot migration.
- Dry-run mode. (Re-running is inherently safe; `find` on the host gives a preview.)
- Upload-size-limit handling. (CLI bypasses HTTP; max file in scope is 47 MB, handled natively.)
- Karton / webhook triggering during import (suppressed via `MWDB_ENABLE_HOOKS=0`).
- Any changes to the `jetpack-threat-library` repo itself.

## 9. Testing

### 9.1 Integration test

Add a test to `tests/backend/` that:
1. Creates a temporary directory matching the expected layout (one nested-style subdir with a sample + README, one flat-style sample).
2. Invokes the CLI command programmatically (via Click's `CliRunner`).
3. Asserts:
   - The nested sample exists in mwdb with the correct tag, `jpop_threat_name` attribute, and comment.
   - The flat sample exists with the correct tag, no `jpop_threat_name` attribute, no comment.
   - Summary output includes correct counts.

Note: this test requires the mwdb app context and a live database, consistent with the project's existing integration-test approach.

### 9.2 Post-import manual verification

Covered in §7.3.

## 10. Implementation order

1. Create `mwdb/cli/import_threat_library.py` with the Click subcommand.
2. Register the subcommand in `mwdb/cli/cli.py`.
3. Add the integration test.
4. Run the importer against the real threat library.
5. Manual verification (§7.3).
