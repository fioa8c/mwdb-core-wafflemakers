# Threat Library Sync and Curation (`threatlib`) — Design

**Status:** approved, ready for implementation planning
**Date:** 2026-09-24
**Fork:** `mwdb-core-wafflemakers`
**Related:** `2026-04-17-jetpack-threat-library-ingestion-design.md` (the one-shot April import this supersedes), `2026-04-29-phpdeobf-plugin-design.md` (plugin layout), wpsandbox branch (plugin-owned tables pattern).

## 1. Goal

Make MWDB the canonical home of the Jetpack threat library while keeping the
`jetpack-threat-library` Git repository alive as a **generated artefact**, so
the YARA rules CI and teammates' scripts keep working unchanged.

Two things are delivered together, because neither is useful alone:

1. **Sync.** A sidecar regenerates the four malware directories of the repo
   from MWDB and pushes to `trunk`, after first ingesting anything new that
   landed in the repo by hand.
2. **Curation.** A first-class *Threat* entity, a one-form upload page, and a
   threat page, so adding a sample to a threat in MWDB is as cheap as a
   pull request is today.

## 2. Context and constraints

- Canonical repo: `git@github.a8c.com:Automattic/jetpack-threat-library.git`,
  default branch `trunk`, **no branch protection**. The github.com copy is a
  stale mirror and is ignored.
- The repo is a live team workflow: ~850 commits since June 2026, one PR per
  sample, branches named `add/sample/<signature-id>`.
- The YARA rules repo (`jetpack-scan-yara-rules`) CI mounts the threat
  library and runs `test-detections.py` and `check-false-positives.py`. The
  contract:
  - a rule's `examples = "a, b"` metadata names folders under `threats/` or
    `for-later-review/`; the rule must match at least one file inside each;
  - the rule must match nothing under `false-positives/`.
- The April import walked one level deep only. **2,931 files** nested
  deeper (mostly `threats/sources/<id>/wp-admin/...` and
  `threats/htaccess_samples/...`) never reached MWDB. Rules reference
  `sources/0154`-style examples, so the nested layout must round-trip.
- Plugins can register hooks, resources, converters and schemas, but not CLI
  commands. The backend image has no `git`.

### 2.1 Decisions taken during brainstorming

| decision | choice |
|---|---|
| Repo role | MWDB canonical, repo generated |
| Scope of generated dirs | `threats/`, `for-later-review/`, `webshells/`, `escalated_issues_samples/` only. `false-positives/` and every other dir stay hand-maintained. |
| Threat model | First-class plugin-owned entity, with tag + attribute mirrored on samples |
| Delivery | Bot pushes straight to `trunk`; repo changes are ingested before each export |
| Curation v1 | Inline threat creation on upload, multi-file and folder drop, threat page with editable README and category |
| Sync placement | Sidecar container running the plugin's console script with direct DB access (Approach A) |

## 3. Data model

Plugin `docker/plugins/threatlib/`, dual Python + npm layout like `phpdeobf`.
Tables are created idempotently at `entrypoint` (`model.ensure_schema()`),
as the wpsandbox plugin does; MWDB has no plugin migrations.

### `threatlib_threat`

| column | type | notes |
|---|---|---|
| `id` | integer PK | |
| `name` | text, unique | repo folder name (`FIO-7243`, `php_uploader_generic_019_2`, `sources`) |
| `category` | enum | `threats`, `for-later-review`, `webshells`, `escalated_issues_samples` |
| `readme` | text, nullable | markdown, exported verbatim as `README.md` |
| `created_by` | text | MWDB login |
| `created_at`, `updated_at` | timestamptz | |

### `threatlib_threat_sample`

| column | type | notes |
|---|---|---|
| `threat_id` | FK `threatlib_threat.id`, `ON DELETE CASCADE` | |
| `object_id` | FK `object.id`, `ON DELETE CASCADE` | the `File` |
| `rel_path` | text | path inside the threat folder: `1.php`, `0154/wp-admin/menu.php` |
| `added_at` | timestamptz | |

