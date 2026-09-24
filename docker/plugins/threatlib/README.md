# threatlib — Threat library plugin

First-class *Threat* entity (a named folder of samples with a README and a
category), a curation API, and the `threatlib-sync` sidecar that keeps the
`jetpack-threat-library` repository generated from MWDB.

Spec: `docs/superpowers/specs/2026-09-24-threatlib-sync-and-curation-design.md`.

## API (`/api/threatlib/`)

| method and path | purpose | capability |
|---|---|---|
| `GET /threat?query=&category=&page=&per_page=` | list threats (name prefix search) | logged-in |
| `POST /threat` `{name, category, readme}` | create | `adding_files` |
| `GET /threat/<name>` | threat + samples | logged-in |
| `PUT /threat/<name>` `{readme?, category?}` | edit | `adding_files` |
| `DELETE /threat/<name>` | delete (samples unlinked, never deleted) | `removing_objects` |
| `POST /threat/<name>/sample` `{sha256, rel_path}` | link existing sample | `adding_files` |
| `DELETE /threat/<name>/sample/<sha256>?rel_path=` | unlink | `adding_files` |
| `POST /upload` multipart `threat, category?, readme?, files[], rel_paths[]` | upload into a threat | `adding_files` |

Every link writes the category as a tag and `jpop_threat_name` as an
attribute on the sample so upstream search keeps working.

## Sync loop

Each run (default every 15 min): pull `trunk` → ingest paths absent from the
committed manifest `.mwdb-threatlib.json` (plus repo-side README edits and
deletions) → repair the tag/attribute mirror → regenerate the four owned dirs
(`threats/`, `for-later-review/`, `webshells/`, `escalated_issues_samples/`)
→ commit and push as the bot. Files directly under `threats/`,
`for-later-review/` and `escalated_issues_samples/` are never touched;
`false-positives/` and every other dir stay hand-maintained.

Env: `MWDB_THREATLIB_REPO_URL`, `MWDB_THREATLIB_DEPLOY_KEY`,
`MWDB_THREATLIB_SYNC_INTERVAL` (900), `MWDB_THREATLIB_PUSH` (1),
`MWDB_THREATLIB_GIT_AUTHOR`, `MWDB_THREATLIB_CLONE_DIR` (/data/repo),
`MWDB_THREATLIB_SHARE_WITH` (public). Run once by hand:
`docker compose run --rm threatlib-sync threatlib-sync --once`.

## Tests

    cd docker/plugins/threatlib && python -m pytest tests/ -v     # unit, no stack
    cd tests/backend && uv run pytest test_threatlib.py -v          # e2e, needs stack
