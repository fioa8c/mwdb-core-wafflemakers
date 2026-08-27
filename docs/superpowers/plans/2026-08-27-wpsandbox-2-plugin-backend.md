# WP Sandbox — Stage 2: MWDB Plugin Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `wpsandbox` MWDB plugin backend: a `wpsandbox_run` table, the REST resources that create/list/poll/cancel runs and let the worker update them, a Redis job queue, and attribute definitions — all testable without a running MWDB stack.

**Architecture:** Python package at `docker/plugins/wpsandbox/` following the `phpdeobf` layout (`__init__.py` entrypoint, `resource.py`, `tests/` with the stubbed-`mwdb` conftest). New pieces vs. `phpdeobf`: a SQLAlchemy model (`model.py`) created idempotently at entrypoint, a tiny queue module (`queue.py`) over `redis`, config helpers (`config.py`), and request validation (`validation.py`) kept separate so it is unit-testable with plain dicts.

**Tech Stack:** Python ≥3.10, Flask-RESTful `Resource` (via `mwdb.core.service`), SQLAlchemy 1.4 models on `mwdb.model.db`, `redis` 4.x (already an MWDB dependency), pytest + `fakeredis` for tests.

**Spec:** `docs/superpowers/specs/2026-08-27-wpsandbox-plugin-design.md` (§5 Plugin, §8 Security)

## Global Constraints

