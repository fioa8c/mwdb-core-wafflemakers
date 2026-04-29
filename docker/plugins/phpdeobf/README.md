# phpdeobf — MWDB plugin

Adds a "PHP Deobfuscate" tab to the sample detail page. Click → run the sample
through the `phpdeobf` sidecar → create a `TextBlob` child of the sample with
`blob_type="deobfuscated-php"`.

See spec: `docs/superpowers/specs/2026-04-29-phpdeobf-plugin-design.md`.

## Configuration

| Env var (mwdb service) | Default | Description |
|---|---|---|
| `MWDB_PHPDEOBF_URL` | `http://phpdeobf:8080` | Sidecar base URL. |

| Env var (sidecar) | Default | Description |
|---|---|---|
| `MAX_INPUT_BYTES` | `5242880` (5 MB) | Per-request source size cap. |

The plugin enforces a matching 5 MB cap on the sample side (defense-in-depth).
The plugin enforces a 30 s wall-clock timeout on the sidecar call.

## Vendored source

The PHP deobfuscator source lives under `docker/phpdeobf/` (NOT this directory).
That's the build context for the sidecar image. To refresh from
`~/WORK/PHPDeobfuscator/`:

    bash docker/phpdeobf/sync-from-source.sh

Then review and commit.

## Tests

Plugin unit tests (mocked sidecar):

    cd docker/plugins/phpdeobf && python -m pytest tests/ -v

Sidecar HTTP wrapper tests:

    php docker/phpdeobf/tests/server_test.php

End-to-end test (requires running stack):

    cd tests/backend && uv run pytest test_phpdeobf.py -v
