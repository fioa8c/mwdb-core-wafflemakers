# WP Sandbox — Stage 3: Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `wpsandbox-worker` container that pops run ids from Redis, executes each run in a SecEx `wordpress` sandbox via the `e2b` SDK, and writes the report blob, dropped files and attributes back to MWDB over HTTP; plus the compose wiring and the e2e tests (fake-sandbox and live).

**Architecture:** Standalone Python package `docker/wpsandbox-worker/wpsandbox_worker/` that never imports `mwdb`. Layers: `mwdb_client.py` (thin HTTP client), `sandbox.py` (a `SandboxBackend` protocol with `E2BBackend` and `FakeBackend`), `extract.py` (pure functions: report → attributes/dropped files/hosts), `runner.py` (`process_run()` — the state machine for one run), `main.py` (BLPOP loop with N threads + backoff). Every layer is unit-tested with `responses` (HTTP) and the fake backend; the real SecEx path is covered by a live test gated on `E2B_API_KEY`.

**Tech Stack:** Python 3.12, `e2b` SDK, `redis`, `requests`; tests with `pytest`, `responses`, `fakeredis`. Docker image `python:3.12-slim`.

**Spec:** `docs/superpowers/specs/2026-08-27-wpsandbox-plugin-design.md` (§6 Worker, §7 Report schema, §8 Security, §9 Testing, §10 Deployment)

## Global Constraints

- Worker env: `E2B_API_KEY`, `E2B_API_URL`, `E2B_PROXY` (optional), `WPSANDBOX_TEMPLATE` (default `wordpress`), `WPSANDBOX_MWDB_URL` (e.g. `http://mwdb:8080/api`), `WPSANDBOX_MWDB_API_KEY`, `WPSANDBOX_REDIS_URL` (default `redis://redis/`), `WPSANDBOX_CONCURRENCY` (default `2`), `WPSANDBOX_EGRESS_BLOCKLIST` (CIDRs, default `10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,169.254.0.0/16`), `WPSANDBOX_FAKE_SANDBOX` (`0|1`), `WPSANDBOX_FAKE_REPORT` (path to canned report when fake).
- Redis list `wpsandbox:jobs` (same key as Stage 2).
- Harness invocation inside the VM is exactly: `cd /opt/harness && python3 -m harness run --mode <mode> --sample /tmp/sample [--path …] [--method …] [--query …] [--body …] --timeout <t> --blocklist <cidrs>`; partial report at `/var/log/harness/report.json`.
- Sandbox lifetime cap: `Sandbox.create(timeout=params.timeout + 120)`; `commands.run(timeout=params.timeout + 90)`; `kill()` in `finally`.
- Report blob: `blob_name="wp-sandbox-report.json"`, `blob_type="wp-sandbox-report"`, `parent=<sample sha256>`, `upload_as=<first parent group>`, then `PUT /<type>/<id>/share {"group": g}` for the remaining parent groups.
- Attributes: `c2_host` (each DNS name ∪ URL host from flows), `dropped_file` (each `filesystem.created[].sha256`), `wp_user_added` (each `database.users.added[].user_login`).
- Error policy per spec §6: MWDB HTTP error → one immediate retry then `failed`; SecEx unreachable → exponential backoff 1→60 s and `LPUSH` job back; harness crash → `failed` with last 2 KB of stderr; timeout → `timeout` with partial report if available.
- Worker MWDB user must be in a group with `access_all_objects, adding_files, adding_blobs, adding_parents, adding_all_attributes, sharing_with_all`.

---

## File map

```
docker/wpsandbox-worker/
├── pyproject.toml
├── Dockerfile
├── README.md
├── wpsandbox_worker/
│   ├── __init__.py
│   ├── config.py        # Settings dataclass from env
│   ├── mwdb_client.py   # MwdbClient: get_run, patch_run, get_file, download, get_share_groups, upload_blob, upload_file, share, add_attribute
│   ├── sandbox.py       # SandboxBackend protocol, E2BBackend, FakeBackend, RunResult, SandboxUnavailable
│   ├── extract.py       # hosts(), dropped_files(), added_users(), truncated_created_paths()
│   ├── runner.py        # process_run(run_id, client, backend, settings) -> str(status)
│   └── main.py          # loop(): BLPOP + threads + backoff
└── tests/
    ├── __init__.py
    ├── fixtures/report_sample.json
    ├── test_mwdb_client.py
    ├── test_extract.py
    ├── test_sandbox_fake.py
    ├── test_runner.py
    └── test_main.py
tests/backend/test_wpsandbox.py         # e2e with FakeBackend (stack required)
tests/backend/test_wpsandbox_live.py    # real SecEx run, skipped without E2B_API_KEY
docker-compose-dev.yml / docker-compose-prod.yml   # wpsandbox-worker service
deploy/DEPLOYMENT.md                     # worker user/group/API key setup
```

---

### Task 1: Scaffold + settings + canned report fixture

**Files:**
- Create: `docker/wpsandbox-worker/pyproject.toml`
- Create: `docker/wpsandbox-worker/wpsandbox_worker/__init__.py`
- Create: `docker/wpsandbox-worker/wpsandbox_worker/config.py`
- Create: `docker/wpsandbox-worker/tests/__init__.py`
- Create: `docker/wpsandbox-worker/tests/fixtures/report_sample.json`
- Test: `docker/wpsandbox-worker/tests/test_config.py`

**Interfaces:**
- Produces: `config.Settings` dataclass with fields `e2b_api_key, e2b_api_url, e2b_proxy, template, mwdb_url, mwdb_api_key, redis_url, concurrency, blocklist (list[str]), fake_sandbox (bool), fake_report (str|None)` and `Settings.from_env(env: Mapping = os.environ)`.
- Produces: `tests/fixtures/report_sample.json` — a valid §7 report reused by every later test and by Stage 4's frontend tests and the e2e test.

- [ ] **Step 1: pyproject + init**

`docker/wpsandbox-worker/pyproject.toml`:
```toml
[project]
name = "wpsandbox-worker"
version = "0.1.0"
description = "Executes MWDB wpsandbox runs on SecEx and reports back over HTTP"
requires-python = ">=3.12"
dependencies = [
    "e2b>=1.0",
    "redis>=4.5",
    "requests>=2.31",
]

[project.optional-dependencies]
dev = ["pytest>=7", "responses>=0.24", "fakeredis>=2.20"]

[project.scripts]
wpsandbox-worker = "wpsandbox_worker.main:cli"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["wpsandbox_worker"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`docker/wpsandbox-worker/wpsandbox_worker/__init__.py`:
```python
"""wpsandbox worker — see docs/superpowers/specs/2026-08-27-wpsandbox-plugin-design.md §6."""
import logging

logger = logging.getLogger("wpsandbox_worker")
```

- [ ] **Step 2: Write the failing test**

`docker/wpsandbox-worker/tests/test_config.py`:
```python
import pytest

from wpsandbox_worker.config import DEFAULT_BLOCKLIST, Settings


def test_from_env_defaults():
    s = Settings.from_env({"E2B_API_KEY": "k", "WPSANDBOX_MWDB_URL": "http://mwdb:8080/api",
                           "WPSANDBOX_MWDB_API_KEY": "t"})
    assert s.template == "wordpress" and s.concurrency == 2
    assert s.redis_url == "redis://redis/" and s.fake_sandbox is False
    assert s.blocklist == DEFAULT_BLOCKLIST.split(",")
    assert s.mwdb_url == "http://mwdb:8080/api"


def test_from_env_overrides_and_strips_trailing_slash():
    s = Settings.from_env({"E2B_API_KEY": "k", "WPSANDBOX_MWDB_URL": "http://x/api/",
                           "WPSANDBOX_MWDB_API_KEY": "t", "WPSANDBOX_CONCURRENCY": "4",
                           "WPSANDBOX_EGRESS_BLOCKLIST": "10.0.0.0/8, 192.168.0.0/16",
                           "WPSANDBOX_FAKE_SANDBOX": "1", "WPSANDBOX_FAKE_REPORT": "/r.json"})
    assert s.mwdb_url == "http://x/api" and s.concurrency == 4
    assert s.blocklist == ["10.0.0.0/8", "192.168.0.0/16"]
    assert s.fake_sandbox is True and s.fake_report == "/r.json"


def test_missing_required_raises():
    with pytest.raises(ValueError, match="WPSANDBOX_MWDB_API_KEY"):
        Settings.from_env({"E2B_API_KEY": "k", "WPSANDBOX_MWDB_URL": "http://x"})