- Plugin package name `wpsandbox`; installed into the MWDB image by `deploy/docker/Dockerfile` because it has a `pyproject.toml`. **Do not** put any other `pyproject.toml` under `docker/plugins/wpsandbox/` (the Dockerfile would pip-install it too).
- Registered in `MWDB_PLUGINS` in both `docker-compose-dev.yml` and `docker-compose-prod.yml`.
- Redis key: list `wpsandbox:jobs`, values are run ids (uuid strings).
- Run statuses: `queued | running | done | failed | timeout`. Derived "worker lost" rule: `running` and `started_at < now - (MWDB_WPSANDBOX_MAX_TIMEOUT + 600) s` → reported as `failed`, `error="worker lost"`.
- Config env (mwdb service): `MWDB_WPSANDBOX_REDIS_URL` (default: MWDB's `MWDB_REDIS_URI`), `MWDB_WPSANDBOX_WORKER_LOGIN` (default `wpsandbox-worker`), `MWDB_WPSANDBOX_MAX_SAMPLE_BYTES` (default `20971520`), `MWDB_WPSANDBOX_MAX_TIMEOUT` (default `300`).
- `POST` requires `adding_blobs`; `PATCH` requires `g.auth_user.login == worker login`; `DELETE` requires requester or `manage_users`.
- Tests run with `cd docker/plugins/wpsandbox && python -m pytest tests/ -v` and must not need Postgres/Redis/libmagic.

---

## File map

```
docker/plugins/wpsandbox/
├── pyproject.toml
├── __init__.py          # entrypoint(): ensure_schema, ensure_attribute_definitions, register resources
├── config.py            # env accessors
├── model.py             # WpSandboxRun + ensure_schema() + to_dict()/effective_status()
├── validation.py        # normalize_params(), ValidationError
├── queue.py             # JobQueue (push/remove) over redis
├── resource.py          # WpSandboxRunListResource, WpSandboxRunResource
├── README.md
└── tests/
    ├── __init__.py
    ├── conftest.py      # stubs mwdb.* like phpdeobf; provides `app` fixture
    ├── test_validation.py
    ├── test_model.py
    ├── test_queue.py
    └── test_resource.py
```

---

### Task 1: Package scaffold + validation

**Files:**
- Create: `docker/plugins/wpsandbox/pyproject.toml`
- Create: `docker/plugins/wpsandbox/__init__.py` (minimal; completed in Task 6)
- Create: `docker/plugins/wpsandbox/config.py`
- Create: `docker/plugins/wpsandbox/validation.py`
- Create: `docker/plugins/wpsandbox/tests/__init__.py`, `tests/conftest.py`
- Test: `docker/plugins/wpsandbox/tests/test_validation.py`

**Interfaces:**
- Produces: `validation.normalize_params(body: dict, *, max_timeout: int, sample_sha256: str, sample_name: str | None) -> tuple[str, dict]` returning `(mode, params)` where `params` always has keys `path, method, query, body, timeout` (webroot) or `timeout` only (plugin); raises `validation.ValidationError(message)`.
- Produces: `config.redis_url()`, `config.worker_login()`, `config.max_sample_bytes()`, `config.max_timeout()`.

- [ ] **Step 1: pyproject + package init + config**

`docker/plugins/wpsandbox/pyproject.toml`:
```toml
[project]
name = "wpsandbox"
version = "0.1.0"
description = "WordPress sandbox (SecEx) plugin for MWDB"
requires-python = ">=3.10"
dependencies = [
    "redis>=4.5",
]

[project.optional-dependencies]
dev = ["pytest>=7", "fakeredis>=2.20", "flask>=3", "sqlalchemy>=1.4,<2"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["wpsandbox"]
package-dir = {"wpsandbox" = "."}
```

`docker/plugins/wpsandbox/__init__.py` (temporary; Task 6 completes it):
```python
"""WordPress sandbox plugin — runs samples in a SecEx WordPress microVM."""
import logging

__author__ = "Waffle Makers"
__version__ = "0.1.0"

logger = logging.getLogger("mwdb.plugin.wpsandbox")
```

`docker/plugins/wpsandbox/config.py`:
```python
import os

JOBS_KEY = "wpsandbox:jobs"


def redis_url() -> str:
    return os.environ.get("MWDB_WPSANDBOX_REDIS_URL") or os.environ.get(
        "MWDB_REDIS_URI", "redis://redis/"
    )


def worker_login() -> str:
    return os.environ.get("MWDB_WPSANDBOX_WORKER_LOGIN", "wpsandbox-worker")


def max_sample_bytes() -> int:
    return int(os.environ.get("MWDB_WPSANDBOX_MAX_SAMPLE_BYTES", 20 * 1024 * 1024))


def max_timeout() -> int:
    return int(os.environ.get("MWDB_WPSANDBOX_MAX_TIMEOUT", 300))


def worker_lost_after() -> int:
    return max_timeout() + 600
```

`docker/plugins/wpsandbox/tests/__init__.py`: empty.

`docker/plugins/wpsandbox/tests/conftest.py` — copy `docker/plugins/phpdeobf/tests/conftest.py` verbatim, then append these stubs so `model.py`/`resource.py` import without MWDB:
```python
# --- wpsandbox additions -------------------------------------------------
# mwdb.model.db must be a real SQLAlchemy-like object so model.py can declare
# columns. We give it a real declarative base bound to in-memory SQLite.
import sqlalchemy as sa
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

_engine = sa.create_engine("sqlite://")
_Base = declarative_base()
_Session = scoped_session(sessionmaker(bind=_engine))


class _FakeDB:
    Model = _Base
    Column = sa.Column
    Integer = sa.Integer
    String = sa.String
    Text = sa.Text
    DateTime = sa.DateTime
    JSON = sa.JSON
    ForeignKey = sa.ForeignKey
    engine = _engine
    session = _Session


import mwdb.model as _mwdb_model  # the MagicMock stub from above
_mwdb_model.db = _FakeDB

# Minimal `object` and `user` tables so FKs resolve on SQLite.
sa.Table("object", _Base.metadata, sa.Column("id", sa.Integer, primary_key=True),
         sa.Column("dhash", sa.String(64)))
sa.Table("user", _Base.metadata, sa.Column("id", sa.Integer, primary_key=True))

if "mwdb.core.capabilities" not in sys.modules:
    _caps = MagicMock()
    _caps.Capabilities.adding_blobs = "adding_blobs"
    _caps.Capabilities.manage_users = "manage_users"
    sys.modules["mwdb.core.capabilities"] = _caps
    import mwdb.core as _mwdb_core
    _mwdb_core.capabilities = _caps
```

- [ ] **Step 2: Write the failing test**

`docker/plugins/wpsandbox/tests/test_validation.py`:
```python
import pytest

from wpsandbox.validation import ValidationError, normalize_params

SHA = "ab" * 32


def test_webroot_defaults():
    mode, p = normalize_params({"mode": "webroot"}, max_timeout=300, sample_sha256=SHA, sample_name="x.php")
    assert mode == "webroot"
    assert p == {"path": f"wp-content/uploads/{SHA[:8]}.php", "method": "GET",
                 "query": "", "body": "", "timeout": 120}


def test_webroot_explicit_values_normalised():
    _, p = normalize_params(
        {"mode": "webroot", "path": "/wp-content/uploads/../x.php ", "method": "post",
         "query": "a=1", "body": "b", "timeout": 30},
        max_timeout=300, sample_sha256=SHA, sample_name=None)
    assert p["path"] == "wp-content/uploads/../x.php".replace("../", "")  # traversal stripped
    assert p["method"] == "POST" and p["timeout"] == 30


def test_plugin_mode_only_keeps_timeout():
    mode, p = normalize_params({"mode": "plugin", "path": "ignored", "timeout": 10},
                               max_timeout=300, sample_sha256=SHA, sample_name="p.zip")
    assert mode == "plugin" and p == {"timeout": 10}


def test_plugin_mode_requires_zip_name():
    with pytest.raises(ValidationError, match="zip"):
        normalize_params({"mode": "plugin"}, max_timeout=300, sample_sha256=SHA, sample_name="p.php")


@pytest.mark.parametrize("body,msg", [
    ({"mode": "nope"}, "mode"),
    ({"mode": "webroot", "method": "PUT"}, "method"),
    ({"mode": "webroot", "timeout": 301}, "timeout"),
    ({"mode": "webroot", "timeout": 0}, "timeout"),
    ({"mode": "webroot", "timeout": "abc"}, "timeout"),
    ({"mode": "webroot", "path": "wp-content/uploads/x.txt"}, "path"),
])
def test_rejects(body, msg):
    with pytest.raises(ValidationError, match=msg):
        normalize_params(body, max_timeout=300, sample_sha256=SHA, sample_name="x.php")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd docker/plugins/wpsandbox && python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]' && python -m pytest tests/test_validation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wpsandbox.validation'`

- [ ] **Step 4: Implement**

`docker/plugins/wpsandbox/validation.py`:
```python
"""Request validation for run creation. Pure functions, no Flask."""

MODES = ("webroot", "plugin")
METHODS = ("GET", "POST")
DEFAULT_TIMEOUT = 120


class ValidationError(ValueError):
    pass


def _timeout(value, max_timeout: int) -> int:
    if value is None:
        return DEFAULT_TIMEOUT
    try:
        t = int(value)
    except (TypeError, ValueError):
        raise ValidationError("timeout must be an integer")
    if t < 1 or t > max_timeout:
        raise ValidationError(f"timeout must be between 1 and {max_timeout}")
    return t


def _path(value, sha256: str) -> str:
    if not value:
        return f"wp-content/uploads/{sha256[:8]}.php"
    p = str(value).strip().lstrip("/").replace("../", "")
    if not p.lower().endswith((".php", ".phtml", ".php5", ".php7", ".inc")):
        raise ValidationError("path must end with a PHP extension")
    return p


def normalize_params(body: dict, *, max_timeout: int, sample_sha256: str,
                     sample_name: str | None) -> tuple[str, dict]:
    body = body or {}
    mode = body.get("mode")
    if mode not in MODES:
        raise ValidationError(f"mode must be one of {', '.join(MODES)}")
    timeout = _timeout(body.get("timeout"), max_timeout)
    if mode == "plugin":
        if not (sample_name or "").lower().endswith(".zip"):
            raise ValidationError("plugin mode requires a .zip sample")
        return mode, {"timeout": timeout}
    method = str(body.get("method") or "GET").upper()
    if method not in METHODS:
        raise ValidationError("method must be GET or POST")
    return mode, {
        "path": _path(body.get("path"), sample_sha256),
        "method": method,
        "query": str(body.get("query") or ""),
        "body": str(body.get("body") or ""),
        "timeout": timeout,
    }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd docker/plugins/wpsandbox && python -m pytest tests/test_validation.py -v`
Expected: 10 passed

- [ ] **Step 6: Commit**

```bash
git add docker/plugins/wpsandbox
git commit -m "wpsandbox plugin: scaffold, config, run-param validation"
```

---

### Task 2: Run model + idempotent schema creation

**Files:**
- Create: `docker/plugins/wpsandbox/model.py`
- Test: `docker/plugins/wpsandbox/tests/test_model.py`

**Interfaces:**
- Produces: `class WpSandboxRun(db.Model)` (`__tablename__ = "wpsandbox_run"`) with columns per spec §5.1 (`id` String(36) pk, `object_id`, `requested_by`, `mode`, `params` JSON, `status`, `created_at`, `started_at`, `finished_at`, `error`, `report_blob_id` String(64) blob dhash, `sandbox_id`); `WpSandboxRun.new(object_id, requested_by, mode, params) -> WpSandboxRun` (uuid4 id, status `queued`, `created_at` now UTC); `effective_status(now=None) -> (status, error)` applying the worker-lost rule; `to_dict(now=None) -> dict` with ISO timestamps; `ensure_schema() -> bool` (creates the table if missing; returns False and logs on failure instead of raising).

- [ ] **Step 1: Write the failing test**

`docker/plugins/wpsandbox/tests/test_model.py`:
```python
from datetime import datetime, timedelta, timezone

import pytest

from wpsandbox import model
from wpsandbox.model import WpSandboxRun, ensure_schema


@pytest.fixture(autouse=True)
def schema():
    assert ensure_schema() is True
    yield
    from mwdb.model import db
    db.session.query(WpSandboxRun).delete()
    db.session.commit()


def test_new_run_defaults():
    r = WpSandboxRun.new(object_id=1, requested_by=2, mode="webroot", params={"timeout": 5})
    assert len(r.id) == 36 and r.status == "queued"
    assert r.params == {"timeout": 5} and r.created_at.tzinfo is not None


def test_to_dict_shape():
    r = WpSandboxRun.new(object_id=1, requested_by=2, mode="plugin", params={"timeout": 5})
    d = r.to_dict()
    assert set(d) == {"id", "object_id", "requested_by", "mode", "params", "status",
                      "created_at", "started_at", "finished_at", "error",
                      "report_blob_id", "sandbox_id"}
    assert d["started_at"] is None and d["created_at"].endswith("+00:00")


def test_worker_lost_derived(monkeypatch):
    monkeypatch.setenv("MWDB_WPSANDBOX_MAX_TIMEOUT", "300")
    r = WpSandboxRun.new(object_id=1, requested_by=2, mode="webroot", params={})
    r.status = "running"
    r.started_at = datetime.now(timezone.utc) - timedelta(seconds=300 + 600 + 1)
    assert r.effective_status() == ("failed", "worker lost")
    assert r.to_dict()["status"] == "failed" and r.to_dict()["error"] == "worker lost"
    r.started_at = datetime.now(timezone.utc) - timedelta(seconds=100)
    assert r.effective_status() == ("running", None)


def test_ensure_schema_idempotent_and_persists():
    from mwdb.model import db
    assert ensure_schema() is True
    r = WpSandboxRun.new(object_id=1, requested_by=2, mode="webroot", params={})
    db.session.add(r)
    db.session.commit()
    assert db.session.get(WpSandboxRun, r.id).mode == "webroot"


def test_ensure_schema_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(WpSandboxRun.__table__, "create", boom)
    assert ensure_schema() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd docker/plugins/wpsandbox && python -m pytest tests/test_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wpsandbox.model'`

- [ ] **Step 3: Implement**

`docker/plugins/wpsandbox/model.py`:
```python
"""wpsandbox_run table. Created idempotently (MWDB has no plugin migrations)."""
import uuid
from datetime import datetime, timezone

from mwdb.model import db

from . import config, logger

STATUSES = ("queued", "running", "done", "failed", "timeout")
TERMINAL = ("done", "failed", "timeout")


def _iso(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class WpSandboxRun(db.Model):
    __tablename__ = "wpsandbox_run"

    id = db.Column(db.String(36), primary_key=True)
    object_id = db.Column(db.Integer, db.ForeignKey("object.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    requested_by = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"))
    mode = db.Column(db.String(16), nullable=False)
    params = db.Column(db.JSON, nullable=False, default=dict)
    status = db.Column(db.String(16), nullable=False, default="queued", index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)
    started_at = db.Column(db.DateTime(timezone=True))
    finished_at = db.Column(db.DateTime(timezone=True))
    error = db.Column(db.Text)
    # dhash (sha256) of the report TextBlob — the worker only knows dhashes over HTTP
    report_blob_id = db.Column(db.String(64))
    sandbox_id = db.Column(db.String(128))

    @classmethod
    def new(cls, object_id: int, requested_by: int | None, mode: str, params: dict):
        return cls(
            id=str(uuid.uuid4()),
            object_id=object_id,
            requested_by=requested_by,
            mode=mode,
            params=params,
            status="queued",
            created_at=datetime.now(timezone.utc),
        )

    def effective_status(self, now=None):
        now = now or datetime.now(timezone.utc)
        if self.status == "running" and self.started_at is not None:
            started = self.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if (now - started).total_seconds() > config.worker_lost_after():
                return "failed", "worker lost"
        return self.status, self.error

    def to_dict(self, now=None) -> dict:
        status, error = self.effective_status(now)
        return {
            "id": self.id,
            "object_id": self.object_id,
            "requested_by": self.requested_by,
            "mode": self.mode,
            "params": self.params,
            "status": status,
            "created_at": _iso(self.created_at),
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "error": error,
            "report_blob_id": self.report_blob_id,
            "sandbox_id": self.sandbox_id,
        }


def ensure_schema() -> bool:
    try:
        WpSandboxRun.__table__.create(bind=db.engine, checkfirst=True)
        return True
    except Exception as e:  # pragma: no cover - exercised via monkeypatch
        logger.warning("wpsandbox: could not create wpsandbox_run table yet: %s", e)
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd docker/plugins/wpsandbox && python -m pytest tests/test_model.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add docker/plugins/wpsandbox/model.py docker/plugins/wpsandbox/tests/test_model.py
git commit -m "wpsandbox plugin: run model with idempotent schema creation"
```

---

### Task 3: Redis job queue

**Files:**
- Create: `docker/plugins/wpsandbox/queue.py`
- Test: `docker/plugins/wpsandbox/tests/test_queue.py`

**Interfaces:**
- Produces: `class JobQueue(client=None)` — `push(run_id: str) -> None` (`RPUSH`), `remove(run_id: str) -> int` (`LREM` all), `pending() -> list[str]`; `get_queue() -> JobQueue` (module singleton built from `config.redis_url()`). Tests inject `fakeredis.FakeRedis()`.

- [ ] **Step 1: Write the failing test**

`docker/plugins/wpsandbox/tests/test_queue.py`:
```python
import fakeredis

from wpsandbox.config import JOBS_KEY
from wpsandbox.queue import JobQueue


def test_push_appends_in_order():
    r = fakeredis.FakeRedis()
    q = JobQueue(r)
    q.push("a"); q.push("b")
    assert r.lrange(JOBS_KEY, 0, -1) == [b"a", b"b"]
    assert q.pending() == ["a", "b"]


def test_remove_deletes_all_occurrences():
    r = fakeredis.FakeRedis()
    q = JobQueue(r)
    q.push("a"); q.push("b"); q.push("a")
    assert q.remove("a") == 2
    assert q.pending() == ["b"]
    assert q.remove("zzz") == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd docker/plugins/wpsandbox && python -m pytest tests/test_queue.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wpsandbox.queue'`

- [ ] **Step 3: Implement**

`docker/plugins/wpsandbox/queue.py`:
```python
"""Redis-backed FIFO of run ids. The worker BLPOPs the same key."""
import redis

from . import config


class JobQueue:
    def __init__(self, client=None):
        self.client = client or redis.Redis.from_url(config.redis_url())

    def push(self, run_id: str) -> None:
        self.client.rpush(config.JOBS_KEY, run_id)

    def remove(self, run_id: str) -> int:
        return int(self.client.lrem(config.JOBS_KEY, 0, run_id))

    def pending(self) -> list[str]:
        return [v.decode() for v in self.client.lrange(config.JOBS_KEY, 0, -1)]


_queue: JobQueue | None = None


def get_queue() -> JobQueue:
    global _queue
    if _queue is None:
        _queue = JobQueue()
    return _queue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd docker/plugins/wpsandbox && python -m pytest tests/test_queue.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add docker/plugins/wpsandbox/queue.py docker/plugins/wpsandbox/tests/test_queue.py
git commit -m "wpsandbox plugin: redis job queue"
```

---

### Task 4: Run list resource (POST create + GET list)

**Files:**
- Create: `docker/plugins/wpsandbox/resource.py`
- Test: `docker/plugins/wpsandbox/tests/test_resource.py`

**Interfaces:**
- Produces: `WpSandboxRunListResource` bound to `/wpsandbox/<hash64:identifier>`:
  - `POST` → `202 {"run_id": ...}`; `400` on validation error; `404` when `File.access` returns None; `403` without `adding_blobs`; `409 {"run_id": existing, "message": ...}` when an active identical run exists.
  - `GET` → `200 {"runs": [run.to_dict() + {"sample_sha256": <parent dhash>}, ...]}` newest first. Every run JSON returned by any endpoint carries `sample_sha256` (the worker needs it to download the sample).
- Module attributes `File`, `db`, `get_queue` are re-exported so tests can monkeypatch them (same trick `phpdeobf` uses with `client`).

- [ ] **Step 1: Write the failing test**

`docker/plugins/wpsandbox/tests/test_resource.py`:
```python
"""Flask test-client tests for wpsandbox.resource with stubbed MWDB."""
import functools
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import fakeredis
import pytest
from flask import Flask

from wpsandbox.model import WpSandboxRun, ensure_schema
from wpsandbox.queue import JobQueue


@pytest.fixture
def app(monkeypatch):
    def noop(f):
        @functools.wraps(f)
        def w(*a, **k):
            return f(*a, **k)
        return w

    import mwdb.resources
    monkeypatch.setattr(mwdb.resources, "requires_authorization", noop)
    monkeypatch.setenv("MWDB_WPSANDBOX_MAX_TIMEOUT", "300")
    monkeypatch.setenv("MWDB_WPSANDBOX_MAX_SAMPLE_BYTES", "1000")
    monkeypatch.setenv("MWDB_WPSANDBOX_WORKER_LOGIN", "wpsandbox-worker")

    if "wpsandbox.resource" in sys.modules:
        del sys.modules["wpsandbox.resource"]
    from wpsandbox import resource as res

    ensure_schema()
    from mwdb.model import db
    db.session.query(WpSandboxRun).delete(); db.session.commit()
    db.session.execute(db.Model.metadata.tables["object"].delete())
    db.session.execute(db.Model.metadata.tables["object"].insert().values(id=11, dhash="ab" * 32))
    db.session.commit()

    sample = MagicMock()
    sample.id = 11; sample.sha256 = "ab" * 32; sample.file_name = "x.php"; sample.file_size = 100
    fake_access = MagicMock(return_value=sample)
    monkeypatch.setattr(res.File, "access", fake_access)

    fake_redis = fakeredis.FakeRedis()
    monkeypatch.setattr(res, "get_queue", lambda: JobQueue(fake_redis))

    user = MagicMock()
    user.id = 5; user.login = "alice"
    user.has_rights = MagicMock(return_value=True)

    flask_app = Flask(__name__)
    flask_app.add_url_rule("/api/wpsandbox/<identifier>",
                           view_func=res.WpSandboxRunListResource.as_view("runs"))
    flask_app.add_url_rule("/api/wpsandbox/run/<run_id>",
                           view_func=res.WpSandboxRunResource.as_view("run"))

    @flask_app.before_request
    def _auth():
        from flask import g
        g.auth_user = user

    flask_app.sample = sample
    flask_app.user = user
    flask_app.redis = fake_redis
    flask_app.fake_access = fake_access
    flask_app.db = db
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


# --- POST ---------------------------------------------------------------

def test_post_creates_queued_run_and_pushes_job(client, app):
    r = client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot", "timeout": 10})
    assert r.status_code == 202, r.data
    run_id = r.get_json()["run_id"]
    assert app.redis.lrange("wpsandbox:jobs", 0, -1) == [run_id.encode()]
    row = app.db.session.get(WpSandboxRun, run_id)
    assert row.status == "queued" and row.object_id == 11 and row.requested_by == 5
    assert row.params["path"] == "wp-content/uploads/abababab.php"


def test_post_404_when_no_access(client, app):
    app.fake_access.return_value = None
    assert client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot"}).status_code == 404


def test_post_403_without_adding_blobs(client, app):
    app.user.has_rights.return_value = False
    assert client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot"}).status_code == 403


def test_post_400_on_bad_params(client):
    r = client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot", "timeout": 999})
    assert r.status_code == 400 and "timeout" in r.get_json()["message"]


def test_post_400_when_sample_too_large(client, app):
    app.sample.file_size = 1001
    r = client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot"})
    assert r.status_code == 400 and "size" in r.get_json()["message"].lower()


def test_post_409_on_duplicate_active_run(client, app):
    first = client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot"}).get_json()["run_id"]
    r = client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot", "method": "get"})
    assert r.status_code == 409 and r.get_json()["run_id"] == first
    # different params -> new run
    r = client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot", "query": "x=1"})
    assert r.status_code == 202
    assert len(app.redis.lrange("wpsandbox:jobs", 0, -1)) == 2


def test_post_after_terminal_run_is_allowed(client, app):
    run_id = client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot"}).get_json()["run_id"]
    row = app.db.session.get(WpSandboxRun, run_id); row.status = "done"; app.db.session.commit()
    assert client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot"}).status_code == 202


# --- GET list -----------------------------------------------------------

def test_get_lists_runs_newest_first(client, app):
    a = client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot"}).get_json()["run_id"]
    b = client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot", "query": "q"}).get_json()["run_id"]
    r = client.get("/api/wpsandbox/" + "ab" * 32)
    assert r.status_code == 200
    ids = [x["id"] for x in r.get_json()["runs"]]
    assert ids == [b, a]
    assert all(x["sample_sha256"] == "ab" * 32 for x in r.get_json()["runs"])


def test_get_404_when_no_access(client, app):
    app.fake_access.return_value = None
    assert client.get("/api/wpsandbox/" + "ab" * 32).status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd docker/plugins/wpsandbox && python -m pytest tests/test_resource.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wpsandbox.resource'`

- [ ] **Step 3: Implement the list resource**

`docker/plugins/wpsandbox/resource.py`:
```python
"""REST resources for the wpsandbox plugin.

  POST   /api/wpsandbox/<hash>        create a run (202) — requires adding_blobs
  GET    /api/wpsandbox/<hash>        list runs for a sample
  GET    /api/wpsandbox/run/<id>      poll one run
  PATCH  /api/wpsandbox/run/<id>      worker-only status update
  DELETE /api/wpsandbox/run/<id>      cancel a queued run
"""
from datetime import datetime, timezone

from flask import g, jsonify, request
from werkzeug.exceptions import BadRequest, Conflict, Forbidden, NotFound

import mwdb.model as _mwdb_model
from mwdb.core.capabilities import Capabilities
from mwdb.core.service import Resource
from mwdb.model import File
from mwdb.resources import requires_authorization

from . import config, logger
from .model import TERMINAL, WpSandboxRun, ensure_schema
from .queue import get_queue as _get_queue
from .validation import ValidationError, normalize_params

# Re-exported names so tests can monkeypatch them on this module.
get_queue = _get_queue

_schema_ready = False


def _db():
    return _mwdb_model.db


def _ensure_schema_once():
    global _schema_ready
    if not _schema_ready:
        _schema_ready = ensure_schema()


def _now():
    return datetime.now(timezone.utc)


def _parse_iso(value):
    if value is None:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _run_json(run, now=None) -> dict:
    """Run dict + the parent sample's sha256 (dhash), which the worker needs."""
    d = run.to_dict(now)
    table = _db().Model.metadata.tables["object"]
    row = _db().session.execute(
        table.select().where(table.c.id == run.object_id)
    ).first()
    d["sample_sha256"] = row.dhash if row is not None else None
    return d


class WpSandboxRunListResource(Resource):
    @requires_authorization
    def get(self, identifier):
        """
        ---
        summary: List WP sandbox runs for a sample
        tags: [wpsandbox]
        security: [{bearerAuth: []}]
        parameters:
          - in: path
            name: identifier
            schema: {type: string}
            required: true
        responses:
          200: {description: Runs, newest first}
          404: {description: Sample not found or unauthorized}
        """
        _ensure_schema_once()
        sample = File.access(identifier)
        if sample is None:
            raise NotFound("Sample not found or you don't have access to it")
        runs = (
            _db().session.query(WpSandboxRun)
            .filter(WpSandboxRun.object_id == sample.id)
            .order_by(WpSandboxRun.created_at.desc())
            .all()
        )
        now = _now()
        return jsonify({"runs": [_run_json(r, now) for r in runs]})

    @requires_authorization
    def post(self, identifier):
        """
        ---
        summary: Queue a WP sandbox run for a sample
        description: |
          Body: {mode: webroot|plugin, path?, method?, query?, body?, timeout?}.
          Requires the adding_blobs capability. Returns 202 with run_id,
          409 if an identical run is already queued/running.
        tags: [wpsandbox]
        security: [{bearerAuth: []}]
        parameters:
          - in: path
            name: identifier
            schema: {type: string}
            required: true
        responses:
          202: {description: Run queued}
          400: {description: Invalid parameters or sample too large}
          403: {description: Missing adding_blobs capability}
          404: {description: Sample not found or unauthorized}
          409: {description: Identical run already active}
        """
        _ensure_schema_once()
        if not g.auth_user.has_rights(Capabilities.adding_blobs):
            raise Forbidden("You don't have required capability (adding_blobs)")
        sample = File.access(identifier)
        if sample is None:
            raise NotFound("Sample not found or you don't have access to it")
        if (sample.file_size or 0) > config.max_sample_bytes():
            raise BadRequest(
                f"Sample size exceeds the maximum of {config.max_sample_bytes()} bytes"
            )
        try:
            mode, params = normalize_params(
                request.get_json(silent=True) or {},
                max_timeout=config.max_timeout(),
                sample_sha256=sample.sha256,
                sample_name=sample.file_name,
            )
        except ValidationError as e:
            raise BadRequest(str(e))

        session = _db().session
        active = (
            session.query(WpSandboxRun)
            .filter(
                WpSandboxRun.object_id == sample.id,
                WpSandboxRun.mode == mode,
                WpSandboxRun.status.in_(("queued", "running")),
            )
            .all()
        )
        for run in active:
            if run.params == params and run.effective_status()[0] in ("queued", "running"):
                response = jsonify(
                    {"run_id": run.id, "message": "An identical run is already active"}
                )
                response.status_code = 409
                return response

        run = WpSandboxRun.new(
            object_id=sample.id, requested_by=g.auth_user.id, mode=mode, params=params
        )
        session.add(run)
        session.commit()
        get_queue().push(run.id)
        logger.info("wpsandbox run queued run=%s sample=%s mode=%s", run.id, identifier, mode)
        response = jsonify({"run_id": run.id})
        response.status_code = 202
        return response


class WpSandboxRunResource(Resource):
    def _load(self, run_id):
        _ensure_schema_once()
        run = _db().session.get(WpSandboxRun, run_id)
        if run is None:
            raise NotFound("Run not found")
        return run

    @requires_authorization
    def get(self, run_id):
        """
        ---
        summary: Get one WP sandbox run
        tags: [wpsandbox]
        security: [{bearerAuth: []}]
        parameters:
          - in: path
            name: run_id
            schema: {type: string}
            required: true
        responses:
          200: {description: Run}
          404: {description: Run not found or sample unauthorized}
        """
        run = self._load(run_id)
        d = _run_json(run)
        if d["sample_sha256"] is None or File.access(d["sample_sha256"]) is None:
            raise NotFound("Run not found")
        return jsonify(d)

    @requires_authorization
    def patch(self, run_id):
        """
        ---
        summary: Update run state (worker only)
        description: |
          Only the configured worker login may call this. Body may contain
          status, started_at, finished_at, error, report_blob_id, sandbox_id.
        tags: [wpsandbox]
        security: [{bearerAuth: []}]
        parameters:
          - in: path
            name: run_id
            schema: {type: string}
            required: true
        responses:
          200: {description: Updated run}
          400: {description: Invalid status}
          403: {description: Not the worker}
          404: {description: Run not found}
        """
        if g.auth_user.login != config.worker_login():
            raise Forbidden("Only the sandbox worker may update runs")
        run = self._load(run_id)
        body = request.get_json(silent=True) or {}
        if "status" in body:
            if body["status"] not in ("queued", "running", "done", "failed", "timeout"):
                raise BadRequest("Invalid status")
            run.status = body["status"]
        for key in ("started_at", "finished_at"):
            if key in body:
                setattr(run, key, _parse_iso(body[key]))
        for key in ("error", "report_blob_id", "sandbox_id"):
            if key in body:
                setattr(run, key, body[key])
        _db().session.commit()
        return jsonify(_run_json(run))

    @requires_authorization
    def delete(self, run_id):
        """
        ---
        summary: Cancel a queued run
        tags: [wpsandbox]
        security: [{bearerAuth: []}]
        parameters:
          - in: path
            name: run_id
            schema: {type: string}
            required: true
        responses:
          200: {description: Cancelled}
          403: {description: Not requester nor admin}
          404: {description: Run not found}
          409: {description: Run is no longer queued}
        """
        run = self._load(run_id)
        user = g.auth_user
        if run.requested_by != user.id and not user.has_rights(Capabilities.manage_users):
            raise Forbidden("Only the requester or an admin may cancel a run")
        if run.status != "queued":
            raise Conflict("Only queued runs can be cancelled")
        get_queue().remove(run.id)
        _db().session.delete(run)
        _db().session.commit()
        return jsonify({"cancelled": run_id})
```

Note: `_run_json` reads the `object` table through SQLAlchemy Core (`db.Model.metadata.tables["object"]`) rather than importing `mwdb.model.Object`, so the same code works against the real MWDB metadata and the SQLite stand-in used in tests. In real MWDB the `object` table is registered in `db.Model.metadata` because `mwdb.model` is imported at app boot.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd docker/plugins/wpsandbox && python -m pytest tests/test_resource.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add docker/plugins/wpsandbox/resource.py docker/plugins/wpsandbox/tests/test_resource.py
git commit -m "wpsandbox plugin: run list/create and run item resources"
```

---

### Task 5: Run item resource tests (GET / PATCH / DELETE)

**Files:**
- Modify: `docker/plugins/wpsandbox/tests/test_resource.py` (append)

**Interfaces:**
- Consumes: `WpSandboxRunResource` from Task 4.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_resource.py`:
```python
# --- run item -----------------------------------------------------------

def _create(client):
    return client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot"}).get_json()["run_id"]


def test_get_run(client):
    run_id = _create(client)
    r = client.get(f"/api/wpsandbox/run/{run_id}")
    assert r.status_code == 200 and r.get_json()["status"] == "queued"
    assert r.get_json()["sample_sha256"] == "ab" * 32
    assert client.get("/api/wpsandbox/run/nope").status_code == 404


def test_patch_requires_worker_login(client, app):
    run_id = _create(client)
    assert client.patch(f"/api/wpsandbox/run/{run_id}", json={"status": "running"}).status_code == 403


def test_patch_by_worker_updates_fields(client, app):
    run_id = _create(client)
    app.user.login = "wpsandbox-worker"
    r = client.patch(f"/api/wpsandbox/run/{run_id}", json={
        "status": "running", "started_at": "2026-08-27T10:00:00Z", "sandbox_id": "sbx1"})
    assert r.status_code == 200
    d = r.get_json()
    assert d["status"] == "running" and d["sandbox_id"] == "sbx1"
    assert d["started_at"].startswith("2026-08-27T10:00:00")
    r = client.patch(f"/api/wpsandbox/run/{run_id}", json={
        "status": "done", "finished_at": "2026-08-27T10:02:00+00:00", "report_blob_id": "cd" * 32})
    assert r.get_json()["status"] == "done" and r.get_json()["report_blob_id"] == "cd" * 32


def test_patch_rejects_bad_status(client, app):
    run_id = _create(client)
    app.user.login = "wpsandbox-worker"
    assert client.patch(f"/api/wpsandbox/run/{run_id}", json={"status": "weird"}).status_code == 400


def test_delete_queued_run_by_requester(client, app):
    run_id = _create(client)
    r = client.delete(f"/api/wpsandbox/run/{run_id}")
    assert r.status_code == 200
    assert app.redis.lrange("wpsandbox:jobs", 0, -1) == []
    assert client.get(f"/api/wpsandbox/run/{run_id}").status_code == 404


def test_delete_forbidden_for_other_user_without_manage_users(client, app):
    run_id = _create(client)
    app.user.id = 77
    app.user.has_rights = lambda cap: cap != "manage_users"
    assert client.delete(f"/api/wpsandbox/run/{run_id}").status_code == 403


def test_delete_running_run_conflicts(client, app):
    run_id = _create(client)
    row = app.db.session.get(WpSandboxRun, run_id); row.status = "running"; app.db.session.commit()
    assert client.delete(f"/api/wpsandbox/run/{run_id}").status_code == 409
```

- [ ] **Step 2: Run tests**

Run: `cd docker/plugins/wpsandbox && python -m pytest tests/test_resource.py -v`
Expected: 16 passed. (The implementation already exists from Task 4; if any of these fail, fix `resource.py` — do not weaken the test.)

- [ ] **Step 3: Commit**

```bash
git add docker/plugins/wpsandbox/tests/test_resource.py
git commit -m "wpsandbox plugin: tests for run get/patch/delete"
```

---

### Task 6: Entrypoint, attribute definitions, compose wiring, README

**Files:**
- Modify: `docker/plugins/wpsandbox/__init__.py`
- Create: `docker/plugins/wpsandbox/attributes.py`
- Create: `docker/plugins/wpsandbox/README.md`
- Modify: `docker-compose-dev.yml` (`MWDB_PLUGINS`), `docker-compose-prod.yml` (`MWDB_PLUGINS`)
- Test: `docker/plugins/wpsandbox/tests/test_entrypoint.py`

**Interfaces:**
- Produces: `entrypoint(app_context)` registering `WpSandboxRunListResource` at `/wpsandbox/<hash64:identifier>` and `WpSandboxRunResource` at `/wpsandbox/run/<run_id>`; `attributes.ATTRIBUTE_KEYS = ("c2_host", "dropped_file", "wp_user_added")`; `attributes.ensure_attribute_definitions() -> None` creating missing `AttributeDefinition` rows (Stage 3's worker sets these attributes via HTTP and relies on their existence).

- [ ] **Step 1: Write the failing test**

`docker/plugins/wpsandbox/tests/test_entrypoint.py`:
```python
from unittest.mock import MagicMock


def test_entrypoint_registers_resources(monkeypatch):
    import wpsandbox
    monkeypatch.setattr("wpsandbox.model.ensure_schema", lambda: True)
    monkeypatch.setattr("wpsandbox.attributes.ensure_attribute_definitions", lambda: None)
    ctx = MagicMock()
    wpsandbox.entrypoint(ctx)
    urls = [c.args[1] for c in ctx.register_resource.call_args_list]
    assert "/wpsandbox/<hash64:identifier>" in urls
    assert "/wpsandbox/run/<run_id>" in urls


def test_ensure_attribute_definitions_creates_missing(monkeypatch):
    from wpsandbox import attributes

    existing = {"c2_host"}
    added = []
    committed = []

    class FakeSession:
        def add(self, obj):
            added.append(obj.key)

        def commit(self):
            committed.append(True)

    class FakeDef:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    monkeypatch.setattr(attributes, "_session", lambda: FakeSession())
    monkeypatch.setattr(attributes, "_definition_cls", lambda: FakeDef)
    monkeypatch.setattr(attributes, "_definition_exists", lambda session, cls, key: key in existing)
    attributes.ensure_attribute_definitions()
    assert sorted(added) == ["dropped_file", "wp_user_added"]
    assert committed == [True]


def test_ensure_attribute_definitions_noop_when_all_exist(monkeypatch):
    from wpsandbox import attributes

    class FakeSession:
        def add(self, obj):
            raise AssertionError("should not add")

        def commit(self):
            raise AssertionError("should not commit")

    monkeypatch.setattr(attributes, "_session", lambda: FakeSession())
    monkeypatch.setattr(attributes, "_definition_cls", lambda: object)
    monkeypatch.setattr(attributes, "_definition_exists", lambda session, cls, key: True)
    attributes.ensure_attribute_definitions()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd docker/plugins/wpsandbox && python -m pytest tests/test_entrypoint.py -v`
Expected: FAIL (`entrypoint` / `wpsandbox.attributes` missing)

- [ ] **Step 3: Implement attributes + entrypoint**

`docker/plugins/wpsandbox/attributes.py`:
```python
"""Attribute definitions the worker populates from reports."""
from . import logger

ATTRIBUTE_KEYS = ("c2_host", "dropped_file", "wp_user_added")
DESCRIPTIONS = {
    "c2_host": "Host contacted by the sample during WP sandbox execution",
    "dropped_file": "SHA256 of a file created by the sample during WP sandbox execution",
    "wp_user_added": "WordPress user login created by the sample during WP sandbox execution",
}


def _session():
    from mwdb.model import db
    return db.session


def _definition_cls():
    from mwdb.model.attribute import AttributeDefinition
    return AttributeDefinition


def _definition_exists(session, AttributeDefinition, key: str) -> bool:
    return (
        session.query(AttributeDefinition)
        .filter(AttributeDefinition.key == key)
        .first()
        is not None
    )


def ensure_attribute_definitions() -> None:
    session = _session()
    AttributeDefinition = _definition_cls()
    created = False
    for key in ATTRIBUTE_KEYS:
        if _definition_exists(session, AttributeDefinition, key):
            continue
        session.add(
            AttributeDefinition(
                key=key,
                label=key,
                description=DESCRIPTIONS[key],
                url_template="",
                rich_template="",
                example_value="",
            )
        )
        created = True
        logger.info("wpsandbox: created attribute definition %s", key)
    if created:
        session.commit()
```

`docker/plugins/wpsandbox/__init__.py` (final):
```python
"""WordPress sandbox plugin — runs samples in a SecEx WordPress microVM
and attaches behavioural reports to the sample."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mwdb.core.plugins import PluginAppContext

__author__ = "Waffle Makers"
__version__ = "0.1.0"
__doc__ = "WordPress sandbox (SecEx) plugin for the MWDB sample detail page."

logger = logging.getLogger("mwdb.plugin.wpsandbox")


def entrypoint(app_context: "PluginAppContext") -> None:
    # Deferred imports: this package is loaded before the MWDB app finishes booting.
    from . import attributes, model
    from .resource import WpSandboxRunListResource, WpSandboxRunResource

    try:
        model.ensure_schema()
        attributes.ensure_attribute_definitions()
    except Exception as e:  # schema may not exist yet during `mwdb-core configure`
        logger.warning("wpsandbox: deferred setup until first request: %s", e)

    app_context.register_resource(WpSandboxRunListResource, "/wpsandbox/<hash64:identifier>")
    app_context.register_resource(WpSandboxRunResource, "/wpsandbox/run/<run_id>")
    logger.info("Registered /api/wpsandbox/<sample> and /api/wpsandbox/run/<id>")


__plugin_entrypoint__ = entrypoint
```

Also add to `resource.py`, inside `_ensure_schema_once()` after `ensure_schema()` succeeds, a call to `attributes.ensure_attribute_definitions()` wrapped in `try/except Exception` (so the lazy path also creates definitions). Import: `from . import attributes`.

- [ ] **Step 4: Run all plugin tests**

Run: `cd docker/plugins/wpsandbox && python -m pytest tests/ -v`
Expected: all pass

- [ ] **Step 5: Compose wiring**

In `docker-compose-dev.yml` change `MWDB_PLUGINS: "yarax_regex,phpdeobf"` → `MWDB_PLUGINS: "yarax_regex,phpdeobf,wpsandbox"`.
In `docker-compose-prod.yml` change `- MWDB_PLUGINS=yarax_regex,phpdeobf` → `- MWDB_PLUGINS=yarax_regex,phpdeobf,wpsandbox`.
(Worker env/service is added in Stage 3.)

- [ ] **Step 6: README**

`docker/plugins/wpsandbox/README.md`:
```markdown
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
```

- [ ] **Step 7: Smoke test in the dev stack**

Run: `docker compose -f docker-compose-dev.yml up --build -d mwdb && docker compose -f docker-compose-dev.yml logs mwdb | grep -i wpsandbox`
Expected: `Registered /api/wpsandbox/<sample> and /api/wpsandbox/run/<id>` and no traceback. Then, with an admin token: `curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1/api/wpsandbox/<sha256 of any uploaded sample>` → `{"runs": []}`.

- [ ] **Step 8: Commit**

```bash
git add docker/plugins/wpsandbox docker-compose-dev.yml docker-compose-prod.yml
git commit -m "wpsandbox plugin: entrypoint, attribute definitions, compose registration"
```

---

## Self-review

- **Spec coverage:** §5.1 model + derived worker-lost (Task 2); §5.2 all five endpoints with the specified status codes and auth rules (Tasks 4–5); duplicate detection on normalised params (Task 1 + 4); §5.3 config + attribute definitions (Tasks 1, 6); compose `MWDB_PLUGINS` (Task 6). §5.4 (tab) is Stage 4.
- **Placeholder scan:** none.
- **Type consistency:** `normalize_params(body, *, max_timeout, sample_sha256, sample_name) -> (mode, params)` used in Task 4 as defined in Task 1; `JobQueue.push/remove` in Task 3/4; `WpSandboxRun.new/effective_status/to_dict` in 2/4/5; `ensure_schema() -> bool` in 2/4/6.