Primary key `(threat_id, rel_path)`. A sample may sit in several threats,
and one threat may hold the same sha256 under two paths (the repo already
does both).

`webshells` is the flat category. Its threats are one-file threats whose
name is the file's stem and whose single link has `rel_path` = the
filename, so the exporter treats all four categories uniformly.

### Mirror on the sample

On every link write, the plugin also applies the category as a **tag** and
`jpop_threat_name` as an **attribute** on the `File`, and removes them on
unlink (the attribute only when the file has no remaining links). Upstream
search and existing YARA-hunting habits keep working with no plugin
awareness. The tables are the source of truth; the sync's repair pass
(§4 step 4) reconciles the mirror from the tables.

### Deletion semantics

- Deleting a threat unlinks its samples; `File` objects are never deleted
  by the plugin.
- Deleting a `File` in MWDB cascades the links; the next export drops it
  from the repo.
- The README lives only on the threat. The per-sample README comments left
  by the April importer are kept untouched and are read once by the
  catch-up migration to seed `readme`.

### Catch-up migration

Performed by the sync's first run, before the normal ingest: for every
`File` carrying `jpop_threat_name`, create the threat (category from the
category tag, `readme` from the repo clone's `README.md` for that folder
when present, else from the file's first comment) and link it with
`rel_path` = its stored `file_name`. Then the normal ingest (§4 step 3)
picks up everything the April import missed, including nested trees.

## 4. Sync loop (`threatlib-sync` sidecar)

### Container

Service `threatlib-sync` in `compose/compose.with-plugins.yml` and
`docker-compose-prod.yml`, built from the backend image plus `git`. Mounts a
volume holding the clone. Environment:

| variable | meaning |
|---|---|
| `MWDB_THREATLIB_REPO_URL` | SSH URL of the repo |
| `MWDB_THREATLIB_DEPLOY_KEY` | path to the private deploy key (mounted secret) |
| `MWDB_THREATLIB_SYNC_INTERVAL` | seconds between runs, default `900` |
| `MWDB_THREATLIB_PUSH` | `0` disables the push (rollout step 1), default `1` |
| `MWDB_THREATLIB_GIT_AUTHOR` | bot identity for commits |
| `MWDB_ENABLE_HOOKS` | set to `0` on this service so ingest does not fire plugin hooks per file |

plus the same database settings as the `mwdb` service. The plugin's
`pyproject.toml` exposes a console script `threatlib-sync` that boots the
Flask app the way `mwdb/cli/import_threat_library.py` does. `--once` runs a
single pass (manual use, rollout, tests); without it the script loops.

### One run

1. **Pull.** `git fetch`; hard-reset the clone to `origin/trunk`. Abort the
   run if the remote is unreachable.
2. **Read the manifest.** `.mwdb-threatlib.json` at the repo root, committed
   by the previous export. It maps every exported path under the four dirs
   to its sha256 and each threat's `README.md` to its sha256. No manifest
   means first run: every path is "new".
3. **Ingest.** Walk the four dirs.
   - A sample file whose path is **absent from the manifest** is new:
     `File.get_or_create`, create the threat if needed (category = top dir;
     name = next segment, or the stem for `webshells`), link with the
     remaining relative path as `rel_path`.
   - A `README.md` whose hash differs from the manifest is a repo-side
     edit: copy it into the threat if the threat has no `readme`, or if
     the sha256 of the threat's current `readme` equals the README hash
     recorded in the manifest (i.e. MWDB did not change it since the last
     export). Otherwise MWDB wins.
   - A path **present in the manifest but missing from the repo** is a
     repo-side deletion: unlink it.
   - Everything else is untouched.
4. **Repair the mirror.** For every linked file ensure the category tag and
   `jpop_threat_name` attribute; strip the attribute from files with no
   links. Idempotent; the catch-up migration is a special case of this
   pass.
5. **Export.** Delete the four dirs in the clone and rewrite them from the
   tables: `<category>/<name>/<rel_path>` per link, `README.md` where
   `readme` is set, bytes streamed from object storage. Write the new
   manifest.
6. **Commit and push.** Clean `git status` → stop. Otherwise commit as the
   bot with a message summarising counts (added, removed, READMEs changed)
   and push to `trunk`. On a non-fast-forward rejection do nothing; the next
   run starts again from the remote. No rebase logic.

### Why ingest before export

A PR merged by a teammate between runs is absorbed into MWDB before the
tree is regenerated, so the export never reverts it. Deletions made in MWDB
survive because the *manifest*, not the working tree, defines "new in
repo".

### Failure handling

Any exception in steps 3–5 rolls back the DB session and skips the push, so
the repo never receives a half-written tree. Per-file ingest errors
(unreadable file, storage failure) are logged, counted, skipped, and the
run continues. The summary goes to the container log. No run-history
table in v1.

### Concurrency

One sync process. Plugin REST writes from request workers are single short
transactions, so the sync sees a consistent state per file. No advisory
locks in v1.

## 5. REST API

All under `/api/threatlib/`, registered with `register_resource`.

| method and path | purpose | capability |
|---|---|---|
| `GET /threatlib/threat?query=&category=&page=` | list threats; name-prefix search for autocomplete; sample count per threat | logged-in |
| `POST /threatlib/threat` | create `{name, category, readme}` | `adding_files` |
| `GET /threatlib/threat/<name>` | threat with samples (`sha256`, `file_name`, `rel_path`, `added_at`) | logged-in |
| `PUT /threatlib/threat/<name>` | update `readme` and/or `category` | `adding_files` |
| `DELETE /threatlib/threat/<name>` | delete threat, unlinking samples | `removing_objects` |
| `POST /threatlib/threat/<name>/sample` | link an existing file `{sha256, rel_path}`; applies the mirror | `adding_files` |
| `DELETE /threatlib/threat/<name>/sample/<sha256>?rel_path=` | unlink one path | `adding_files` |
| `POST /threatlib/upload` | multipart: `threat`, plus `category` + `readme` when creating, `files[]` with parallel `rel_paths[]`; returns threat and per-file result | `adding_files` |

Rules:

- **Names** match `^[A-Za-z0-9._-]+$`, are not `.` or `..`, and are unique.
  **`rel_path`** is normalised, relative, without `..` segments or a leading
  `/`. Both are validated before touching storage because they become
  filesystem paths in the export.
- **Category** is one of the four values. Changing it on `PUT` is the
  "promote from for-later-review" operation; the mirror tag on every linked
  sample is swapped in the same transaction.
- **Upload** uses `File.get_or_create` exactly like the standard upload, so
  hashes, dedup, storage and standard hooks apply. If a `rel_path` already
  exists on the threat with a different sha256, that file gets `409` and
  the others proceed. The response says per file: `new`, `existing`, or
  `rejected` with a reason.
- **Existing capabilities only.** `adding_files` gates create/link/edit;
  `removing_objects` gates threat deletion. No new capability.
- **Sharing.** Uploaded files inherit the uploader's default sharing like
  the standard form. Threats have no ACL; the sample list is filtered by
  the caller's object access.

Errors: `400` validation, `403` capability, `404` unknown threat or sha256,
`409` conflict.

## 6. Frontend

`index.tsx` exports `protectedRoutes` (the pages), `navdropdownExtras`
("Threat library" entry, all logged-in users) and `attributeRenderers`
(`jpop_threat_name` renders as a link to the threat page). The standard
upload form is not modified.

- **`/threatlib`** — table of threats: name, category badge, sample count,
  updated time. Name-prefix search box and category filter. Row click →
  threat page. "Add samples" → upload page.
- **`/threatlib/upload`** — one form.
  - *Threat*: text input with autocomplete on `GET /threatlib/threat?query=`.
    Picking an existing threat locks category and hides README (link to
    edit it on the threat page). Typing a new name reveals the category
    selector and a README editor pre-filled with `# <name>`.
  - *Files*: drop zone for files and folders; folders are read with
    `webkitGetAsEntry` so each file carries its path relative to the dropped
    folder root, which becomes `rel_path`. Plain files use their filename.
    List shows path and size with a remove button.
  - *Submit*: one `POST /threatlib/upload`. Result view lists each file as
    new / existing / rejected with reason, and links to the threat page.
- **`/threatlib/threat/<name>`** — two columns like the sample view. Left:
  name, category badge with "Change category" select, created by, dates,
  README rendered with the SPA's existing `marked` dependency, "Edit"
  toggle to a textarea and save. Right: sample table with `rel_path`,
  filename, sha256 (link to sample), added date, unlink icon with
  confirmation. "Delete threat" at the bottom (confirmation; shown only
  with `removing_objects`).
- Edit, unlink and add controls render only with `adding_files`; otherwise
  the pages are read-only.
- Everything lives under `docker/plugins/threatlib/` and uses the SPA's
  exported components (`ObjectLink`, `ConfirmationModal`,
  `@mwdb-web/commons/api`), so upstream syncs stay clean.

## 7. Testing

- **Plugin unit tests** (`docker/plugins/threatlib/tests/`, no stack):
  name and `rel_path` validation; link/unlink apply and remove the mirror;
  category change swaps tags on all linked samples; upload dedup and `409`
  on path conflict.
- **Sync tests** against a temporary git repo and a fixture tree: first run
  imports everything and writes the manifest; a file added to the repo
  between runs is ingested; a link removed in MWDB disappears from the repo
  and is not re-ingested; a README edited in the repo reaches the threat; a
  README edited in MWDB wins; nested `sources/<id>/...` paths round-trip
  byte for byte; a clean tree produces no commit; a push rejection leaves
  the clone untouched.
- **Round-trip property**: export the fixture, ingest into an empty
  database, export again, assert identical trees.
- **Backend e2e** (`tests/backend/test_threatlib.py`, needs the stack): the
  REST flows over HTTP.
- **Frontend**: no Cypress in v1; manual.

## 8. Rollout

1. Deploy plugin and sidecar to prod with `MWDB_THREATLIB_PUSH=0`. The first
   run performs the catch-up migration and ingests everything the April
   import missed. Diff the clone volume against `trunk`: the expected diff
   is the manifest file only. That diff is the acceptance test.
2. Set `MWDB_THREATLIB_PUSH=1`. The first commit adds only the manifest.
3. Announce MWDB as canonical for the four dirs. Hand-made PRs remain safe
   during the transition because ingest runs first. Point the YARA rules
   repo README at the Threat library page for adding samples.
4. After a quiet period with no hand-made PRs, mark the four dirs as
   generated in the repo README.

## 9. Non-goals (v1)

- `false-positives/` and every other repo directory.
- A "Threat library" tab on the sample page (attach an existing sample from
  there). The REST link endpoint exists; the UI comes later.
- Run-history table, "sync now" button, advisory locks.
- Signature linkage, IoC extraction, feeds (roadmap pieces 3–5).
- Two-way README merge; MWDB wins on conflict.
- Cypress coverage.

## 10. Known risk

`sources` is one threat holding 2,208 files, and rules reference
`sources/0154`-style examples. Keeping `sources` as a single threat with
deep `rel_path` values round-trips correctly and satisfies CI. Splitting it
into `sources/<id>` threats is a later curation decision, not a v1 need.

## 11. Roadmap context

This is project 1 of the fork's larger direction (MWDB as the PHP/JS-friendly
single source of truth, evolving toward an OTX-like threat-intel platform):

1. **Threat library sync + curation** — this spec.
2. PHP/JS-native handling: source-aware views, normalized/deobfuscated side
   by side, JS as first-class, IoC extraction into attributes.
3. Detection linkage: which signatures match which samples, coverage per
   threat, CI reading the corpus from MWDB.
4. Threat-intel layer: IoCs as objects, reports/pulses, feeds, sharing.
