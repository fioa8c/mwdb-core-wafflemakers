# PHP Deobfuscation & Normalization Pipeline — Design

**Status:** approved, ready for implementation planning
**Date:** 2026-04-19
**Fork:** `mwdb-core-wafflemakers` (diverged from upstream `CERT-Polska/mwdb-core`)
**Subsystem:** 3 of 3 for the jetpack-threat-library integration
**Depends on:** subsystem 1 (TLSH support, shipped), subsystem 2 (threat library ingestion, complete — 14,781 samples in mwdb)

## 1. Goal

Batch-normalize all PHP samples in mwdb by sending them through the existing PHP deobfuscation sandbox, storing the normalized code as child TextBlobs, and computing a "normalized TLSH" hash that enables semantic similarity matching (two functionally-identical backdoors with different variable names produce the same normalized-TLSH, even though their raw TLSH hashes differ).

## 2. The PHP sandbox

**Location:** `/Users/fioa8c/WORK/waffle-makers-tooling/security-research-malware-sandbox/`
**Production deployment:** `sandbox.wafflemakers.xyz`

The sandbox is an AST-based PHP deobfuscation tool using `nikic/php-parser` v5.6+. It provides:

- **Variable deobfuscation** (`/var_deobfuscate_web.php`): tracks variables built character-by-character through concatenation, resolves their final values, and pretty-prints the result. Returns JSON:
  ```json
  {
    "success": true,
    "deobfuscated_code": "<?php\n...",
    "variable_values": {"$a": "eval", "$b": "system"},
    "variables_found": 2,
    "original_size": 1200,
    "deobfuscated_size": 850
  }
  ```

- **Beautification** (`/beautify.php`): AST parse → pretty-print without variable resolution. Lighter, fewer failure modes. Returns JSON:
  ```json
  {
    "success": true,
    "beautified_code": "<?php\n...",
    "original_size": 6804,
    "beautified_size": 2542
  }
  ```

The sandbox runs in Docker (PHP 8.2 + Apache). It has no mwdb integration — this spec builds the bridge.

## 3. Architecture

### 3.1 New CLI subcommand: `mwdb-core normalize-php <sandbox-url>`

A Click command in `mwdb/cli/normalize_php.py`, registered in `mwdb/cli/cli.py`. Same pattern as the `import-jetpack-threat-library` command from subsystem 2. Invoked inside the mwdb Docker container, talks to the sandbox via HTTP over the Docker internal network.

### 3.2 Sandbox as a compose service

The sandbox runs as a Docker service defined in a **separate compose override file** (`docker-compose-sandbox.yml`) to avoid polluting the main dev compose:

```yaml
services:
  sandbox:
    build:
      context: ../waffle-makers-tooling/security-research-malware-sandbox
    networks:
      default:
        aliases:
          - sandbox
```

Started with: `docker compose -f docker-compose-dev.yml -f docker-compose-sandbox.yml up -d sandbox`

The CLI command then hits `http://sandbox:8000/var_deobfuscate_web.php` from inside the mwdb container. No rate limit (local container), no TLS overhead, ~10-50ms per request over Docker's internal network.

### 3.3 Core logic flow

```
1. Query all File objects where file extension is PHP-like (.php, .phtml, .php5, .php7, .inc)
2. Skip files that already have a `normalized_tlsh` attribute (idempotent re-run)
3. Auto-create AttributeDefinition("normalized_tlsh") if not exists
4. For each qualifying file:
   a. Read file content from mwdb storage (File.open() / File.read())
   b. POST content to <sandbox-url>/var_deobfuscate_web.php
   c. If response indicates failure (success=false or HTTP error):
      POST content to <sandbox-url>/beautify.php as fallback
   d. If both fail: log warning, skip, increment error counter
   e. Extract normalized code from response JSON
      (deobfuscated_code from var_deobfuscate, or beautified_code from beautify)
   f. Create child TextBlob:
      TextBlob.get_or_create(
          content=normalized_code,
          blob_name="<original_filename>.normalized",
          blob_type="normalized-php",
          share_3rd_party=False,
          parent=file_obj,
      )
   g. Compute TLSH of normalized code:
      calc_tlsh(BytesIO(normalized_code.encode("utf-8")))
   h. Store as attribute on the ORIGINAL File:
      file_obj.add_attribute("normalized_tlsh", tlsh_hash, check_permissions=False)
   i. db.session.commit()
   j. Print progress line
5. Print summary (processed / already-done / non-php-skipped / errors / wall-clock)
```

### 3.4 Why the normalized TLSH goes on the File, not the TextBlob

The similarity query is "which Files are similar after deobfuscation?" The File is the query target — analysts search for Files, not TextBlobs. Storing `normalized_tlsh` on the File means `attribute.normalized_tlsh:T1ABC...` returns the original malware samples directly.

The TextBlob is an artifact for human inspection ("what does this malware look like after deobfuscation?"), not a query target.

## 4. File filtering

### 4.1 Extension-based PHP filter

The CLI command queries all Files and filters by filename extension. PHP-like extensions: `.php`, `.phtml`, `.php5`, `.php7`, `.inc`.

The filter checks `File.file_name` using SQL `LIKE` patterns (case-insensitive). Files with non-PHP extensions are logged at debug level and skipped.

### 4.2 Why not filter by `file_type` (libmagic)?

libmagic's PHP detection is unreliable for obfuscated samples — heavily encoded PHP files often identify as "ASCII text" or "data" rather than "PHP script". Extension-based filtering is more inclusive and matches how the threat library organizes its samples.

### 4.3 Idempotency

Files that already have a `normalized_tlsh` attribute are skipped. This makes re-runs cheap — only new or previously-failed files get processed. To force re-processing of a specific file, remove its `normalized_tlsh` attribute first.

