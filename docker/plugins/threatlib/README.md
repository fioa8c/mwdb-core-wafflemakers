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

Paths that could not be ingested (storage errors, invalid threat names such
as `My Sample (2)`, a folder whose name is already a threat in another
category or layout, symlinks) are logged as a WARNING, written back to the
repo unchanged and left out of the manifest, so they are retried on every run
until fixed by hand. Symlinks are never followed or ingested.

| env | default | meaning |
|---|---|---|
| `MWDB_THREATLIB_REPO_URL` | (required) | git URL of the library |
| `MWDB_THREATLIB_BRANCH` | `trunk` | branch to sync |
| `MWDB_THREATLIB_DEPLOY_KEY` | unset | ssh private key path (must be readable by `nobody`) |
| `MWDB_THREATLIB_KNOWN_HOSTS` | unset | ssh known_hosts file; when set the host key is pinned (`StrictHostKeyChecking=yes`), otherwise trusted on first use |
| `MWDB_THREATLIB_USER` | the admin login (`MWDB_ADMIN_LOGIN`) | MWDB user the sync uploads, shares and tags as; the sync exits with code 2 if it does not exist |
| `MWDB_THREATLIB_SHARE_WITH` | `public` | group every ingested sample is shared with (see below) |
| `MWDB_THREATLIB_SYNC_INTERVAL` | `900` | seconds between runs |
| `MWDB_THREATLIB_PUSH` | `1` | `0` commits locally without pushing |
| `MWDB_THREATLIB_GIT_AUTHOR` | `mwdb-threatlib-bot <noreply@wafflemakers.xyz>` | commit author |
| `MWDB_THREATLIB_CLONE_DIR` | `/data/repo` | working clone |

**Sharing.** Every sample the sync ingests is shared with
`MWDB_THREATLIB_SHARE_WITH`, including samples already in MWDB from the
April import, which are re-matched by sha256 and gain that share. The default
`public` makes the library visible to every logged-in researcher, which is
the intent. Set the variable to an empty string to share with nobody (only
the sync user and admins see ingested samples).

Run once by hand (prod: `docker compose -f docker-compose-prod.yml run ...`;
dev, where the service only exists in the plugins overlay, use a separate
clone dir so the pass does not share `/data/repo` with the running loop):

    ./compose.sh --with dev --with plugins run --rm \
        -e MWDB_THREATLIB_CLONE_DIR=/tmp/clone threatlib-sync threatlib-sync --once

## Tests

    cd docker/plugins/threatlib && python -m pytest tests/ -v     # unit, no stack
    cd tests/backend && uv run pytest test_threatlib.py -v          # e2e, needs stack
    # sync e2e: pushes to the dev bare repo and runs one sync pass (from the repo root's
    # compose project; skipped unless both variables are set)
    cd tests/backend && MWDB_THREATLIB_E2E_REPO=$PWD/../../dev-threatlib-origin.git \
      MWDB_THREATLIB_E2E_SYNC_CMD="./compose.sh --with dev --with plugins run --rm -e MWDB_THREATLIB_CLONE_DIR=/tmp/clone threatlib-sync threatlib-sync --once" \
      uv run pytest test_threatlib_sync.py -v