def test_fake_sandbox_does_not_require_e2b_key():
    s = Settings.from_env({"WPSANDBOX_MWDB_URL": "http://x", "WPSANDBOX_MWDB_API_KEY": "t",
                           "WPSANDBOX_FAKE_SANDBOX": "1"})
    assert s.e2b_api_key is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd docker/wpsandbox-worker && python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]' && python -m pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wpsandbox_worker.config'`

- [ ] **Step 4: Implement**

`docker/wpsandbox-worker/wpsandbox_worker/config.py`:
```python
import os
from dataclasses import dataclass
from typing import Mapping

DEFAULT_BLOCKLIST = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,169.254.0.0/16"
JOBS_KEY = "wpsandbox:jobs"


@dataclass
class Settings:
    e2b_api_key: str | None
    e2b_api_url: str | None
    e2b_proxy: str | None
    template: str
    mwdb_url: str
    mwdb_api_key: str
    redis_url: str
    concurrency: int
    blocklist: list[str]
    fake_sandbox: bool
    fake_report: str | None

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        fake = env.get("WPSANDBOX_FAKE_SANDBOX", "0") == "1"
        for key in ("WPSANDBOX_MWDB_URL", "WPSANDBOX_MWDB_API_KEY"):
            if not env.get(key):
                raise ValueError(f"{key} is required")
        if not fake and not env.get("E2B_API_KEY"):
            raise ValueError("E2B_API_KEY is required unless WPSANDBOX_FAKE_SANDBOX=1")
        return cls(
            e2b_api_key=env.get("E2B_API_KEY") or None,
            e2b_api_url=env.get("E2B_API_URL") or None,
            e2b_proxy=env.get("E2B_PROXY") or None,
            template=env.get("WPSANDBOX_TEMPLATE", "wordpress"),
            mwdb_url=env["WPSANDBOX_MWDB_URL"].rstrip("/"),
            mwdb_api_key=env["WPSANDBOX_MWDB_API_KEY"],
            redis_url=env.get("WPSANDBOX_REDIS_URL", "redis://redis/"),
            concurrency=int(env.get("WPSANDBOX_CONCURRENCY", "2")),
            blocklist=[c.strip() for c in env.get("WPSANDBOX_EGRESS_BLOCKLIST", DEFAULT_BLOCKLIST).split(",") if c.strip()],
            fake_sandbox=fake,
            fake_report=env.get("WPSANDBOX_FAKE_REPORT") or None,
        )