## 5. Data stored per sample

### 5.1 Child TextBlob (normalized code)

| Field | Value |
|---|---|
| `blob_name` | `<original_filename>.normalized` (e.g., `backdoor.php.normalized`) |
| `blob_type` | `normalized-php` |
| `content` | The deobfuscated/beautified PHP source |
| Parent relation | Child of the original File |

Deduplication: `TextBlob.get_or_create` hashes the content with SHA256. If two different obfuscated files normalize to identical code, they share the same TextBlob (different Files as parents). This is exactly the "same payload, different obfuscation" detection that motivates the pipeline.

### 5.2 Attribute on the original File

| Key | Value | Purpose |
|---|---|---|
| `normalized_tlsh` | `T1ABC...` (72-char TLSH hash of normalized code) | Semantic similarity matching. Query: `attribute.normalized_tlsh:T1ABC...` |

The attribute definition is auto-created at command startup (same pattern as `jpop_threat_name` in subsystem 2).

### 5.3 What is NOT stored

- No `size_reduction` attribute. The TextBlob preserves both sizes implicitly (original File has `file_size`, TextBlob has `blob_size`).
- No `dangerous_functions` attribute. The sandbox's `/analyze.php` endpoint provides this, but storing it is a separate concern — can be added later as a targeted enhancement without rearchitecting.
- No `normalization_method` attribute (whether var_deobfuscate or beautify succeeded). YAGNI — if you need to know, re-run the sandbox on the file and check.

## 6. Error handling

- **Sandbox HTTP errors** (connection refused, timeout, 5xx): log, skip, increment error counter. The sandbox may legitimately fail on deeply malformed PHP.
- **Sandbox reports `success: false`**: try fallback endpoint. If both fail, log and skip.
- **Empty normalized output** (sandbox returns empty string): skip, log as warning. No TextBlob created, no attribute set.
- **File read errors** (file missing from storage): log, skip.
- **TLSH computation fails** (normalized code too small/low-entropy): TextBlob is still created (the normalized code is valuable even without a TLSH hash), but `normalized_tlsh` attribute is not set. Logged at debug level.
- **Error threshold**: abort after 100 cumulative errors (same pattern as the import tool).
- **Crash recovery**: re-running is safe. Files with `normalized_tlsh` already set are skipped. TextBlob dedup handles duplicate content. The only wasted work is re-sending files that the sandbox already processed but whose results weren't committed before the crash.

## 7. Invocation

### 7.1 Prerequisites

- mwdb stack is up and healthy.
- Samples have been imported (subsystem 2 complete).
- The sandbox source is at `../waffle-makers-tooling/security-research-malware-sandbox/` relative to the mwdb-core repo.

### 7.2 Commands

```bash
# Start the sandbox service
docker compose -f docker-compose-dev.yml -f docker-compose-sandbox.yml up -d sandbox

# Run normalization
MWDB_ENABLE_HOOKS=0 docker compose -f docker-compose-dev.yml -f docker-compose-sandbox.yml run --rm \
  --entrypoint "" mwdb /app/.venv/bin/mwdb-core normalize-php http://sandbox:8000

# When done, stop the sandbox
docker compose -f docker-compose-dev.yml -f docker-compose-sandbox.yml stop sandbox
```

### 7.3 Expected runtime

~14,781 PHP samples × ~50ms per sandbox HTTP call ≈ **12-15 minutes**. Re-runs skip already-processed files.

### 7.4 Post-normalization verification

1. Check total TextBlobs created: `SELECT count(*) FROM object WHERE type='text_blob';`
2. Check normalized_tlsh coverage: count Files with the `normalized_tlsh` attribute.
3. Spot-check a known obfuscated threat in the SPA: navigate to the File, verify child TextBlob exists, click into it, verify normalized PHP is readable.
4. Search `attribute.normalized_tlsh:T1...` with a specific hash — verify it returns the expected File.

## 8. Scope boundaries

### In scope

- `mwdb/cli/normalize_php.py` — the Click subcommand
- Registration in `mwdb/cli/cli.py`
- `docker-compose-sandbox.yml` — compose override for the sandbox service
- `normalized_tlsh` attribute definition auto-creation
- Child TextBlob creation with parent relation
- Progress output and error summary

### Out of scope (YAGNI)

- Auto-trigger on upload (karton service, webhooks, or post-upload hooks). Batch only.
- Security analysis via `/analyze.php` (dangerous function detection). Separate enhancement.
- Similarity search endpoint (`GET /file/<id>/similar?algorithm=normalized_tlsh`). Phase-2 of the TLSH work, already deferred.
- Any changes to the sandbox tool itself.
- `size_reduction`, `dangerous_functions`, or `normalization_method` attributes.
- Processing non-PHP files through the sandbox.

## 9. Testing

### 9.1 Fixture test

Before running against the full corpus:
1. Upload a small obfuscated PHP file to mwdb manually (via SPA or API).
2. Start the sandbox service.
3. Run `mwdb-core normalize-php http://sandbox:8000`.
4. Verify:
   - The File now has a child TextBlob with `blob_type="normalized-php"`.
   - The TextBlob content is deobfuscated/beautified PHP.
   - The File has a `normalized_tlsh` attribute with a valid T1-prefixed hash.

### 9.2 Post-batch verification

Covered in §7.4.

## 10. Implementation order

1. Create `docker-compose-sandbox.yml` and verify the sandbox service starts and responds.
2. Create `mwdb/cli/normalize_php.py` with the full CLI command.
3. Register in `mwdb/cli/cli.py`.
4. Fixture test against a single obfuscated sample.
5. Run the full batch normalization.
6. Post-batch verification (§7.4).
