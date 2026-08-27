# wpsandbox — MWDB plugin

Adds a "WP Sandbox" tab to the sample page. A run executes the sample inside a disposable
SecEx WordPress microVM and attaches a behaviour report (`TextBlob`, `blob_type="wp-sandbox-report"`)
plus attributes `c2_host`, `dropped_file`, `wp_user_added` to the sample.

Spec: `docs/superpowers/specs/2026-08-27-wpsandbox-plugin-design.md`.
Components: this plugin (API + tab), `docker/wpsandbox-worker/` (executes runs), `docker/wpsandbox-template/` (SecEx template + harness).

## API

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/wpsandbox/<hash>` | body `{mode, path?, method?, query?, body?, timeout?}` → `202 {run_id}`; needs `adding_blobs` |
| `GET` | `/api/wpsandbox/<hash>` | `{runs: [...]}` newest first |
| `GET` | `/api/wpsandbox/run/<id>` | poll |
| `PATCH` | `/api/wpsandbox/run/<id>` | worker only |
| `DELETE` | `/api/wpsandbox/run/<id>` | cancel while `queued` |

## Configuration (mwdb service)

| Env | Default |
|---|---|
| `MWDB_WPSANDBOX_REDIS_URL` | `MWDB_REDIS_URI` |
| `MWDB_WPSANDBOX_WORKER_LOGIN` | `wpsandbox-worker` |
| `MWDB_WPSANDBOX_MAX_SAMPLE_BYTES` | `20971520` |
| `MWDB_WPSANDBOX_MAX_TIMEOUT` | `300` |

## Tests

    cd docker/plugins/wpsandbox && python -m pytest tests/ -v