```

- [ ] **Step 5: Canned report fixture**

`docker/wpsandbox-worker/tests/fixtures/report_sample.json`:
```json
{
  "schema_version": 1,
  "run": {"mode": "webroot", "params": {"path": "wp-content/uploads/abababab.php", "method": "GET", "query": "", "body": "", "timeout": 5},
          "template": "wordpress", "wp_version": "6.6.1", "php_version": "8.4.2",
          "started_at": "2026-08-27T10:00:00+00:00", "duration_s": 7.5},
  "trigger": {"requests": [{"url": "/wp-content/uploads/abababab.php", "method": "GET", "status": 200, "response_head": "ok", "elapsed_ms": 120, "error": null}]},
  "filesystem": {
    "created": [
      {"path": "wp-content/uploads/abababab.php", "size": 40, "mode": "0644", "sha256": "1111111111111111111111111111111111111111111111111111111111111111", "content": "<?php echo 'ok';"},
      {"path": "wp-content/uploads/.cache/s.php", "size": 300000, "mode": "0644", "sha256": "2222222222222222222222222222222222222222222222222222222222222222", "content": null}
    ],
    "modified": [{"path": "wp-config.php", "sha256_before": "aa", "sha256_after": "bb", "diff": "--- a/wp-config.php\n+++ b/wp-config.php\n@@ -1 +1 @@\n-x\n+y\n"}],
    "deleted": [],
    "events": [{"ts": "1787205600.123", "op": "create", "path": "wp-content/uploads/.cache/s.php", "pid": 321}]
  },
  "database": {
    "users": {"added": [{"ID": 7, "user_login": "wp_backup", "user_email": "x@evil.test", "roles": "administrator"}], "modified": []},
    "options": {"added": [], "modified": [{"name": "siteurl", "before": "http://127.0.0.1", "after": "http://evil.test"}]},
    "cron": {"added": []},
    "posts": {"added": [], "modified": []},
    "other_tables_changed": ["wp_usermeta"]
  },
  "network": {
    "dns": [{"ts": "Aug 27 10:00:00", "name": "evil.test", "answers": ["203.0.113.9"]}],
    "flows": [{"ts": "2026-08-27T10:00:00+00:00", "method": "POST", "url": "https://evil.test/gate.php", "status": 200,
               "request_headers": {"Host": "evil.test"}, "request_body": "hello", "response_headers": {}, "response_body": "ok",
               "response_sha256": "3333333333333333333333333333333333333333333333333333333333333333"},
              {"ts": "2026-08-27T10:00:01+00:00", "method": "GET", "url": "http://cdn.example.net:8080/p.txt", "status": 404,
               "request_headers": {}, "request_body": "", "response_headers": {}, "response_body": "",
               "response_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}]
  },
  "php": {"eval_layers": [{"depth": 1, "sha256": "44", "code": "echo 1;"}], "errors": []},
  "truncated": ["filesystem.created[1].content"]
}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd docker/wpsandbox-worker && python -m pytest tests/test_config.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add docker/wpsandbox-worker
git commit -m "wpsandbox worker: scaffold, settings, canned report fixture"
```

---

### Task 2: MWDB HTTP client

**Files:**
- Create: `docker/wpsandbox-worker/wpsandbox_worker/mwdb_client.py`
- Test: `docker/wpsandbox-worker/tests/test_mwdb_client.py`

**Interfaces:**
- Produces: `class MwdbError(Exception)` with `.status` and `.body`; `class MwdbClient(base_url, api_key, session=None)` with:
  - `get_run(run_id) -> dict`
  - `patch_run(run_id, **fields) -> dict`
  - `get_file(sha256) -> dict` (`GET /file/<sha>`; has `file_name`, `id`)
  - `download(sha256) -> bytes` (`GET /file/<sha>/download`)
  - `get_share_groups(sha256) -> list[str]` (group names from `GET /file/<sha>/share` whose `related_object_dhash == sha`, deduped, order preserved)
  - `upload_blob(parent, blob_name, blob_type, content, upload_as) -> str` (returns blob dhash from `POST /blob`)
  - `upload_file(parent, file_name, data, upload_as) -> str` (`POST /file` multipart; returns sha256)
  - `share(obj_type, identifier, group) -> None` (`PUT /<type>/<id>/share`)
  - `add_attribute(sha256, key, value) -> None` (`POST /file/<sha>/attribute`)
  - Every call retries **once** immediately on connection error or 5xx, then raises `MwdbError`.

- [ ] **Step 1: Write the failing test**

`docker/wpsandbox-worker/tests/test_mwdb_client.py`:
```python
import json

import pytest
import responses

from wpsandbox_worker.mwdb_client import MwdbClient, MwdbError

BASE = "http://mwdb/api"
SHA = "ab" * 32


@pytest.fixture
def client():
    return MwdbClient(BASE, "tok")


@responses.activate
def test_auth_header_and_get_run(client):
    responses.get(f"{BASE}/wpsandbox/run/r1", json={"id": "r1", "status": "queued"})
    assert client.get_run("r1")["status"] == "queued"
    assert responses.calls[0].request.headers["Authorization"] == "Bearer tok"


@responses.activate
def test_patch_run_sends_fields(client):
    responses.patch(f"{BASE}/wpsandbox/run/r1", json={"id": "r1", "status": "running"})
    client.patch_run("r1", status="running", sandbox_id="s")
    assert json.loads(responses.calls[0].request.body) == {"status": "running", "sandbox_id": "s"}


@responses.activate
def test_download_bytes(client):
    responses.get(f"{BASE}/file/{SHA}/download", body=b"<?php", content_type="application/octet-stream")
    assert client.download(SHA) == b"<?php"


@responses.activate
def test_get_share_groups_filters_and_dedupes(client):
    responses.get(f"{BASE}/file/{SHA}/share", json={"groups": ["a", "b"], "shares": [
        {"group_name": "alice", "related_object_dhash": SHA},
        {"group_name": "research", "related_object_dhash": SHA},
        {"group_name": "alice", "related_object_dhash": SHA},
        {"group_name": "other", "related_object_dhash": "cd" * 32},
    ]})
    assert client.get_share_groups(SHA) == ["alice", "research"]


@responses.activate
def test_upload_blob_returns_dhash(client):
    responses.post(f"{BASE}/blob", json={"id": "ef" * 32, "blob_type": "wp-sandbox-report"})
    dhash = client.upload_blob(SHA, "wp-sandbox-report.json", "wp-sandbox-report", "{}", upload_as="alice")
    assert dhash == "ef" * 32
    body = json.loads(responses.calls[0].request.body)
    assert body == {"parent": SHA, "blob_name": "wp-sandbox-report.json", "blob_type": "wp-sandbox-report",
                    "content": "{}", "upload_as": "alice"}


@responses.activate
def test_upload_file_multipart(client):
    responses.post(f"{BASE}/file", json={"id": "12" * 32, "sha256": "12" * 32})
    assert client.upload_file(SHA, "s.php", b"<?php", upload_as="alice") == "12" * 32
    req = responses.calls[0].request
    assert b'name="file"; filename="s.php"' in req.body
    assert b'name="options"' in req.body and b'"upload_as": "alice"' in req.body


@responses.activate
def test_share_and_add_attribute(client):
    responses.put(f"{BASE}/blob/{SHA}/share", json={})
    responses.post(f"{BASE}/file/{SHA}/attribute", json={"attributes": []})
    client.share("blob", SHA, "research")
    client.add_attribute(SHA, "c2_host", "evil.test")
    assert json.loads(responses.calls[0].request.body) == {"group": "research"}
    assert json.loads(responses.calls[1].request.body) == {"key": "c2_host", "value": "evil.test"}


@responses.activate
def test_retries_once_on_5xx_then_raises(client):
    responses.get(f"{BASE}/wpsandbox/run/r1", status=502, body="bad gateway")
    responses.get(f"{BASE}/wpsandbox/run/r1", status=502, body="bad gateway")
    with pytest.raises(MwdbError) as ei:
        client.get_run("r1")
    assert ei.value.status == 502 and "bad gateway" in ei.value.body
    assert len(responses.calls) == 2


@responses.activate
def test_retry_succeeds_second_time(client):
    responses.get(f"{BASE}/wpsandbox/run/r1", status=503)
    responses.get(f"{BASE}/wpsandbox/run/r1", json={"id": "r1"})
    assert client.get_run("r1") == {"id": "r1"}


@responses.activate
def test_4xx_not_retried(client):
    responses.get(f"{BASE}/wpsandbox/run/r1", status=404, body="nope")
    with pytest.raises(MwdbError):
        client.get_run("r1")
    assert len(responses.calls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd docker/wpsandbox-worker && python -m pytest tests/test_mwdb_client.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`docker/wpsandbox-worker/wpsandbox_worker/mwdb_client.py`:
```python
"""Minimal MWDB REST client used by the worker. One immediate retry on
connection errors / 5xx, then MwdbError."""
import json

import requests


class MwdbError(Exception):
    def __init__(self, status: int | None, body: str):
        super().__init__(f"MWDB HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


class MwdbClient:
    def __init__(self, base_url: str, api_key: str, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers["Authorization"] = f"Bearer {api_key}"

    def _request(self, method: str, path: str, **kw) -> requests.Response:
        url = f"{self.base_url}{path}"
        last: Exception | None = None
        for attempt in range(2):
            try:
                resp = self.session.request(method, url, timeout=60, **kw)
            except requests.RequestException as e:
                last = MwdbError(None, str(e))
                continue
            if resp.status_code >= 500:
                last = MwdbError(resp.status_code, resp.text)
                continue
            if resp.status_code >= 400:
                raise MwdbError(resp.status_code, resp.text)
            return resp
        assert last is not None
        raise last

    # --- runs
    def get_run(self, run_id: str) -> dict:
        return self._request("GET", f"/wpsandbox/run/{run_id}").json()

    def patch_run(self, run_id: str, **fields) -> dict:
        return self._request("PATCH", f"/wpsandbox/run/{run_id}", json=fields).json()

    # --- sample
    def get_file(self, sha256: str) -> dict:
        return self._request("GET", f"/file/{sha256}").json()

    def download(self, sha256: str) -> bytes:
        return self._request("GET", f"/file/{sha256}/download").content

    def get_share_groups(self, sha256: str) -> list[str]:
        data = self._request("GET", f"/file/{sha256}/share").json()
        out: list[str] = []
        for s in data.get("shares", []):
            if s.get("related_object_dhash") == sha256 and s["group_name"] not in out:
                out.append(s["group_name"])
        return out

    # --- artefacts
    def upload_blob(self, parent: str, blob_name: str, blob_type: str, content: str,
                    upload_as: str) -> str:
        body = {"parent": parent, "blob_name": blob_name, "blob_type": blob_type,
                "content": content, "upload_as": upload_as}
        return self._request("POST", "/blob", json=body).json()["id"]

    def upload_file(self, parent: str, file_name: str, data: bytes, upload_as: str) -> str:
        files = {
            "file": (file_name, data),
            "options": (None, json.dumps({"parent": parent, "upload_as": upload_as})),
        }
        return self._request("POST", "/file", files=files).json()["id"]

    def share(self, obj_type: str, identifier: str, group: str) -> None:
        self._request("PUT", f"/{obj_type}/{identifier}/share", json={"group": group})

    def add_attribute(self, sha256: str, key: str, value: str) -> None:
        self._request("POST", f"/file/{sha256}/attribute", json={"key": key, "value": value})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd docker/wpsandbox-worker && python -m pytest tests/test_mwdb_client.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add docker/wpsandbox-worker/wpsandbox_worker/mwdb_client.py docker/wpsandbox-worker/tests/test_mwdb_client.py
git commit -m "wpsandbox worker: MWDB HTTP client with single retry"
```

---

### Task 3: Report extraction helpers

**Files:**
- Create: `docker/wpsandbox-worker/wpsandbox_worker/extract.py`
- Test: `docker/wpsandbox-worker/tests/test_extract.py`

**Interfaces:**
- Produces: `hosts(report) -> list[str]` (DNS names ∪ URL hosts without port, deduped, sorted), `dropped_files(report) -> list[str]` (sha256s of `filesystem.created`, deduped, order preserved), `added_users(report) -> list[str]`, `truncated_created_paths(report) -> list[str]` (created entries with `content is None`, i.e. needing full upload), `attributes(report) -> list[tuple[str, str]]` combining the first three as `(key, value)` pairs.

- [ ] **Step 1: Write the failing test**

`docker/wpsandbox-worker/tests/test_extract.py`:
```python
import json
from pathlib import Path

from wpsandbox_worker import extract

REPORT = json.loads((Path(__file__).parent / "fixtures" / "report_sample.json").read_text())


def test_hosts_from_dns_and_flows_without_ports():
    assert extract.hosts(REPORT) == ["cdn.example.net", "evil.test"]


def test_dropped_files():
    assert extract.dropped_files(REPORT) == ["1" * 64, "2" * 64]


def test_added_users():
    assert extract.added_users(REPORT) == ["wp_backup"]


def test_truncated_created_paths():
    assert extract.truncated_created_paths(REPORT) == ["wp-content/uploads/.cache/s.php"]


def test_attributes_pairs():
    pairs = extract.attributes(REPORT)
    assert ("c2_host", "evil.test") in pairs
    assert ("dropped_file", "1" * 64) in pairs
    assert ("wp_user_added", "wp_backup") in pairs
    assert len(pairs) == 2 + 2 + 1


def test_empty_report_sections_tolerated():
    assert extract.attributes({"network": {}, "filesystem": {}, "database": {}}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd docker/wpsandbox-worker && python -m pytest tests/test_extract.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`docker/wpsandbox-worker/wpsandbox_worker/extract.py`:
```python
"""Pure helpers that derive MWDB attributes from a report document."""
from urllib.parse import urlsplit


def hosts(report: dict) -> list[str]:
    net = report.get("network") or {}
    out: set[str] = set()
    for q in net.get("dns") or []:
        if q.get("name"):
            out.add(q["name"].rstrip("."))
    for f in net.get("flows") or []:
        host = urlsplit(f.get("url") or "").hostname
        if host:
            out.add(host)
    return sorted(out)


def dropped_files(report: dict) -> list[str]:
    seen: list[str] = []
    for c in (report.get("filesystem") or {}).get("created") or []:
        if c.get("sha256") and c["sha256"] not in seen:
            seen.append(c["sha256"])
    return seen


def added_users(report: dict) -> list[str]:
    users = ((report.get("database") or {}).get("users") or {}).get("added") or []
    return [u["user_login"] for u in users if u.get("user_login")]


def truncated_created_paths(report: dict) -> list[str]:
    return [c["path"] for c in (report.get("filesystem") or {}).get("created") or []
            if c.get("content") is None]


def attributes(report: dict) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    pairs += [("c2_host", h) for h in hosts(report)]
    pairs += [("dropped_file", s) for s in dropped_files(report)]
    pairs += [("wp_user_added", u) for u in added_users(report)]
    return pairs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd docker/wpsandbox-worker && python -m pytest tests/test_extract.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add docker/wpsandbox-worker/wpsandbox_worker/extract.py docker/wpsandbox-worker/tests/test_extract.py
git commit -m "wpsandbox worker: attribute extraction from reports"
```

---

### Task 4: Sandbox backends (E2B + Fake)

**Files:**
- Create: `docker/wpsandbox-worker/wpsandbox_worker/sandbox.py`
- Test: `docker/wpsandbox-worker/tests/test_sandbox_fake.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class RunResult:
      report: dict | None      # parsed final or partial report
      stdout: str
      stderr: str
      exit_code: int
      timed_out: bool
      sandbox_id: str
      files: dict[str, bytes]  # full contents of requested truncated files (path -> bytes)

  class SandboxUnavailable(Exception): ...   # SecEx unreachable / no capacity

  class SandboxBackend(Protocol):
      def execute(self, sample: bytes, mode: str, params: dict, blocklist: list[str],
                  on_started: Callable[[str], None]) -> RunResult: ...
  ```
  `on_started(sandbox_id)` is called as soon as the sandbox exists so the runner can record the id before the (long) harness run.
- `E2BBackend(settings)` — uses `e2b.Sandbox`; `FakeBackend(report: dict, *, files=None, timed_out=False, stderr="", exit_code=0, unavailable=False)` — returns canned results, records the last call in `.calls`.
- `build_harness_cmd(mode, params, blocklist) -> str` (shell-quoted, exact spec invocation).

- [ ] **Step 1: Write the failing test**

`docker/wpsandbox-worker/tests/test_sandbox_fake.py`:
```python
import json
import shlex
from pathlib import Path

import pytest

from wpsandbox_worker.sandbox import FakeBackend, SandboxUnavailable, build_harness_cmd

REPORT = json.loads((Path(__file__).parent / "fixtures" / "report_sample.json").read_text())


def test_build_harness_cmd_webroot():
    cmd = build_harness_cmd("webroot", {"path": "wp-content/uploads/x.php", "method": "POST",
                                        "query": "a=1&b=2", "body": "c=d", "timeout": 30},
                            ["10.0.0.0/8", "192.168.0.0/16"])
    assert cmd.startswith("cd /opt/harness && python3 -m harness run --mode webroot --sample /tmp/sample ")
    argv = shlex.split(cmd.split("&&", 1)[1])
    assert argv[argv.index("--path") + 1] == "wp-content/uploads/x.php"
    assert argv[argv.index("--method") + 1] == "POST"
    assert argv[argv.index("--query") + 1] == "a=1&b=2"
    assert argv[argv.index("--body") + 1] == "c=d"
    assert argv[argv.index("--timeout") + 1] == "30"
    assert argv[argv.index("--blocklist") + 1] == "10.0.0.0/8,192.168.0.0/16"


def test_build_harness_cmd_plugin_omits_webroot_flags():
    cmd = build_harness_cmd("plugin", {"timeout": 10}, [])
    assert "--path" not in cmd and "--method" not in cmd
    assert "--mode plugin" in cmd and "--timeout 10" in cmd and "--blocklist" not in cmd


def test_build_harness_cmd_quotes_dangerous_values():
    cmd = build_harness_cmd("webroot", {"path": "x.php", "method": "GET", "query": "", "body": "$(rm -rf /); 'q'", "timeout": 1}, [])
    assert "$(rm" not in cmd.replace(shlex.quote("$(rm -rf /); 'q'"), "")


def test_fake_backend_returns_report_and_calls_on_started():
    fb = FakeBackend(REPORT, files={"wp-content/uploads/.cache/s.php": b"<?php big"})
    started = []
    res = fb.execute(b"<?php", "webroot", {"timeout": 1}, [], on_started=started.append)
    assert started == ["fake-sandbox-1"]
    assert res.report == REPORT and res.exit_code == 0 and not res.timed_out
    assert res.files["wp-content/uploads/.cache/s.php"] == b"<?php big"
    assert fb.calls[0]["mode"] == "webroot" and fb.calls[0]["sample"] == b"<?php"


def test_fake_backend_unavailable():
    fb = FakeBackend(REPORT, unavailable=True)
    with pytest.raises(SandboxUnavailable):
        fb.execute(b"x", "webroot", {}, [], on_started=lambda _: None)


def test_fake_backend_timeout_partial_report():
    fb = FakeBackend(REPORT, timed_out=True, exit_code=124, stderr="killed")
    res = fb.execute(b"x", "webroot", {}, [], on_started=lambda _: None)
    assert res.timed_out and res.report == REPORT and res.stderr == "killed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd docker/wpsandbox-worker && python -m pytest tests/test_sandbox_fake.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`docker/wpsandbox-worker/wpsandbox_worker/sandbox.py`:
```python
"""Sandbox backends. E2BBackend drives SecEx; FakeBackend is for tests/e2e."""
import json
import shlex
from dataclasses import dataclass, field
from typing import Callable, Protocol

from . import logger
from .config import Settings

SAMPLE_PATH = "/tmp/sample"
PARTIAL_REPORT_PATH = "/var/log/harness/report.json"
BOOT_GRACE_S = 120
RUN_GRACE_S = 90


class SandboxUnavailable(Exception):
    """SecEx API unreachable, unauthenticated, or out of capacity."""


@dataclass
class RunResult:
    report: dict | None
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    sandbox_id: str
    files: dict[str, bytes] = field(default_factory=dict)


class SandboxBackend(Protocol):
    def execute(self, sample: bytes, mode: str, params: dict, blocklist: list[str],
                on_started: Callable[[str], None]) -> RunResult: ...


def build_harness_cmd(mode: str, params: dict, blocklist: list[str]) -> str:
    argv = ["python3", "-m", "harness", "run", "--mode", mode, "--sample", SAMPLE_PATH]
    if mode == "webroot":
        argv += ["--path", params.get("path", ""), "--method", params.get("method", "GET")]
        if params.get("query"):
            argv += ["--query", params["query"]]
        if params.get("body"):
            argv += ["--body", params["body"]]
    argv += ["--timeout", str(int(params.get("timeout", 120)))]
    if blocklist:
        argv += ["--blocklist", ",".join(blocklist)]
    return "cd /opt/harness && " + " ".join(shlex.quote(a) for a in argv)


def _parse_report(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


class E2BBackend:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _create(self, timeout: int):
        from e2b import Sandbox
        from e2b.exceptions import AuthenticationException, SandboxException

        kwargs = {"template": self.settings.template, "timeout": timeout}
        if self.settings.e2b_api_key:
            kwargs["api_key"] = self.settings.e2b_api_key
        if self.settings.e2b_api_url:
            kwargs["api_url"] = self.settings.e2b_api_url
        if self.settings.e2b_proxy:
            kwargs["proxy"] = self.settings.e2b_proxy
        try:
            return Sandbox.create(**kwargs)
        except (AuthenticationException, SandboxException, OSError) as e:
            raise SandboxUnavailable(f"{type(e).__name__}: {e}") from e

    def execute(self, sample, mode, params, blocklist, on_started):
        from e2b.exceptions import TimeoutException

        run_timeout = int(params.get("timeout", 120))
        sb = self._create(timeout=run_timeout + BOOT_GRACE_S)
        on_started(sb.sandbox_id)
        try:
            sb.files.write(SAMPLE_PATH, sample)
            cmd = build_harness_cmd(mode, params, blocklist)
            timed_out = False
            stdout = stderr = ""
            exit_code = 0
            try:
                res = sb.commands.run(cmd, timeout=run_timeout + RUN_GRACE_S)
                stdout, stderr, exit_code = res.stdout, res.stderr, res.exit_code
            except TimeoutException as e:
                timed_out = True
                stderr = f"[timeout] {e}"
                exit_code = 124
            except Exception as e:  # e2b CommandExitException carries stdout/stderr
                stdout = getattr(e, "stdout", "") or ""
                stderr = getattr(e, "stderr", "") or str(e)
                exit_code = getattr(e, "exit_code", 1) or 1

            report = _parse_report(stdout)
            if report is None:
                try:
                    report = _parse_report(sb.files.read(PARTIAL_REPORT_PATH))
                except Exception:
                    report = None

            files: dict[str, bytes] = {}
            if report:
                for c in (report.get("filesystem") or {}).get("created") or []:
                    if c.get("content") is None and c.get("path"):
                        try:
                            data = sb.files.read(f"/var/www/html/{c['path']}", format="bytes")
                            files[c["path"]] = bytes(data)
                        except Exception as e:
                            logger.warning("could not fetch dropped file %s: %s", c["path"], e)
            return RunResult(report, stdout, stderr, exit_code, timed_out, sb.sandbox_id, files)
        finally:
            try:
                sb.kill()
            except Exception as e:
                logger.warning("sandbox kill failed for %s: %s", sb.sandbox_id, e)


class FakeBackend:
    """Canned backend for unit tests and `WPSANDBOX_FAKE_SANDBOX=1` e2e runs."""

    def __init__(self, report: dict | None, *, files=None, timed_out=False, stderr="",
                 exit_code=0, unavailable=False):
        self.report = report
        self.files = files or {}
        self.timed_out = timed_out
        self.stderr = stderr
        self.exit_code = exit_code
        self.unavailable = unavailable
        self.calls: list[dict] = []
        self._n = 0

    def execute(self, sample, mode, params, blocklist, on_started):
        if self.unavailable:
            raise SandboxUnavailable("fake: unavailable")
        self._n += 1
        sid = f"fake-sandbox-{self._n}"
        self.calls.append({"sample": sample, "mode": mode, "params": params,
                           "blocklist": blocklist, "sandbox_id": sid})
        on_started(sid)
        stdout = json.dumps(self.report) if (self.report and not self.timed_out) else ""
        return RunResult(self.report, stdout, self.stderr, self.exit_code,
                         self.timed_out, sid, dict(self.files))
```

If the installed `e2b` version exposes different exception names (`e2b.exceptions` module contents vary between 1.x releases), adjust the imports — the contract is: authentication/connection/capacity errors → `SandboxUnavailable`; command timeout → `timed_out=True`; non-zero exit → captured stdout/stderr.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd docker/wpsandbox-worker && python -m pytest tests/test_sandbox_fake.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add docker/wpsandbox-worker/wpsandbox_worker/sandbox.py docker/wpsandbox-worker/tests/test_sandbox_fake.py
git commit -m "wpsandbox worker: E2B and fake sandbox backends"
```

---

### Task 5: Runner — one run's state machine

**Files:**
- Create: `docker/wpsandbox-worker/wpsandbox_worker/runner.py`
- Test: `docker/wpsandbox-worker/tests/test_runner.py`

**Interfaces:**
- Produces: `process_run(run_id: str, client: MwdbClient, backend: SandboxBackend, settings: Settings) -> str` returning the final status (`done|failed|timeout|skipped|requeue`). `requeue` means the caller must `LPUSH` the id back (SecEx unavailable). Never raises for MWDB/sandbox errors — they become `failed`.
- Consumes: `MwdbClient` (Task 2), `SandboxBackend`/`RunResult`/`SandboxUnavailable` (Task 4), `extract` (Task 3).

- [ ] **Step 1: Write the failing test**

`docker/wpsandbox-worker/tests/test_runner.py`:
```python
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from wpsandbox_worker.config import Settings
from wpsandbox_worker.mwdb_client import MwdbError
from wpsandbox_worker.runner import process_run
from wpsandbox_worker.sandbox import FakeBackend

REPORT = json.loads((Path(__file__).parent / "fixtures" / "report_sample.json").read_text())
SHA = "ab" * 32
BLOB = "ef" * 32


@pytest.fixture
def settings():
    return Settings.from_env({"WPSANDBOX_MWDB_URL": "http://m/api", "WPSANDBOX_MWDB_API_KEY": "t",
                              "WPSANDBOX_FAKE_SANDBOX": "1", "WPSANDBOX_EGRESS_BLOCKLIST": "10.0.0.0/8"})


@pytest.fixture
def client():
    c = MagicMock()
    c.get_run.return_value = {"id": "r1", "status": "queued", "object_id": 11, "mode": "webroot",
                              "params": {"path": "x.php", "method": "GET", "query": "", "body": "", "timeout": 5},
                              "sample_sha256": SHA}
    c.get_file.return_value = {"id": SHA, "file_name": "x.php"}
    c.download.return_value = b"<?php evil();"
    c.get_share_groups.return_value = ["alice", "research", "wpsandbox-worker"]
    c.upload_blob.return_value = BLOB
    c.upload_file.return_value = "12" * 32
    return c


def _patches(client):
    return [call.kwargs for call in client.patch_run.call_args_list]


def test_happy_path(client, settings):
    backend = FakeBackend(REPORT, files={"wp-content/uploads/.cache/s.php": b"<?php big"})
    assert process_run("r1", client, backend, settings) == "done"

    # running -> done with sandbox id and blob
    p = _patches(client)
    assert p[0]["status"] == "running" and "started_at" in p[0]
    assert p[1] == {"sandbox_id": "fake-sandbox-1"}
    assert p[-1]["status"] == "done" and p[-1]["report_blob_id"] == BLOB and "finished_at" in p[-1]

    # sandbox got the sample and the blocklist
    assert backend.calls[0]["sample"] == b"<?php evil();" and backend.calls[0]["blocklist"] == ["10.0.0.0/8"]

    # report blob uploaded as first group, shared with the rest (worker's own group skipped)
    client.upload_blob.assert_called_once()
    kw = client.upload_blob.call_args.kwargs
    assert kw["parent"] == SHA and kw["blob_type"] == "wp-sandbox-report" and kw["upload_as"] == "alice"
    assert json.loads(kw["content"])["schema_version"] == 1
    client.share.assert_any_call("blob", BLOB, "research")
    assert not any(c.args[2] == "wpsandbox-worker" for c in client.share.call_args_list)

    # truncated dropped file uploaded as child File and shared
    client.upload_file.assert_called_once()
    fkw = client.upload_file.call_args.kwargs
    assert fkw["parent"] == SHA and fkw["file_name"] == "s.php" and fkw["data"] == b"<?php big"
    client.share.assert_any_call("file", "12" * 32, "research")

    # attributes
    keys = {(c.args[1], c.args[2]) for c in client.add_attribute.call_args_list}
    assert ("c2_host", "evil.test") in keys and ("dropped_file", "1" * 64) in keys
    assert ("wp_user_added", "wp_backup") in keys


def test_skips_non_queued(client, settings):
    client.get_run.return_value["status"] = "done"
    assert process_run("r1", client, FakeBackend(REPORT), settings) == "skipped"
    client.patch_run.assert_not_called()


def test_sandbox_unavailable_requeues_without_marking_running(client, settings):
    assert process_run("r1", client, FakeBackend(REPORT, unavailable=True), settings) == "requeue"
    statuses = [p.get("status") for p in _patches(client)]
    assert "running" in statuses and statuses[-1] == "queued"


def test_timeout_with_partial_report(client, settings):
    backend = FakeBackend(REPORT, timed_out=True, exit_code=124, stderr="killed")
    assert process_run("r1", client, backend, settings) == "timeout"
    last = _patches(client)[-1]
    assert last["status"] == "timeout" and last["report_blob_id"] == BLOB
    assert "killed" in last["error"]


def test_harness_crash_without_report_fails_with_stderr_tail(client, settings):
    backend = FakeBackend(None, exit_code=1, stderr="x" * 5000 + "TRACEBACK END")
    assert process_run("r1", client, backend, settings) == "failed"
    last = _patches(client)[-1]
    assert last["status"] == "failed" and last["error"].endswith("TRACEBACK END")
    assert len(last["error"]) <= 2048
    client.upload_blob.assert_not_called()


def test_mwdb_error_during_upload_marks_failed(client, settings):
    client.upload_blob.side_effect = MwdbError(500, "boom")
    assert process_run("r1", client, FakeBackend(REPORT), settings) == "failed"
    last = _patches(client)[-1]
    assert last["status"] == "failed" and "500" in last["error"]


def test_no_parent_groups_falls_back_to_star(client, settings):
    client.get_share_groups.return_value = []
    process_run("r1", client, FakeBackend(REPORT), settings)
    assert client.upload_blob.call_args.kwargs["upload_as"] == "*"


def test_attribute_failure_does_not_fail_run(client, settings):
    client.add_attribute.side_effect = MwdbError(404, "not defined")
    assert process_run("r1", client, FakeBackend(REPORT), settings) == "done"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd docker/wpsandbox-worker && python -m pytest tests/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`docker/wpsandbox-worker/wpsandbox_worker/runner.py`:
```python
"""process_run(): the lifecycle of a single run (spec §6 steps 2–7)."""
import json
import posixpath
from datetime import datetime, timezone

from . import extract, logger
from .config import Settings
from .mwdb_client import MwdbClient, MwdbError
from .sandbox import RunResult, SandboxBackend, SandboxUnavailable

REPORT_BLOB_NAME = "wp-sandbox-report.json"
REPORT_BLOB_TYPE = "wp-sandbox-report"
ERROR_TAIL = 2048


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tail(text: str) -> str:
    return (text or "")[-ERROR_TAIL:]


def _sample_sha(run: dict, client: MwdbClient) -> str:
    # Stage 2's run JSON always carries the parent sample's sha256.
    if run.get("sample_sha256"):
        return run["sample_sha256"]
    raise MwdbError(None, f"run {run.get('id')} has no sample_sha256")


def _share_targets(client: MwdbClient, sha: str, own_login: str | None) -> list[str]:
    groups = [g for g in client.get_share_groups(sha) if g != own_login]
    return groups


def _publish(client: MwdbClient, sha: str, report: dict, files: dict[str, bytes],
             groups: list[str]) -> str:
    upload_as = groups[0] if groups else "*"
    rest = groups[1:]
    blob_id = client.upload_blob(parent=sha, blob_name=REPORT_BLOB_NAME,
                                 blob_type=REPORT_BLOB_TYPE,
                                 content=json.dumps(report, ensure_ascii=False),
                                 upload_as=upload_as)
    for g in rest:
        client.share("blob", blob_id, g)
    for path, data in files.items():
        fid = client.upload_file(parent=sha, file_name=posixpath.basename(path) or "dropped",
                                 data=data, upload_as=upload_as)
        for g in rest:
            client.share("file", fid, g)
    for key, value in extract.attributes(report):
        try:
            client.add_attribute(sha, key, value)
        except MwdbError as e:
            logger.warning("attribute %s=%s not set: %s", key, value, e)
    return blob_id


def process_run(run_id: str, client: MwdbClient, backend: SandboxBackend,
                settings: Settings) -> str:
    log = lambda phase, **kw: logger.info(json.dumps({"run_id": run_id, "phase": phase, **kw}))
    try:
        run = client.get_run(run_id)
    except MwdbError as e:
        log("get_run_failed", error=str(e))
        return "failed"
    if run.get("status") != "queued":
        log("skipped", status=run.get("status"))
        return "skipped"

    try:
        sha = _sample_sha(run, client)
        sample = client.download(sha)
        client.patch_run(run_id, status="running", started_at=_now())
    except MwdbError as e:
        log("prepare_failed", error=str(e))
        try:
            client.patch_run(run_id, status="failed", finished_at=_now(), error=str(e))
        except MwdbError:
            pass
        return "failed"

    def on_started(sandbox_id: str):
        try:
            client.patch_run(run_id, sandbox_id=sandbox_id)
        except MwdbError as e:
            logger.warning("could not record sandbox id: %s", e)

    try:
        result: RunResult = backend.execute(sample, run["mode"], run.get("params") or {},
                                            settings.blocklist, on_started)
    except SandboxUnavailable as e:
        log("sandbox_unavailable", error=str(e))
        try:
            client.patch_run(run_id, status="queued", started_at=None, error=None)
        except MwdbError:
            pass
        return "requeue"
    except Exception as e:  # unexpected backend failure
        log("sandbox_error", error=repr(e))
        try:
            client.patch_run(run_id, status="failed", finished_at=_now(), error=_tail(repr(e)))
        except MwdbError:
            pass
        return "failed"

    status = "timeout" if result.timed_out else ("done" if result.report and result.exit_code == 0 else "failed")
    error = None
    if status != "done":
        error = _tail(result.stderr or f"harness exited with {result.exit_code}")

    blob_id = None
    if result.report:
        try:
            groups = _share_targets(client, sha, settings_worker_login(settings))
            blob_id = _publish(client, sha, result.report, result.files, groups)
        except MwdbError as e:
            status, error = "failed", f"publish failed: {e}"

    try:
        client.patch_run(run_id, status=status, finished_at=_now(), error=error,
                         report_blob_id=blob_id)
    except MwdbError as e:
        log("final_patch_failed", error=str(e))
        return "failed"
    log("finished", status=status, blob=blob_id, sandbox_id=result.sandbox_id)
    return status


def settings_worker_login(settings: Settings) -> str | None:
    """The worker's own private group is named after its login; exclude it
    from share targets. Configured via WPSANDBOX_WORKER_LOGIN (optional)."""
    import os
    return os.environ.get("WPSANDBOX_WORKER_LOGIN", "wpsandbox-worker")
```

The run JSON includes `sample_sha256` — Stage 2's `_run_json()` adds it to every run response.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd docker/wpsandbox-worker && python -m pytest tests/test_runner.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add docker/wpsandbox-worker/wpsandbox_worker/runner.py docker/wpsandbox-worker/tests/test_runner.py docker/plugins/wpsandbox
git commit -m "wpsandbox worker: run lifecycle (download, execute, publish, attributes)"
```

---

### Task 6: Main loop, Dockerfile, compose, README

**Files:**
- Create: `docker/wpsandbox-worker/wpsandbox_worker/main.py`
- Create: `docker/wpsandbox-worker/Dockerfile`
- Create: `docker/wpsandbox-worker/README.md`
- Modify: `docker-compose-dev.yml`, `docker-compose-prod.yml` (add `wpsandbox-worker` service)
- Test: `docker/wpsandbox-worker/tests/test_main.py`

**Interfaces:**
- Produces: `main.Worker(settings, client, backend, redis_client)` with `.run_once(block_seconds=1) -> str | None` (BLPOP one id, `process_run`, requeue/backoff handling; returns the status or `None` when idle) and `.loop(stop: threading.Event)`; `main.cli()` entrypoint that builds `Settings.from_env()`, `MwdbClient`, backend (`FakeBackend` loading `WPSANDBOX_FAKE_REPORT` when `fake_sandbox`), starts `concurrency` threads.
- Backoff: after a `requeue`, sleep `min(60, 2**n)` seconds (n = consecutive requeues), reset on any other outcome.

- [ ] **Step 1: Write the failing test**

`docker/wpsandbox-worker/tests/test_main.py`:
```python
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import fakeredis

from wpsandbox_worker.config import JOBS_KEY, Settings
from wpsandbox_worker.main import Worker

REPORT = json.loads((Path(__file__).parent / "fixtures" / "report_sample.json").read_text())


def _settings():
    return Settings.from_env({"WPSANDBOX_MWDB_URL": "http://m/api", "WPSANDBOX_MWDB_API_KEY": "t",
                              "WPSANDBOX_FAKE_SANDBOX": "1"})


def test_run_once_idle_returns_none():
    r = fakeredis.FakeRedis()
    w = Worker(_settings(), MagicMock(), MagicMock(), r)
    assert w.run_once(block_seconds=0) is None


def test_run_once_processes_job():
    r = fakeredis.FakeRedis(); r.rpush(JOBS_KEY, "r1")
    w = Worker(_settings(), MagicMock(), MagicMock(), r)
    with patch("wpsandbox_worker.main.process_run", return_value="done") as pr:
        assert w.run_once(block_seconds=0) == "done"
    pr.assert_called_once()
    assert pr.call_args.args[0] == "r1"
    assert r.llen(JOBS_KEY) == 0


def test_requeue_pushes_to_head_and_backs_off():
    r = fakeredis.FakeRedis(); r.rpush(JOBS_KEY, "r1"); r.rpush(JOBS_KEY, "r2")
    w = Worker(_settings(), MagicMock(), MagicMock(), r)
    sleeps = []
    w._sleep = sleeps.append
    with patch("wpsandbox_worker.main.process_run", return_value="requeue"):
        w.run_once(block_seconds=0)
        w.run_once(block_seconds=0)
    assert r.lrange(JOBS_KEY, 0, -1) == [b"r1", b"r2"]
    assert sleeps == [2, 4]
    with patch("wpsandbox_worker.main.process_run", return_value="done"):
        w.run_once(block_seconds=0)
    assert w._backoff_n == 0


def test_backoff_capped_at_60():
    r = fakeredis.FakeRedis()
    w = Worker(_settings(), MagicMock(), MagicMock(), r)
    w._backoff_n = 10
    r.rpush(JOBS_KEY, "r1")
    sleeps = []
    w._sleep = sleeps.append
    with patch("wpsandbox_worker.main.process_run", return_value="requeue"):
        w.run_once(block_seconds=0)
    assert sleeps == [60]


def test_process_run_exception_does_not_kill_loop():
    r = fakeredis.FakeRedis(); r.rpush(JOBS_KEY, "r1")
    w = Worker(_settings(), MagicMock(), MagicMock(), r)
    with patch("wpsandbox_worker.main.process_run", side_effect=RuntimeError("boom")):
        assert w.run_once(block_seconds=0) == "failed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd docker/wpsandbox-worker && python -m pytest tests/test_main.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement main**

`docker/wpsandbox-worker/wpsandbox_worker/main.py`:
```python
"""BLPOP loop with N worker threads."""
import json
import logging
import signal
import sys
import threading
import time
from pathlib import Path

import redis

from . import logger
from .config import JOBS_KEY, Settings
from .mwdb_client import MwdbClient
from .runner import process_run
from .sandbox import E2BBackend, FakeBackend, SandboxBackend

MAX_BACKOFF = 60


class Worker:
    def __init__(self, settings: Settings, client: MwdbClient, backend: SandboxBackend,
                 redis_client: redis.Redis):
        self.settings = settings
        self.client = client
        self.backend = backend
        self.redis = redis_client
        self._backoff_n = 0
        self._sleep = time.sleep

    def run_once(self, block_seconds: int = 5) -> str | None:
        item = self.redis.blpop(JOBS_KEY, timeout=block_seconds) if block_seconds else self.redis.lpop(JOBS_KEY)
        if item is None:
            return None
        run_id = (item[1] if isinstance(item, tuple) else item).decode()
        try:
            status = process_run(run_id, self.client, self.backend, self.settings)
        except Exception as e:  # never let one job kill the loop
            logger.exception("unhandled error processing %s: %s", run_id, e)
            status = "failed"
        if status == "requeue":
            self.redis.lpush(JOBS_KEY, run_id)
            self._backoff_n += 1
            self._sleep(min(MAX_BACKOFF, 2 ** self._backoff_n))
        else:
            self._backoff_n = 0
        return status

    def loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.run_once()
            except redis.RedisError as e:
                logger.error("redis error: %s", e)
                self._sleep(5)


def build_backend(settings: Settings) -> SandboxBackend:
    if settings.fake_sandbox:
        report = None
        if settings.fake_report:
            report = json.loads(Path(settings.fake_report).read_text())
        logger.warning("FAKE sandbox backend active (WPSANDBOX_FAKE_SANDBOX=1)")
        return FakeBackend(report)
    return E2BBackend(settings)


def cli() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format='{"ts":"%(asctime)s","level":"%(levelname)s","msg":%(message)s}')
    settings = Settings.from_env()
    client = MwdbClient(settings.mwdb_url, settings.mwdb_api_key)
    backend = build_backend(settings)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    threads = []
    for i in range(settings.concurrency):
        w = Worker(settings, client, backend, redis.Redis.from_url(settings.redis_url))
        t = threading.Thread(target=w.loop, args=(stop,), name=f"wpsandbox-{i}", daemon=True)
        t.start()
        threads.append(t)
    logger.info(json.dumps({"phase": "started", "threads": settings.concurrency,
                            "template": settings.template, "fake": settings.fake_sandbox}))
    while not stop.is_set():
        time.sleep(1)
    for t in threads:
        t.join(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(cli())
```

Note the `logging` format wraps `%(message)s` unquoted because `process_run` already logs JSON objects; plain-string log calls (warnings) will produce a non-JSON `msg` value — acceptable for v1.

- [ ] **Step 4: Run tests**

Run: `cd docker/wpsandbox-worker && python -m pytest tests/ -v`
Expected: all pass

- [ ] **Step 5: Dockerfile, compose, README**

`docker/wpsandbox-worker/Dockerfile`:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY wpsandbox_worker ./wpsandbox_worker
RUN pip install --no-cache-dir .
CMD ["wpsandbox-worker"]
```

`docker-compose-dev.yml` — add after the `phpdeobf` service, and add `MWDB_WPSANDBOX_WORKER_LOGIN: "wpsandbox-worker"` to the `mwdb` service environment:
```yaml
  wpsandbox-worker:
    build:
      context: ./docker/wpsandbox-worker
    depends_on:
      - mwdb
      - redis
    restart: unless-stopped
    environment:
      WPSANDBOX_MWDB_URL: "http://mwdb:8080/api"
      WPSANDBOX_MWDB_API_KEY: "${WPSANDBOX_MWDB_API_KEY:-}"
      WPSANDBOX_REDIS_URL: "redis://redis/"
      WPSANDBOX_TEMPLATE: "wordpress"
      WPSANDBOX_CONCURRENCY: "2"
      # Dev default: fake sandbox with the canned report. Set to 0 + E2B_* for real runs.
      WPSANDBOX_FAKE_SANDBOX: "${WPSANDBOX_FAKE_SANDBOX:-1}"
      WPSANDBOX_FAKE_REPORT: "/app/tests/fixtures/report_sample.json"
      E2B_API_KEY: "${E2B_API_KEY:-}"
      E2B_API_URL: "${E2B_API_URL:-}"
      E2B_PROXY: "${E2B_PROXY:-}"
    volumes:
      - "./docker/wpsandbox-worker/tests/fixtures:/app/tests/fixtures:ro"
```

`docker-compose-prod.yml` — add after `phpdeobf` (and `- MWDB_WPSANDBOX_WORKER_LOGIN=wpsandbox-worker` to `mwdb`):
```yaml
  wpsandbox-worker:
    build:
      context: ./docker/wpsandbox-worker
    depends_on:
      - mwdb
      - redis
    restart: unless-stopped
    env_file:
      - wpsandbox-worker.env
    environment:
      - WPSANDBOX_MWDB_URL=http://mwdb:8080/api
      - WPSANDBOX_REDIS_URL=redis://redis/
```
and add `wpsandbox-worker.env` to `.gitignore`.

`docker/wpsandbox-worker/README.md`:
```markdown
# wpsandbox-worker

Consumes run ids from Redis (`wpsandbox:jobs`), executes each in a SecEx `wordpress` sandbox and
publishes the report to MWDB over HTTP. Never imports `mwdb`; needs only Redis, the MWDB API and SecEx —
so it can run beside MWDB (compose default) or on any host that can reach SecEx.

## Env

| Var | Default | |
|---|---|---|
| `WPSANDBOX_MWDB_URL` | — | e.g. `http://mwdb:8080/api` |
| `WPSANDBOX_MWDB_API_KEY` | — | API key of the `wpsandbox-worker` MWDB user |
| `WPSANDBOX_WORKER_LOGIN` | `wpsandbox-worker` | must match `MWDB_WPSANDBOX_WORKER_LOGIN` on the mwdb service |
| `WPSANDBOX_REDIS_URL` | `redis://redis/` | |
| `WPSANDBOX_TEMPLATE` | `wordpress` | SecEx template alias |
| `WPSANDBOX_CONCURRENCY` | `2` | parallel runs |
| `WPSANDBOX_EGRESS_BLOCKLIST` | RFC1918 + CGNAT + link-local | CIDRs dropped inside the VM |
| `E2B_API_KEY`, `E2B_API_URL`, `E2B_PROXY` | — | SecEx credentials |
| `WPSANDBOX_FAKE_SANDBOX` | `0` | `1` → canned report, no SecEx |
| `WPSANDBOX_FAKE_REPORT` | — | path to the canned report |

## MWDB user setup (once, as admin)

    curl -s -X POST $MWDB/api/group/wpsandbox -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
      -d '{"capabilities":["access_all_objects","adding_files","adding_blobs","adding_parents","adding_all_attributes","sharing_with_all"]}'
    curl -s -X POST $MWDB/api/user/wpsandbox-worker -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
      -d '{"email":"wpsandbox-worker@example.invalid","additional_info":"WP sandbox worker","send_email":false}'
    curl -s -X PUT  $MWDB/api/group/wpsandbox/member/wpsandbox-worker -H "Authorization: Bearer $ADMIN"
    curl -s -X POST $MWDB/api/user/wpsandbox-worker/api_key -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' -d '{"name":"worker"}'
    # -> put the returned token in WPSANDBOX_MWDB_API_KEY

## Tests

    python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
    python -m pytest -v
```

(The user creation call may require a `PUT /api/user/<login>/change_password` or setting a password via the admin UI depending on MWDB version — the API key is what the worker uses, a password is not needed.)

- [ ] **Step 6: Bring up the dev stack with the fake backend**

Run:
```bash
docker compose -f docker-compose-dev.yml up --build -d
# create the worker user/group/api key per README, export WPSANDBOX_MWDB_API_KEY, then:
docker compose -f docker-compose-dev.yml up -d wpsandbox-worker
docker compose -f docker-compose-dev.yml logs -f wpsandbox-worker
```
Expected: `{"phase": "started", ...}` and, after `POST /api/wpsandbox/<sha>` from the UI or curl, a `finished` line with `status: done`.

- [ ] **Step 7: Commit**

```bash
git add docker/wpsandbox-worker docker-compose-dev.yml docker-compose-prod.yml .gitignore
git commit -m "wpsandbox worker: main loop, Dockerfile, compose services"
```

---

### Task 7: E2E tests (fake backend + live SecEx)

**Files:**
- Create: `tests/backend/test_wpsandbox.py`
- Create: `tests/backend/test_wpsandbox_live.py`
- Create: `tests/backend/fixtures/wpsandbox_benign.php`

**Interfaces:**
- Consumes the running dev stack (`MWDB_URL`, `MWDB_ADMIN_LOGIN`, `MWDB_ADMIN_PASSWORD` env as in `tests/backend/README`/`CLAUDE.md`), the `admin_session` fixture from `tests/backend/conftest.py`, and the worker container.

- [ ] **Step 1: Fake-backend e2e**

`tests/backend/test_wpsandbox.py`:
```python
"""End-to-end: queue a run, worker (fake sandbox) publishes report + attributes.

Requires the dev stack with the wpsandbox plugin and the wpsandbox-worker
container running with WPSANDBOX_FAKE_SANDBOX=1.
    cd tests/backend && uv run pytest test_wpsandbox.py -v
"""
import time
import uuid

import pytest


def _poll(session, run_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = session.session.get(session.mwdb_url + f"/wpsandbox/run/{run_id}")
        assert r.status_code == 200, r.text
        d = r.json()
        if d["status"] in ("done", "failed", "timeout"):
            return d
        time.sleep(1)
    pytest.fail("run did not finish in time")


def test_wpsandbox_run_publishes_report(admin_session):
    sample = admin_session.add_sample(
        filename=f"wps-{uuid.uuid4().hex[:6]}.php",
        content=f"<?php // {uuid.uuid4()}".encode(),
    )
    sha = sample["id"]
    r = admin_session.session.post(admin_session.mwdb_url + f"/wpsandbox/{sha}",
                                   json={"mode": "webroot", "timeout": 5})
    if r.status_code == 404 and "not found" not in r.text.lower():
        pytest.skip("wpsandbox plugin not registered")
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]

    # duplicate is rejected while active
    dup = admin_session.session.post(admin_session.mwdb_url + f"/wpsandbox/{sha}",
                                     json={"mode": "webroot", "timeout": 5})
    assert dup.status_code in (409, 202)  # 202 only if the worker already finished

    run = _poll(admin_session, run_id)
    assert run["status"] == "done", run
    assert run["sandbox_id"].startswith("fake-sandbox-")
    blob_id = run["report_blob_id"]

    blob = admin_session.get_blob(blob_id)
    assert blob["blob_type"] == "wp-sandbox-report"
    full = admin_session.get_sample(sha)
    assert blob_id in [c["id"] for c in full["children"]]

    attrs = admin_session.get_attributes(sha)["attributes"]
    keys = {(a["key"], a["value"]) for a in attrs}
    assert ("c2_host", "evil.test") in keys
    assert ("wp_user_added", "wp_backup") in keys

    # listing
    runs = admin_session.session.get(admin_session.mwdb_url + f"/wpsandbox/{sha}").json()["runs"]
    assert runs[0]["id"] == run_id


def test_wpsandbox_cancel_queued(admin_session):
    # Push a run for a sample the worker will process; cancel may race the worker,
    # so accept either 200 (cancelled) or 409 (already running).
    sample = admin_session.add_sample(filename="c.php", content=f"<?php // {uuid.uuid4()}".encode())
    r = admin_session.session.post(admin_session.mwdb_url + f"/wpsandbox/{sample['id']}",
                                   json={"mode": "webroot"})
    if r.status_code == 404:
        pytest.skip("wpsandbox plugin not registered")
    run_id = r.json()["run_id"]
    d = admin_session.session.delete(admin_session.mwdb_url + f"/wpsandbox/run/{run_id}")
    assert d.status_code in (200, 409)
```

Check `tests/backend/utils.py` for `get_blob` and `get_attributes` helpers (`get_attributes` exists at line ~336; `get_blob` is used by `test_phpdeobf.py`, so it exists).

- [ ] **Step 2: Live SecEx e2e**

`tests/backend/fixtures/wpsandbox_benign.php`:
```php
<?php
// Benign probe used by the live wpsandbox test: touches a file, does one HTTP request,
// echoes a marker. Safe to run anywhere.
@file_put_contents(__DIR__ . '/wpsandbox-live-marker.txt', "hi\n");
@file_get_contents('http://example.com/');
echo "WPSANDBOX-LIVE-OK";
```

`tests/backend/test_wpsandbox_live.py`:
```python
"""Real SecEx run. Skipped unless the worker is configured for SecEx
(set WPSANDBOX_LIVE=1 when running the stack with WPSANDBOX_FAKE_SANDBOX=0).
    WPSANDBOX_LIVE=1 uv run pytest test_wpsandbox_live.py -v
"""
import json
import os
import time
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "wpsandbox_benign.php"

pytestmark = pytest.mark.skipif(os.environ.get("WPSANDBOX_LIVE") != "1",
                                reason="set WPSANDBOX_LIVE=1 with a SecEx-backed worker")


def test_live_run_instrumentation(admin_session):
    sample = admin_session.add_sample(filename="wpsandbox_benign.php", content=FIXTURE.read_bytes())
    sha = sample["id"]
    r = admin_session.session.post(admin_session.mwdb_url + f"/wpsandbox/{sha}",
                                   json={"mode": "webroot", "timeout": 10})
    assert r.status_code in (202, 409), r.text
    run_id = r.json()["run_id"]
    deadline = time.time() + 600
    while time.time() < deadline:
        d = admin_session.session.get(admin_session.mwdb_url + f"/wpsandbox/run/{run_id}").json()
        if d["status"] in ("done", "failed", "timeout"):
            break
        time.sleep(5)
    assert d["status"] == "done", d
    report = json.loads(admin_session.get_blob(d["report_blob_id"])["content"])

    assert report["trigger"]["requests"][0]["status"] == 200
    assert "WPSANDBOX-LIVE-OK" in report["trigger"]["requests"][0]["response_head"]
    created = {c["path"] for c in report["filesystem"]["created"]}
    assert "wp-content/uploads/wpsandbox-live-marker.txt" in created
    assert any("example.com" in f["url"] for f in report["network"]["flows"])
    assert any(q["name"] == "example.com" for q in report["network"]["dns"])
    assert report["run"]["wp_version"] and report["run"]["php_version"].startswith("8.4")
```

- [ ] **Step 3: Run the fake e2e**

Run (stack up with fake worker, env exported per `CLAUDE.md`): `cd tests/backend && uv run pytest test_wpsandbox.py -v`
Expected: 2 passed

- [ ] **Step 4: Run the live e2e (when SecEx is reachable)**

Run: set `WPSANDBOX_FAKE_SANDBOX=0`, `E2B_API_KEY`, `E2B_API_URL` for the worker, restart it, then `cd tests/backend && WPSANDBOX_LIVE=1 uv run pytest test_wpsandbox_live.py -v`
Expected: 1 passed. If it cannot be run in this environment, say so explicitly in the commit message and the PR description — this is the only test that proves the template's instrumentation.

- [ ] **Step 5: Deployment doc**

Append to `deploy/DEPLOYMENT.md` a section "## WP Sandbox worker" containing: (1) the four `curl` calls from the worker README to create group/user/key, (2) the `wpsandbox-worker.env` template:
```bash
WPSANDBOX_MWDB_API_KEY=...
E2B_API_KEY=...
E2B_API_URL=https://sandbox-api-secex.a8c.com
WPSANDBOX_TEMPLATE=wordpress
WPSANDBOX_CONCURRENCY=2
WPSANDBOX_EGRESS_BLOCKLIST=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,169.254.0.0/16
```
(3) the reachability check `docker compose -f docker-compose-prod.yml run --rm --entrypoint "" wpsandbox-worker python -c "import os,requests;print(requests.get(os.environ['E2B_API_URL']+'/health',timeout=10).status_code)"` and the note that if it fails, run the worker container on a host inside the a8c network with `WPSANDBOX_REDIS_URL`/`WPSANDBOX_MWDB_URL` pointed at the droplet (Redis must then be exposed over a private network/VPN — not the public internet), (4) the template build step from `docker/wpsandbox-template/README.md`.

- [ ] **Step 6: Commit**

```bash
git add tests/backend/test_wpsandbox.py tests/backend/test_wpsandbox_live.py tests/backend/fixtures/wpsandbox_benign.php deploy/DEPLOYMENT.md
git commit -m "wpsandbox: e2e tests (fake + live) and deployment notes"
```

---

## Self-review

- **Spec coverage:** §6 steps 1–7 → Tasks 5–6; error policy (retry once, backoff+requeue, stderr tail, partial report on timeout) → Tasks 2, 5, 6; config → Task 1; structured logs → Task 5/6; §8 worker group capabilities → README/DEPLOYMENT (Task 6/7); §9.3 worker unit tests → Tasks 2–6; §9.4 e2e fake + live → Task 7; §10 compose + docs → Tasks 6–7.
- **Cross-stage dependency made explicit:** the run JSON must carry `sample_sha256` (Task 5 note amends Stage 2).
- **Type consistency:** `process_run(run_id, client, backend, settings) -> str` (Task 5) used by `Worker.run_once` (Task 6); `SandboxBackend.execute(sample, mode, params, blocklist, on_started) -> RunResult` (Task 4) used in Task 5; `MwdbClient` method names identical between Tasks 2 and 5; `extract.attributes(report) -> list[(key, value)]` in 3 and 5.
