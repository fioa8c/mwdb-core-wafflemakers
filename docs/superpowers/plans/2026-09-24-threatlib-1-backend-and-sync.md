# Threat Library Plugin — Part 1: Backend and Sync — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `threatlib` MWDB plugin (Threat entity, REST API, tag/attribute mirror) and the `threatlib-sync` sidecar that ingests hand-made repo changes into MWDB and regenerates the four malware directories of `jetpack-threat-library` from MWDB, pushing to `trunk`.

**Architecture:** One in-tree plugin directory `docker/plugins/threatlib/` (pip-installed into the backend image) owns two tables created idempotently at entrypoint, a `service.py` layer used by both the Flask-RESTful resources and the sync, and a `sync/` package exposed as the console script `threatlib-sync`. The sync runs in its own container built from a copy of the backend image with `git` added, talks to the database directly, and drives git through `subprocess`. Every sync-side behaviour is unit-tested against an in-memory SQLite stand-in for `mwdb.model.db`, a `FakeStore` in place of `File`, and a local bare git repository as the remote. Part 2 (frontend) is a separate plan.

**Tech Stack:** Python ≥ 3.10, Flask 3, Flask-RESTful, SQLAlchemy 1.4 (classic queries), pytest, git CLI, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-24-threatlib-sync-and-curation-design.md`

## Global Constraints

- Python ≥ 3.10; SQLAlchemy 1.4 (no 2.0-style `select()` in plugin code); Flask 3; do not add dependencies to the root `pyproject.toml`.
- Plugin Python package name: `threatlib`; installed from `docker/plugins/threatlib/` (dir has `pyproject.toml` with `package-dir = {"threatlib" = "."}`), so imports are `from threatlib import ...`.
- Lint: CI runs `ruff check` (`extend-select = ["I"]`) and `ruff format --check` over the repo, which includes `docker/plugins/`. Run both before every commit.
- Plugin unit tests need no stack. Run with `cd docker/plugins/threatlib && python -m pytest tests/ -v` using a Python ≥ 3.10 venv (`/opt/homebrew/bin/python3.14 -m venv .venv` is known to work; system python is 3.9). Dev deps: `pytest>=7`, `flask>=3`, `sqlalchemy>=1.4,<2`.
- Categories (exact strings): `threats`, `for-later-review`, `webshells`, `escalated_issues_samples`.
- Attribute key mirrored on samples: `jpop_threat_name`. Category is mirrored as a tag with the category string as the tag.
- Threat name regex: `^[A-Za-z0-9._-]+$`, not `.` or `..`, max 255 chars. `rel_path`: POSIX, relative, no empty segment, no `.` or `..` segment, no leading `/`, max 1024 chars.
- Manifest file name at repo root: `.mwdb-threatlib.json`.
- API prefix: `/api/threatlib/` (plugin registers paths without the `/api` prefix; `api.add_resource` adds it).
- Capabilities: `adding_files` for create/link/edit/upload; `removing_objects` for threat deletion. No new capability.
- Sidecar env: `MWDB_THREATLIB_REPO_URL`, `MWDB_THREATLIB_DEPLOY_KEY`, `MWDB_THREATLIB_SYNC_INTERVAL` (default `900`), `MWDB_THREATLIB_PUSH` (default `1`), `MWDB_THREATLIB_GIT_AUTHOR` (default `mwdb-threatlib-bot <noreply@wafflemakers.xyz>`), `MWDB_THREATLIB_CLONE_DIR` (default `/data/repo`), `MWDB_THREATLIB_SHARE_WITH` (group name, default `public`), `MWDB_ENABLE_HOOKS=0`.
- `MWDB_PLUGINS` must list `threatlib` in **both** `compose/compose.with-plugins.yml` and `docker-compose-prod.yml`, for the `mwdb` service and the `threatlib-sync` service.
- Ownership rule for the export (spec §4 step 5, refined by repo inspection): for every category, the export owns every **subdirectory** of `<category>/`; for `webshells` it additionally owns the regular files directly under `webshells/`. Regular files directly under `threats/`, `for-later-review/`, `escalated_issues_samples/` (e.g. `threats/README.md`, `threats/readme-builder.php`) are never ingested and never deleted.
- Round-trip fidelity rules (from repo inspection): dotfiles are samples; a `README.md` is the threat README **only** at `<category>/<name>/README.md`, any deeper `README.md` is a sample; empty files are linked with `object_id = NULL` and exported as empty files; a regular file directly under `webshells/` becomes a threat with `flat = true` whose name is the file stem and whose single link has `rel_path` = the file name.

---

## File Structure

**New — plugin package `docker/plugins/threatlib/`:**

| file | responsibility |
|---|---|
| `pyproject.toml` | package metadata, `[project.scripts] threatlib-sync = "threatlib.sync.runner:main"` |
| `__init__.py` | `entrypoint(app_context)`: ensure schema + attribute definition, register resources |
| `model.py` | `CATEGORIES`, `Threat`, `ThreatSample`, `ensure_schema()`, `utcnow()`, `iso()` |
| `validation.py` | `ValidationError`, `validate_name`, `validate_rel_path`, `validate_category` |
| `attributes.py` | `ATTRIBUTE_KEY`, `ensure_attribute_definition()` |
| `mirror.py` | `apply_mirror`, `remove_mirror` (tag + attribute on a `File`) |
| `service.py` | all writes to the two tables + mirror orchestration; `NameConflict`, `PathConflict`, `_load_file` |
| `resource.py` | Flask-RESTful resources for `/threatlib/...` |
| `sync/__init__.py` | empty |
| `sync/manifest.py` | `Manifest` dataclass, `load_manifest`, `save_manifest`, `sha256_bytes` |
| `sync/store.py` | `ObjectStore` protocol, `MwdbStore` |
| `sync/repo.py` | `GitRepo`: clone/reset/status/commit/push via `subprocess` |
| `sync/ingest.py` | `ingest(repo_path, manifest, store) -> IngestStats` |
| `sync/export.py` | `export(repo_path, store) -> tuple[Manifest, ExportStats]` |
| `sync/runner.py` | `SyncConfig.from_env()`, `run_once()`, `main()` |
| `README.md` | plugin docs: API, sync loop, env, tests, rollout |
| `tests/conftest.py` | SQLite stand-in for `mwdb.model.db`, stubs for `mwdb.*`, `FakeStore`, `FakeFile` |
| `tests/test_model.py`, `test_validation.py`, `test_mirror.py`, `test_service.py`, `test_resource_threat.py`, `test_resource_samples.py`, `test_manifest.py`, `test_repo.py`, `test_ingest.py`, `test_export.py`, `test_runner.py` | unit tests per module |

**New — sidecar image:** `docker/threatlib-sync/Dockerfile` (copy of `deploy/docker/Dockerfile` with `git openssh-client` added to the runtime stage and `CMD ["threatlib-sync"]`).

**Modified:**
- `compose/compose.with-plugins.yml` — add `threatlib` to `MWDB_PLUGINS`; add `threatlib-sync` service (dev).
- `docker-compose-prod.yml` — same for prod, plus `threatlib-repo` volume.
- `CLAUDE.md` — list the plugin and sidecar under "Fork-specific additions".
- `deploy/DEPLOYMENT.md` — rollout steps (spec §8).
- `tests/backend/test_threatlib.py` — e2e over HTTP (needs the stack).

---

### Task 1: Plugin skeleton, test harness and data model

**Files:**
- Create: `docker/plugins/threatlib/pyproject.toml`
- Create: `docker/plugins/threatlib/__init__.py`
- Create: `docker/plugins/threatlib/model.py`
- Create: `docker/plugins/threatlib/tests/__init__.py` (empty)
- Create: `docker/plugins/threatlib/tests/conftest.py`
- Test: `docker/plugins/threatlib/tests/test_model.py`

**Interfaces:**
- Produces: `model.CATEGORIES: tuple[str, ...]`; `model.Threat` (columns `id, name, category, readme, flat, created_by, created_at, updated_at`, relationship `samples`); `model.ThreatSample` (PK `(threat_id, rel_path)`, `object_id: int | None`, `added_at`, relationship `threat`); `model.ensure_schema() -> bool`; `model.utcnow() -> datetime`; `model.iso(dt) -> str | None`.
- Produces (tests): `conftest.FakeFile`, `conftest.FakeStore`, fixture `schema` (autouse per test: creates tables, truncates after).

- [ ] **Step 1: Create the package metadata**

`docker/plugins/threatlib/pyproject.toml`:

```toml
[project]
name = "threatlib"
version = "0.1.0"
description = "Threat library plugin for MWDB: Threat entity, curation API and repo sync"
requires-python = ">=3.10"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=7", "flask>=3", "sqlalchemy>=1.4,<2"]

[project.scripts]
threatlib-sync = "threatlib.sync.runner:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["threatlib", "threatlib.sync"]
package-dir = {"threatlib" = ".", "threatlib.sync" = "sync"}
```

- [ ] **Step 2: Create the entrypoint (resources are registered in Tasks 5 and 6)**

`docker/plugins/threatlib/__init__.py`:

```python
"""Threat library plugin — first-class Threat entity, curation API and
jetpack-threat-library repo sync."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mwdb.core.plugins import PluginAppContext

__author__ = "Waffle Makers"
__version__ = "0.1.0"
__doc__ = "Threat library plugin (Threat entity, curation API, repo sync)."

logger = logging.getLogger("mwdb.plugin.threatlib")


def entrypoint(app_context: "PluginAppContext") -> None:
    # Deferred imports: this package is loaded before the MWDB app finishes booting.
    from . import attributes, model

    try:
        model.ensure_schema()
        attributes.ensure_attribute_definition()
    except Exception as e:  # schema may not exist yet during `mwdb-core configure`
        logger.warning("threatlib: deferred setup until first request: %s", e)
        try:
            from mwdb.model import db

            db.session.rollback()
        except Exception:
            pass

    _register_resources(app_context)


def _register_resources(app_context: "PluginAppContext") -> None:
    # Filled in by later tasks (resource registration).
    return None


__plugin_entrypoint__ = entrypoint
```

- [ ] **Step 3: Write the test harness**

Pytest names this conftest `threatlib.tests.conftest` (the plugin dir is a
package, `docker/plugins/` is not), so test modules cannot `from conftest
import ...`. The SQLite stand-in and the fakes therefore live in
`tests/fakes.py`, imported everywhere as `from threatlib.tests.fakes import ...`.

`docker/plugins/threatlib/tests/conftest.py`:

```python
"""Shared pytest fixtures for the threatlib plugin.

Stubs the `mwdb` package so plugin modules import without the full stack;
`tests/fakes.py` then replaces `mwdb.model.db` with a real SQLAlchemy
declarative base bound to in-memory SQLite. Mirrors wpsandbox/tests/conftest.py.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def _identity_decorator(f):
    return f


def _capability_decorator(*caps):
    return _identity_decorator


if "mwdb" not in sys.modules:
    sys.modules["mwdb"] = MagicMock()

if "mwdb.resources" not in sys.modules:
    _stub = MagicMock()
    _stub.requires_authorization = _identity_decorator
    _stub.requires_capabilities = _capability_decorator
    _stub.get_shares_for_upload = lambda upload_as: []
    sys.modules["mwdb.resources"] = _stub
    sys.modules["mwdb"].resources = _stub

if "mwdb.model" not in sys.modules:
    _stub = MagicMock()
    sys.modules["mwdb.model"] = _stub
    sys.modules["mwdb"].model = _stub

if "mwdb.model.file" not in sys.modules:
    _stub = MagicMock()

    class EmptyFileError(ValueError):
        pass

    _stub.EmptyFileError = EmptyFileError
    sys.modules["mwdb.model.file"] = _stub
    sys.modules["mwdb.model"].file = _stub

if "mwdb.model.attribute" not in sys.modules:
    _stub = MagicMock()
    sys.modules["mwdb.model.attribute"] = _stub
    sys.modules["mwdb.model"].attribute = _stub

if "mwdb.core" not in sys.modules:
    _stub = MagicMock()
    sys.modules["mwdb.core"] = _stub
    sys.modules["mwdb"].core = _stub

if "mwdb.core.service" not in sys.modules:
    from flask.views import MethodView

    class _FakeResource(MethodView):
        pass

    _stub = MagicMock()
    _stub.Resource = _FakeResource
    sys.modules["mwdb.core.service"] = _stub
    sys.modules["mwdb.core"].service = _stub

if "mwdb.core.hooks" not in sys.modules:
    _stub = MagicMock()
    sys.modules["mwdb.core.hooks"] = _stub
    sys.modules["mwdb.core"].hooks = _stub

if "mwdb.core.capabilities" not in sys.modules:
    _caps = MagicMock()
    _caps.Capabilities.adding_files = "adding_files"
    _caps.Capabilities.removing_objects = "removing_objects"
    sys.modules["mwdb.core.capabilities"] = _caps
    sys.modules["mwdb.core"].capabilities = _caps

if "mwdb.cli" not in sys.modules:
    _stub = MagicMock()
    sys.modules["mwdb.cli"] = _stub
    sys.modules["mwdb.cli.base"] = MagicMock()
    sys.modules["mwdb"].cli = _stub

# Installs the SQLite stand-in for mwdb.model.db (must run after the stubs).
from threatlib.tests.fakes import _Session  # noqa: E402


@pytest.fixture(autouse=True)
def schema():
    from threatlib import model

    assert model.ensure_schema() is True
    yield
    _Session.rollback()
    _Session.query(model.ThreatSample).delete()
    _Session.query(model.Threat).delete()
    _Session.execute(sa.text("DELETE FROM object"))
    _Session.commit()


@pytest.fixture
def store(monkeypatch):
    from threatlib import service
    from threatlib.tests.fakes import FakeStore

    s = FakeStore()
    monkeypatch.setattr(service, "_load_file", s.load)
    return s
```

`docker/plugins/threatlib/tests/fakes.py`:

```python
"""SQLite stand-in for mwdb.model.db plus FakeFile / FakeStore.

Imported by conftest.py after the mwdb stubs are in place, and by tests as
`from threatlib.tests.fakes import FakeFile, FakeStore`.
"""

import hashlib
import io
import sys

import sqlalchemy as sa
from sqlalchemy.orm import declarative_base, relationship, scoped_session, sessionmaker

_engine = sa.create_engine("sqlite://")
_Base = declarative_base()
_Session = scoped_session(sessionmaker(bind=_engine))


class _FakeDB:
    Model = _Base
    Column = sa.Column
    Integer = sa.Integer
    String = sa.String
    Text = sa.Text
    Boolean = sa.Boolean
    DateTime = sa.DateTime
    ForeignKey = sa.ForeignKey
    relationship = staticmethod(relationship)
    engine = _engine
    session = _Session


sys.modules["mwdb.model"].db = _FakeDB

# Minimal `object` table so FKs resolve and FakeStore can insert rows.
sa.Table(
    "object",
    _Base.metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("dhash", sa.String(64), unique=True),
)
_Base.metadata.create_all(_engine, tables=[_Base.metadata.tables["object"]])


class FakeFile:
    """Stand-in for mwdb.model.File exposing what the plugin calls."""

    def __init__(self, object_id: int, content: bytes, file_name: str):
        self.id = object_id
        self.content = content
        self.sha256 = hashlib.sha256(content).hexdigest()
        self.dhash = self.sha256
        self.file_name = file_name
        self.file_size = len(content)
        self.tags: set[str] = set()
        self.attributes: dict[str, set[str]] = {}
        self.calls: list[tuple] = []

    # --- mwdb.model.Object API used by mirror.py ---
    def get_tag(self, tag):
        return tag if tag in self.tags else None

    def add_tag(self, tag, commit=True):
        self.calls.append(("add_tag", tag))
        new = tag not in self.tags
        self.tags.add(tag)
        return new

    def remove_tag(self, tag, commit=True):
        self.calls.append(("remove_tag", tag))
        existed = tag in self.tags
        self.tags.discard(tag)
        return existed

    def add_attribute(self, key, value, commit=True, check_permissions=True):
        self.calls.append(("add_attribute", key, value))
        values = self.attributes.setdefault(key, set())
        new = value not in values
        values.add(value)
        return new

    def remove_attribute(self, key, value, check_permissions=True):
        self.calls.append(("remove_attribute", key, value))
        values = self.attributes.get(key, set())
        existed = value in values
        values.discard(value)
        return existed

    def has_explicit_access(self, user):
        return True

    def read(self):
        return self.content

    def release_after_upload(self):
        pass


class FakeStore:
    """In-memory ObjectStore (see threatlib/sync/store.py) backed by the
    SQLite `object` table so link rows get real object ids."""

    def __init__(self):
        self.by_sha: dict[str, FakeFile] = {}
        self.by_id: dict[int, FakeFile] = {}

    def get_or_create(self, file_name, stream):
        content = stream.read()
        if len(content) == 0:
            from mwdb.model.file import EmptyFileError

            raise EmptyFileError()
        sha = hashlib.sha256(content).hexdigest()
        if sha in self.by_sha:
            return self.by_sha[sha], False
        res = _Session.execute(
            sa.text("INSERT INTO object (dhash) VALUES (:d)"), {"d": sha}
        )
        _Session.commit()
        obj = FakeFile(res.lastrowid, content, file_name)
        self.by_sha[sha] = obj
        self.by_id[obj.id] = obj
        return obj, True

    def load(self, object_id):
        return self.by_id.get(object_id)

    def read(self, object_id):
        return self.by_id[object_id].content

    def add(self, content: bytes, file_name: str = "x.php") -> FakeFile:
        obj, _ = self.get_or_create(file_name, io.BytesIO(content))
        return obj
```

The `store` fixture monkeypatches `service._load_file`, which Task 4 defines; tests before Task 4 do not use that fixture, and `monkeypatch` restores the attribute after each test.

- [ ] **Step 4: Write the failing model tests**

`docker/plugins/threatlib/tests/test_model.py`:

```python
from datetime import timezone

import sqlalchemy as sa

from threatlib import model
from threatlib.model import CATEGORIES, Threat, ThreatSample, ensure_schema, iso, utcnow


def test_categories_are_the_four_repo_dirs():
    assert CATEGORIES == (
        "threats",
        "for-later-review",
        "webshells",
        "escalated_issues_samples",
    )


def test_ensure_schema_is_idempotent():
    assert ensure_schema() is True
    assert ensure_schema() is True


def test_threat_and_link_persist_and_cascade():
    from mwdb.model import db

    now = utcnow()
    t = Threat(name="FIO-1", category="threats", created_at=now, updated_at=now)
    db.session.add(t)
    db.session.flush()
    db.session.add(ThreatSample(threat_id=t.id, rel_path="a.php", object_id=None, added_at=now))
    db.session.add(ThreatSample(threat_id=t.id, rel_path="sub/b.php", object_id=None, added_at=now))
    db.session.commit()

    loaded = db.session.query(Threat).filter(Threat.name == "FIO-1").one()
    assert sorted(s.rel_path for s in loaded.samples) == ["a.php", "sub/b.php"]
    assert loaded.flat is False

    db.session.delete(loaded)
    db.session.commit()
    assert db.session.query(ThreatSample).count() == 0


def test_name_is_unique():
    from mwdb.model import db

    now = utcnow()
    db.session.add(Threat(name="dup", category="threats", created_at=now, updated_at=now))
    db.session.commit()
    db.session.add(Threat(name="dup", category="threats", created_at=now, updated_at=now))
    try:
        db.session.commit()
        assert False, "expected IntegrityError"
    except sa.exc.IntegrityError:
        db.session.rollback()


def test_iso_adds_utc_when_naive():
    dt = utcnow().replace(tzinfo=None)
    assert iso(dt).endswith("+00:00")
    assert iso(None) is None
    assert utcnow().tzinfo == timezone.utc


def test_ensure_schema_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(model.Threat.__table__, "create", boom)
    assert ensure_schema() is False
```

- [ ] **Step 5: Run the tests to verify they fail**

```bash
cd docker/plugins/threatlib && python -m pytest tests/test_model.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'threatlib.model'`.

- [ ] **Step 6: Write the model**

`docker/plugins/threatlib/model.py`:

```python
"""threatlib_threat / threatlib_threat_sample tables.

Created idempotently at plugin entrypoint (MWDB has no plugin migrations).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from mwdb.model import db

logger = logging.getLogger("mwdb.plugin.threatlib")

CATEGORIES = ("threats", "for-later-review", "webshells", "escalated_issues_samples")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class Threat(db.Model):
    __tablename__ = "threatlib_threat"

    id = db.Column(db.Integer, primary_key=True)
    # Repo folder name (or file stem for flat webshells).
    name = db.Column(db.String(255), unique=True, nullable=False, index=True)
    category = db.Column(db.String(32), nullable=False, index=True)
    readme = db.Column(db.Text, nullable=True)
    # True only for single-file threats living directly under webshells/.
    flat = db.Column(db.Boolean, nullable=False, default=False)
    created_by = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False)

    samples = db.relationship(
        "ThreatSample",
        back_populates="threat",
        cascade="all, delete-orphan",
        lazy="select",
    )


class ThreatSample(db.Model):
    __tablename__ = "threatlib_threat_sample"

    threat_id = db.Column(
        db.Integer,
        db.ForeignKey("threatlib_threat.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Path inside the threat folder, POSIX separators, e.g. "0154/wp-admin/menu.php".
    rel_path = db.Column(db.String(1024), primary_key=True)
    # NULL means "an empty file" (MWDB cannot store zero-byte objects).
    object_id = db.Column(
        db.Integer,
        db.ForeignKey("object.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    added_at = db.Column(db.DateTime(timezone=True), nullable=False)

    threat = db.relationship("Threat", back_populates="samples")


def ensure_schema() -> bool:
    try:
        Threat.__table__.create(bind=db.engine, checkfirst=True)
        ThreatSample.__table__.create(bind=db.engine, checkfirst=True)
        return True
    except Exception as e:  # pragma: no cover - exercised via monkeypatch
        logger.warning("threatlib: could not create tables yet: %s", e)
        return False
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
cd docker/plugins/threatlib && python -m pytest tests/test_model.py -v
```

Expected: 6 passed.

- [ ] **Step 8: Lint and commit**

```bash
ruff check --fix docker/plugins/threatlib && ruff format docker/plugins/threatlib
git add docker/plugins/threatlib
git commit -m "threatlib: plugin skeleton, test harness and Threat/ThreatSample model

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Validation

**Files:**
- Create: `docker/plugins/threatlib/validation.py`
- Test: `docker/plugins/threatlib/tests/test_validation.py`

**Interfaces:**
- Produces: `validation.ValidationError(Exception)` with `.message`; `validate_name(name: str) -> str`; `validate_rel_path(rel_path: str) -> str` (returns the normalised POSIX path); `validate_category(category: str) -> str`.

- [ ] **Step 1: Write the failing tests**

`docker/plugins/threatlib/tests/test_validation.py`:

```python
import pytest

from threatlib.validation import (
    ValidationError,
    validate_category,
    validate_name,
    validate_rel_path,
)


@pytest.mark.parametrize("name", ["FIO-7243", "php_uploader_generic_019_2", "sources", "a.b", "_x"])
def test_valid_names(name):
    assert validate_name(name) == name


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a b", "a\\b", "ü", "x" * 256, None])
def test_invalid_names(name):
    with pytest.raises(ValidationError):
        validate_name(name)


@pytest.mark.parametrize(
    "rel_path,expected",
    [
        ("1.php", "1.php"),
        ("0154/wp-admin/menu.php", "0154/wp-admin/menu.php"),
        ("./a/./b.php", "a/b.php"),
        (".hidden", ".hidden"),
        ("dir/README.md", "dir/README.md"),
        ("with space.php", "with space.php"),
    ],
)
def test_valid_rel_paths(rel_path, expected):
    assert validate_rel_path(rel_path) == expected


@pytest.mark.parametrize(
    "rel_path", ["", "/abs.php", "../x.php", "a/../../x.php", "a//b.php", "a/", "x" * 1025, None, "a\\b.php"]
)
def test_invalid_rel_paths(rel_path):
    with pytest.raises(ValidationError):
        validate_rel_path(rel_path)


def test_categories():
    assert validate_category("for-later-review") == "for-later-review"
    with pytest.raises(ValidationError):
        validate_category("false-positives")
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd docker/plugins/threatlib && python -m pytest tests/test_validation.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'threatlib.validation'`.

- [ ] **Step 3: Implement validation**

`docker/plugins/threatlib/validation.py`:

```python
"""Input validation for names and paths that become filesystem paths in the export."""

from __future__ import annotations

import posixpath
import re

from .model import CATEGORIES

NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
MAX_NAME_LEN = 255
MAX_REL_PATH_LEN = 1024


class ValidationError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def validate_name(name) -> str:
    if not isinstance(name, str) or not name:
        raise ValidationError("Threat name is required")
    if len(name) > MAX_NAME_LEN:
        raise ValidationError(f"Threat name longer than {MAX_NAME_LEN} characters")
    if name in (".", ".."):
        raise ValidationError("Threat name cannot be '.' or '..'")
    if not NAME_RE.match(name):
        raise ValidationError("Threat name may contain only A-Z a-z 0-9 . _ -")
    return name


def validate_rel_path(rel_path) -> str:
    if not isinstance(rel_path, str) or not rel_path:
        raise ValidationError("rel_path is required")
    if len(rel_path) > MAX_REL_PATH_LEN:
        raise ValidationError(f"rel_path longer than {MAX_REL_PATH_LEN} characters")
    if "\\" in rel_path or "\x00" in rel_path:
        raise ValidationError("rel_path must use '/' separators")
    if rel_path.startswith("/"):
        raise ValidationError("rel_path must be relative")
    if rel_path.endswith("/"):
        raise ValidationError("rel_path must name a file")
    if "//" in rel_path:
        raise ValidationError("rel_path contains an empty segment")
    segments = rel_path.split("/")
    if any(seg == ".." for seg in segments):
        raise ValidationError("rel_path cannot contain '..'")
    normalised = posixpath.normpath(rel_path)
    if normalised in (".", "") or normalised.startswith("../"):
        raise ValidationError("rel_path is invalid")
    return normalised


def validate_category(category) -> str:
    if category not in CATEGORIES:
        raise ValidationError(f"category must be one of {', '.join(CATEGORIES)}")
    return category
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd docker/plugins/threatlib && python -m pytest tests/test_validation.py -v
```

Expected: all passed.

- [ ] **Step 5: Lint and commit**

```bash
ruff check --fix docker/plugins/threatlib && ruff format docker/plugins/threatlib
git add docker/plugins/threatlib
git commit -m "threatlib: name, rel_path and category validation

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Attribute definition and tag/attribute mirror

**Files:**
- Create: `docker/plugins/threatlib/attributes.py`
- Create: `docker/plugins/threatlib/mirror.py`
- Test: `docker/plugins/threatlib/tests/test_mirror.py`

**Interfaces:**
- Produces: `attributes.ATTRIBUTE_KEY = "jpop_threat_name"`; `attributes.ensure_attribute_definition() -> None`.
- Produces: `mirror.apply_mirror(file_obj, category: str, name: str) -> None`; `mirror.remove_mirror(file_obj, category: str, name: str, remaining_links: list[tuple[str, str]]) -> None` where `remaining_links` is the list of `(category, name)` links the file still has after the unlink.

- [ ] **Step 1: Write the failing tests**

`docker/plugins/threatlib/tests/test_mirror.py`:

```python
from threatlib.attributes import ATTRIBUTE_KEY
from threatlib.mirror import apply_mirror, remove_mirror
from threatlib.tests.fakes import FakeFile


def test_apply_adds_tag_and_attribute_without_commit():
    f = FakeFile(1, b"x", "x.php")
    apply_mirror(f, "threats", "FIO-1")
    assert f.tags == {"threats"}
    assert f.attributes[ATTRIBUTE_KEY] == {"FIO-1"}
    assert ("add_tag", "threats") in f.calls
    assert ("add_attribute", ATTRIBUTE_KEY, "FIO-1") in f.calls


def test_remove_drops_both_when_no_links_remain():
    f = FakeFile(1, b"x", "x.php")
    apply_mirror(f, "threats", "FIO-1")
    remove_mirror(f, "threats", "FIO-1", remaining_links=[])
    assert f.tags == set()
    assert f.attributes[ATTRIBUTE_KEY] == set()


def test_remove_keeps_tag_when_another_link_shares_category():
    f = FakeFile(1, b"x", "x.php")
    apply_mirror(f, "threats", "FIO-1")
    apply_mirror(f, "threats", "FIO-2")
    remove_mirror(f, "threats", "FIO-1", remaining_links=[("threats", "FIO-2")])
    assert f.tags == {"threats"}
    assert f.attributes[ATTRIBUTE_KEY] == {"FIO-2"}


def test_remove_keeps_attribute_when_same_threat_still_linked_by_other_path():
    f = FakeFile(1, b"x", "x.php")
    apply_mirror(f, "threats", "FIO-1")
    remove_mirror(f, "threats", "FIO-1", remaining_links=[("threats", "FIO-1")])
    assert f.tags == {"threats"}
    assert f.attributes[ATTRIBUTE_KEY] == {"FIO-1"}


def test_apply_and_remove_tolerate_none_file():
    apply_mirror(None, "threats", "x")
    remove_mirror(None, "threats", "x", remaining_links=[])
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd docker/plugins/threatlib && python -m pytest tests/test_mirror.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement attributes.py and mirror.py**

`docker/plugins/threatlib/attributes.py`:

```python
"""The jpop_threat_name attribute definition the mirror relies on."""

from __future__ import annotations

import logging

ATTRIBUTE_KEY = "jpop_threat_name"

logger = logging.getLogger("mwdb.plugin.threatlib")


def ensure_attribute_definition() -> None:
    from mwdb.model import db
    from mwdb.model.attribute import AttributeDefinition

    existing = (
        db.session.query(AttributeDefinition)
        .filter(AttributeDefinition.key == ATTRIBUTE_KEY)
        .first()
    )
    if existing:
        return
    db.session.add(
        AttributeDefinition(
            key=ATTRIBUTE_KEY,
            label=ATTRIBUTE_KEY,
            description="Threat name in the jetpack threat library (managed by threatlib)",
            url_template="",
            rich_template="",
            example_value="",
        )
    )
    db.session.commit()
    logger.info("threatlib: created attribute definition %s", ATTRIBUTE_KEY)
```

`docker/plugins/threatlib/mirror.py`:

```python
"""Keeps the category tag and jpop_threat_name attribute on samples in step
with the threatlib tables, so upstream search keeps working."""

from __future__ import annotations

from .attributes import ATTRIBUTE_KEY


def apply_mirror(file_obj, category: str, name: str) -> None:
    if file_obj is None:
        return
    file_obj.add_tag(category, commit=False)
    file_obj.add_attribute(ATTRIBUTE_KEY, name, commit=False, check_permissions=False)


def remove_mirror(
    file_obj, category: str, name: str, remaining_links: list[tuple[str, str]]
) -> None:
    if file_obj is None:
        return
    if not any(c == category for c, _ in remaining_links):
        file_obj.remove_tag(category, commit=False)
    if not any(n == name for _, n in remaining_links):
        file_obj.remove_attribute(ATTRIBUTE_KEY, name, check_permissions=False)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd docker/plugins/threatlib && python -m pytest tests/test_mirror.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Lint and commit**

```bash
ruff check --fix docker/plugins/threatlib && ruff format docker/plugins/threatlib
git add docker/plugins/threatlib
git commit -m "threatlib: attribute definition and tag/attribute mirror

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Service layer

**Files:**
- Create: `docker/plugins/threatlib/service.py`
- Test: `docker/plugins/threatlib/tests/test_service.py`

**Interfaces:**
- Consumes: `model.*`, `validation.*`, `mirror.*`.
- Produces (all in `service.py`):
  - `class NameConflict(Exception)`, `class PathConflict(Exception)`
  - `_default_load_file(object_id: int)` and module attribute `_load_file = _default_load_file` (monkeypatchable)
  - `create_threat(name, category, readme=None, created_by=None, *, flat=False, commit=True) -> Threat`
  - `get_threat(name: str) -> Threat | None`
  - `list_threats(query: str | None = None, category: str | None = None, page: int = 1, per_page: int = 50) -> tuple[list[tuple[Threat, int]], int]`
  - `links_for_object(object_id: int) -> list[tuple[str, str]]`
  - `link_sample(threat, file_obj, rel_path, *, commit=True) -> tuple[ThreatSample, bool]`
  - `unlink_sample(threat, rel_path, *, commit=True) -> bool`
  - `set_category(threat, category, *, commit=True) -> None`
  - `set_readme(threat, readme: str | None, *, commit=True) -> None`
  - `delete_threat(threat, *, commit=True) -> None`
  - `sample_count(threat) -> int`

- [ ] **Step 1: Confirm the harness is in place**

Run `cd docker/plugins/threatlib && python -m pytest tests/ -v` once: Tasks 1–3 must still pass before adding the service (the `store` fixture from `tests/conftest.py` is used from here on).

- [ ] **Step 2: Write the failing tests**

`docker/plugins/threatlib/tests/test_service.py`:

```python
import pytest

from threatlib import service
from threatlib.attributes import ATTRIBUTE_KEY
from threatlib.model import Threat, ThreatSample
from threatlib.validation import ValidationError


def test_create_get_and_conflict():
    t = service.create_threat("FIO-1", "threats", readme="# FIO-1", created_by="admin")
    assert t.id is not None and t.flat is False
    assert service.get_threat("FIO-1") is t
    assert service.get_threat("nope") is None
    with pytest.raises(service.NameConflict):
        service.create_threat("FIO-1", "threats")
    with pytest.raises(ValidationError):
        service.create_threat("bad name", "threats")
    with pytest.raises(ValidationError):
        service.create_threat("ok", "not-a-category")


def test_link_applies_mirror_and_is_idempotent(store):
    t = service.create_threat("FIO-1", "threats")
    f = store.add(b"<?php a();")
    link, created = service.link_sample(t, f, "a.php")
    assert created is True and link.object_id == f.id
    assert f.tags == {"threats"} and f.attributes[ATTRIBUTE_KEY] == {"FIO-1"}
    link2, created2 = service.link_sample(t, f, "a.php")
    assert created2 is False and link2 is link
    assert service.sample_count(t) == 1


def test_link_conflict_on_same_path_other_object(store):
    t = service.create_threat("FIO-1", "threats")
    f1 = store.add(b"one")
    f2 = store.add(b"two")
    service.link_sample(t, f1, "a.php")
    with pytest.raises(service.PathConflict):
        service.link_sample(t, f2, "a.php")


def test_link_normalises_rel_path_and_rejects_bad(store):
    t = service.create_threat("FIO-1", "threats")
    f = store.add(b"x")
    link, _ = service.link_sample(t, f, "./sub/./a.php")
    assert link.rel_path == "sub/a.php"
    with pytest.raises(ValidationError):
        service.link_sample(t, f, "../a.php")


def test_link_empty_file_has_null_object():
    t = service.create_threat("FIO-1", "threats")
    link, created = service.link_sample(t, None, "empty.php")
    assert created is True and link.object_id is None


def test_unlink_removes_mirror_only_when_no_links_remain(store):
    t1 = service.create_threat("FIO-1", "threats")
    t2 = service.create_threat("FIO-2", "threats")
    f = store.add(b"x")
    service.link_sample(t1, f, "a.php")
    service.link_sample(t2, f, "b.php")
    assert service.links_for_object(f.id) == [("threats", "FIO-1"), ("threats", "FIO-2")]

    assert service.unlink_sample(t1, "a.php") is True
    assert f.tags == {"threats"} and f.attributes[ATTRIBUTE_KEY] == {"FIO-2"}
    assert service.unlink_sample(t1, "a.php") is False

    assert service.unlink_sample(t2, "b.php") is True
    assert f.tags == set() and f.attributes[ATTRIBUTE_KEY] == set()


def test_set_category_swaps_tag_on_all_samples(store):
    t = service.create_threat("FIO-1", "for-later-review")
    f1 = store.add(b"one")
    f2 = store.add(b"two")
    service.link_sample(t, f1, "a.php")
    service.link_sample(t, f2, "b.php")
    service.set_category(t, "threats")
    assert t.category == "threats"
    assert f1.tags == {"threats"} and f2.tags == {"threats"}
    with pytest.raises(ValidationError):
        service.set_category(t, "nope")


def test_set_category_keeps_old_tag_if_another_threat_uses_it(store):
    t1 = service.create_threat("FIO-1", "for-later-review")
    t2 = service.create_threat("FIO-2", "for-later-review")
    f = store.add(b"x")
    service.link_sample(t1, f, "a.php")
    service.link_sample(t2, f, "a.php")
    service.set_category(t1, "threats")
    assert f.tags == {"threats", "for-later-review"}


def test_set_readme_touches_updated_at():
    t = service.create_threat("FIO-1", "threats")
    before = t.updated_at
    service.set_readme(t, "# new")
    assert t.readme == "# new" and t.updated_at >= before


def test_delete_threat_unlinks_with_mirror_and_keeps_object(store):
    from mwdb.model import db

    t = service.create_threat("FIO-1", "threats")
    f = store.add(b"x")
    service.link_sample(t, f, "a.php")
    service.delete_threat(t)
    assert service.get_threat("FIO-1") is None
    assert db.session.query(ThreatSample).count() == 0
    assert f.tags == set()
    assert store.load(f.id) is f


def test_list_threats_filters_and_counts(store):
    a = service.create_threat("alpha", "threats")
    service.create_threat("alpine", "for-later-review")
    service.create_threat("beta", "threats")
    service.link_sample(a, store.add(b"1"), "1.php")
    service.link_sample(a, store.add(b"2"), "2.php")

    items, total = service.list_threats()
    assert total == 3
    assert [(t.name, n) for t, n in items] == [("alpha", 2), ("alpine", 0), ("beta", 0)]

    items, total = service.list_threats(query="alp")
    assert total == 2 and [t.name for t, _ in items] == ["alpha", "alpine"]

    items, total = service.list_threats(category="for-later-review")
    assert total == 1 and items[0][0].name == "alpine"

    items, total = service.list_threats(page=2, per_page=2)
    assert total == 3 and [t.name for t, _ in items] == ["beta"]
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
cd docker/plugins/threatlib && python -m pytest tests/test_service.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'threatlib.service'`.

- [ ] **Step 4: Implement the service**

`docker/plugins/threatlib/service.py`:

```python
"""All writes to the threatlib tables, with the sample mirror kept in step.

Used by the REST resources and by the sync. Functions take `commit=True`
by default; the sync passes commit=False and commits per file itself.
"""

from __future__ import annotations

from sqlalchemy import func

from mwdb.model import db

from .mirror import apply_mirror, remove_mirror
from .model import Threat, ThreatSample, utcnow
from .validation import validate_category, validate_name, validate_rel_path


class NameConflict(Exception):
    pass


class PathConflict(Exception):
    pass


def _default_load_file(object_id: int):
    from mwdb.model import File

    return db.session.get(File, object_id)


# Monkeypatched by tests and by the sync (store.load).
_load_file = _default_load_file


def get_threat(name: str) -> Threat | None:
    return db.session.query(Threat).filter(Threat.name == name).first()


def create_threat(
    name, category, readme=None, created_by=None, *, flat=False, commit=True
) -> Threat:
    name = validate_name(name)
    category = validate_category(category)
    if get_threat(name) is not None:
        raise NameConflict(name)
    now = utcnow()
    threat = Threat(
        name=name,
        category=category,
        readme=readme,
        flat=flat,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    db.session.add(threat)
    db.session.flush()
    if commit:
        db.session.commit()
    return threat


def list_threats(query=None, category=None, page=1, per_page=50):
    counts = (
        db.session.query(
            ThreatSample.threat_id.label("threat_id"),
            func.count().label("n"),
        )
        .group_by(ThreatSample.threat_id)
        .subquery()
    )
    q = db.session.query(Threat, func.coalesce(counts.c.n, 0)).outerjoin(
        counts, counts.c.threat_id == Threat.id
    )
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        q = q.filter(Threat.name.like(escaped + "%", escape="\\"))
    if category:
        q = q.filter(Threat.category == category)
    total = q.count()
    rows = q.order_by(Threat.name).offset((page - 1) * per_page).limit(per_page).all()
    return [(t, int(n)) for t, n in rows], total


def sample_count(threat: Threat) -> int:
    return (
        db.session.query(func.count())
        .select_from(ThreatSample)
        .filter(ThreatSample.threat_id == threat.id)
        .scalar()
    )


def links_for_object(object_id: int) -> list[tuple[str, str]]:
    rows = (
        db.session.query(Threat.category, Threat.name)
        .join(ThreatSample, ThreatSample.threat_id == Threat.id)
        .filter(ThreatSample.object_id == object_id)
        .order_by(Threat.name)
        .all()
    )
    return [(c, n) for c, n in rows]


def link_sample(threat: Threat, file_obj, rel_path, *, commit=True):
    rel_path = validate_rel_path(rel_path)
    object_id = None if file_obj is None else file_obj.id
    existing = db.session.get(ThreatSample, (threat.id, rel_path))
    if existing is not None:
        if existing.object_id == object_id:
            return existing, False
        raise PathConflict(rel_path)
    now = utcnow()
    link = ThreatSample(
        threat_id=threat.id, rel_path=rel_path, object_id=object_id, added_at=now
    )
    db.session.add(link)
    apply_mirror(file_obj, threat.category, threat.name)
    threat.updated_at = now
    db.session.flush()
    if commit:
        db.session.commit()
    return link, True


def _unlink(threat: Threat, link: ThreatSample) -> None:
    object_id = link.object_id
    db.session.delete(link)
    db.session.flush()
    if object_id is not None:
        remaining = links_for_object(object_id)
        remove_mirror(_load_file(object_id), threat.category, threat.name, remaining)
    threat.updated_at = utcnow()


def unlink_sample(threat: Threat, rel_path: str, *, commit=True) -> bool:
    link = db.session.get(ThreatSample, (threat.id, rel_path))
    if link is None:
        return False
    _unlink(threat, link)
    if commit:
        db.session.commit()
    return True


def set_category(threat: Threat, category: str, *, commit=True) -> None:
    category = validate_category(category)
    old = threat.category
    if category == old:
        return
    object_ids = sorted(
        {link.object_id for link in threat.samples if link.object_id is not None}
    )
    threat.category = category
    db.session.flush()
    for object_id in object_ids:
        file_obj = _load_file(object_id)
        if file_obj is None:
            continue
        file_obj.add_tag(category, commit=False)
        others = [
            (c, n) for c, n in links_for_object(object_id) if n != threat.name
        ]
        if not any(c == old for c, _ in others):
            file_obj.remove_tag(old, commit=False)
    threat.updated_at = utcnow()
    if commit:
        db.session.commit()


def set_readme(threat: Threat, readme, *, commit=True) -> None:
    threat.readme = readme
    threat.updated_at = utcnow()
    if commit:
        db.session.commit()


def delete_threat(threat: Threat, *, commit=True) -> None:
    for link in list(threat.samples):
        _unlink(threat, link)
    db.session.delete(threat)
    if commit:
        db.session.commit()
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd docker/plugins/threatlib && python -m pytest tests/ -v
```

Expected: all passed (model, validation, mirror, service).

- [ ] **Step 6: Lint and commit**

```bash
ruff check --fix docker/plugins/threatlib && ruff format docker/plugins/threatlib
git add docker/plugins/threatlib
git commit -m "threatlib: service layer for threats, links and mirror

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Threat resources (list, create, get, update, delete)

**Files:**
- Create: `docker/plugins/threatlib/resource.py`
- Modify: `docker/plugins/threatlib/__init__.py` (`_register_resources`)
- Test: `docker/plugins/threatlib/tests/test_resource_threat.py`

**Interfaces:**
- Consumes: `service.*`, `model.iso`.
- Produces: `resource.ThreatListResource` (`GET/POST /threatlib/threat`), `resource.ThreatResource` (`GET/PUT/DELETE /threatlib/threat/<name>`), helpers `resource.threat_dict(threat, sample_count, samples=None) -> dict`, `resource.sample_dict(link, file_obj) -> dict`, `resource.visible_samples(threat) -> list[tuple[ThreatSample, file_obj]]`.
- JSON shapes:
  - threat: `{"name", "category", "readme", "flat", "created_by", "created_at", "updated_at", "sample_count", "samples"?: [...]}`
  - sample: `{"sha256": str | null, "file_name": str | null, "rel_path", "added_at"}`
  - list: `{"threats": [threat...], "total": int, "page": int, "per_page": int}`

- [ ] **Step 1: Write the failing tests**

`docker/plugins/threatlib/tests/test_resource_threat.py`:

```python
import sys
from unittest.mock import MagicMock

import pytest
from flask import Flask

from threatlib.tests.fakes import FakeStore


@pytest.fixture
def app(monkeypatch):
    from threatlib import service

    store = FakeStore()
    monkeypatch.setattr(service, "_load_file", store.load)

    if "threatlib.resource" in sys.modules:
        del sys.modules["threatlib.resource"]
    from threatlib import resource as resource_mod

    flask_app = Flask(__name__)
    flask_app.add_url_rule(
        "/api/threatlib/threat",
        view_func=resource_mod.ThreatListResource.as_view("threat_list"),
    )
    flask_app.add_url_rule(
        "/api/threatlib/threat/<name>",
        view_func=resource_mod.ThreatResource.as_view("threat"),
    )

    fake_user = MagicMock()
    fake_user.login = "alice"

    @flask_app.before_request
    def _auth():
        from flask import g

        g.auth_user = fake_user

    flask_app.store = store
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def test_create_and_get(client):
    r = client.post(
        "/api/threatlib/threat",
        json={"name": "FIO-1", "category": "threats", "readme": "# FIO-1"},
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["name"] == "FIO-1" and body["created_by"] == "alice"
    assert body["sample_count"] == 0 and body["samples"] == []

    r = client.get("/api/threatlib/threat/FIO-1")
    assert r.status_code == 200 and r.get_json()["readme"] == "# FIO-1"
    assert client.get("/api/threatlib/threat/nope").status_code == 404


def test_create_validation_and_conflict(client):
    assert client.post("/api/threatlib/threat", json={"name": "a b", "category": "threats"}).status_code == 400
    assert client.post("/api/threatlib/threat", json={"name": "x", "category": "zzz"}).status_code == 400
    assert client.post("/api/threatlib/threat", json={"name": "x", "category": "threats"}).status_code == 200
    assert client.post("/api/threatlib/threat", json={"name": "x", "category": "threats"}).status_code == 409


def test_list_with_query_category_and_paging(client, app):
    from threatlib import service

    a = service.create_threat("alpha", "threats")
    service.create_threat("alpine", "for-later-review")
    service.create_threat("beta", "threats")
    service.link_sample(a, app.store.add(b"1"), "1.php")

    r = client.get("/api/threatlib/threat")
    body = r.get_json()
    assert body["total"] == 3 and body["page"] == 1
    assert [t["name"] for t in body["threats"]] == ["alpha", "alpine", "beta"]
    assert body["threats"][0]["sample_count"] == 1
    assert "samples" not in body["threats"][0]

    assert client.get("/api/threatlib/threat?query=alp").get_json()["total"] == 2
    assert client.get("/api/threatlib/threat?category=threats").get_json()["total"] == 2
    r = client.get("/api/threatlib/threat?page=2&per_page=2").get_json()
    assert [t["name"] for t in r["threats"]] == ["beta"]
    assert client.get("/api/threatlib/threat?category=zzz").status_code == 400


def test_get_includes_samples(client, app):
    from threatlib import service

    t = service.create_threat("FIO-1", "threats")
    f = app.store.add(b"<?php", "orig.php")
    service.link_sample(t, f, "sub/a.php")
    service.link_sample(t, None, "empty.php")
    body = client.get("/api/threatlib/threat/FIO-1").get_json()
    assert body["sample_count"] == 2
    assert body["samples"] == [
        {"sha256": None, "file_name": None, "rel_path": "empty.php", "added_at": body["samples"][0]["added_at"]},
        {"sha256": f.sha256, "file_name": "orig.php", "rel_path": "sub/a.php", "added_at": body["samples"][1]["added_at"]},
    ]


def test_put_readme_and_category(client, app):
    from threatlib import service

    t = service.create_threat("FIO-1", "for-later-review")
    f = app.store.add(b"x")
    service.link_sample(t, f, "a.php")

    r = client.put("/api/threatlib/threat/FIO-1", json={"readme": "# edited"})
    assert r.status_code == 200 and r.get_json()["readme"] == "# edited"
    r = client.put("/api/threatlib/threat/FIO-1", json={"category": "threats"})
    assert r.status_code == 200 and r.get_json()["category"] == "threats"
    assert f.tags == {"threats"}
    assert client.put("/api/threatlib/threat/FIO-1", json={"category": "zzz"}).status_code == 400
    assert client.put("/api/threatlib/threat/FIO-1", json={}).status_code == 400
    assert client.put("/api/threatlib/threat/nope", json={"readme": "x"}).status_code == 404


def test_delete(client, app):
    from threatlib import service

    t = service.create_threat("FIO-1", "threats")
    f = app.store.add(b"x")
    service.link_sample(t, f, "a.php")
    assert client.delete("/api/threatlib/threat/FIO-1").status_code == 200
    assert service.get_threat("FIO-1") is None and f.tags == set()
    assert client.delete("/api/threatlib/threat/FIO-1").status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd docker/plugins/threatlib && python -m pytest tests/test_resource_threat.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'threatlib.resource'`.

- [ ] **Step 3: Implement the resources**

`docker/plugins/threatlib/resource.py`:

```python
"""Flask-RESTful resources under /api/threatlib/."""

from __future__ import annotations

from flask import g, jsonify, request
from werkzeug.exceptions import BadRequest, Conflict, NotFound

from mwdb.core.capabilities import Capabilities
from mwdb.core.service import Resource
from mwdb.model import db
from mwdb.resources import requires_authorization, requires_capabilities

from . import service
from .model import Threat, iso
from .validation import ValidationError, validate_category

MAX_PER_PAGE = 200


def sample_dict(link, file_obj) -> dict:
    return {
        "sha256": None if file_obj is None else file_obj.dhash,
        "file_name": None if file_obj is None else file_obj.file_name,
        "rel_path": link.rel_path,
        "added_at": iso(link.added_at),
    }


def visible_samples(threat: Threat):
    """Links the current user may see: empty-file links always, object links
    only when the user has explicit access to the object."""
    result = []
    for link in sorted(threat.samples, key=lambda l: l.rel_path):
        if link.object_id is None:
            result.append((link, None))
            continue
        file_obj = service._load_file(link.object_id)
        if file_obj is None or not file_obj.has_explicit_access(g.auth_user):
            continue
        result.append((link, file_obj))
    return result


def threat_dict(threat: Threat, sample_count: int, samples=None) -> dict:
    data = {
        "name": threat.name,
        "category": threat.category,
        "readme": threat.readme,
        "flat": bool(threat.flat),
        "created_by": threat.created_by,
        "created_at": iso(threat.created_at),
        "updated_at": iso(threat.updated_at),
        "sample_count": sample_count,
    }
    if samples is not None:
        data["samples"] = [sample_dict(link, f) for link, f in samples]
    return data


def _json_body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise BadRequest("JSON object body required")
    return body


def _get_or_404(name: str) -> Threat:
    threat = service.get_threat(name)
    if threat is None:
        raise NotFound("Threat not found")
    return threat


class ThreatListResource(Resource):
    @requires_authorization
    def get(self):
        """
        ---
        summary: List threats
        description: Name-prefix search with optional category filter and paging.
        security:
            - bearerAuth: []
        tags:
            - threatlib
        """
        query = request.args.get("query") or None
        category = request.args.get("category") or None
        if category is not None:
            try:
                validate_category(category)
            except ValidationError as e:
                raise BadRequest(e.message)
        try:
            page = max(1, int(request.args.get("page", 1)))
            per_page = min(MAX_PER_PAGE, max(1, int(request.args.get("per_page", 50))))
        except ValueError:
            raise BadRequest("page and per_page must be integers")
        items, total = service.list_threats(
            query=query, category=category, page=page, per_page=per_page
        )
        return jsonify(
            {
                "threats": [threat_dict(t, n) for t, n in items],
                "total": total,
                "page": page,
                "per_page": per_page,
            }
        )

    @requires_authorization
    @requires_capabilities(Capabilities.adding_files)
    def post(self):
        """
        ---
        summary: Create a threat
        security:
            - bearerAuth: []
        tags:
            - threatlib
        """
        body = _json_body()
        try:
            threat = service.create_threat(
                body.get("name"),
                body.get("category"),
                readme=body.get("readme"),
                created_by=g.auth_user.login,
            )
        except ValidationError as e:
            raise BadRequest(e.message)
        except service.NameConflict:
            raise Conflict("A threat with this name already exists")
        return jsonify(threat_dict(threat, 0, samples=[]))


class ThreatResource(Resource):
    @requires_authorization
    def get(self, name):
        """
        ---
        summary: Get a threat with its samples
        security:
            - bearerAuth: []
        tags:
            - threatlib
        """
        threat = _get_or_404(name)
        return jsonify(
            threat_dict(threat, service.sample_count(threat), visible_samples(threat))
        )

    @requires_authorization
    @requires_capabilities(Capabilities.adding_files)
    def put(self, name):
        """
        ---
        summary: Update readme and/or category
        security:
            - bearerAuth: []
        tags:
            - threatlib
        """
        threat = _get_or_404(name)
        body = _json_body()
        if "readme" not in body and "category" not in body:
            raise BadRequest("Provide 'readme' and/or 'category'")
        try:
            if "category" in body:
                service.set_category(threat, body["category"], commit=False)
            if "readme" in body:
                readme = body["readme"]
                if readme is not None and not isinstance(readme, str):
                    raise BadRequest("'readme' must be a string or null")
                service.set_readme(threat, readme, commit=False)
        except ValidationError as e:
            db.session.rollback()
            raise BadRequest(e.message)
        db.session.commit()
        return jsonify(
            threat_dict(threat, service.sample_count(threat), visible_samples(threat))
        )

    @requires_authorization
    @requires_capabilities(Capabilities.removing_objects)
    def delete(self, name):
        """
        ---
        summary: Delete a threat (samples are unlinked, never deleted)
        security:
            - bearerAuth: []
        tags:
            - threatlib
        """
        threat = _get_or_404(name)
        service.delete_threat(threat)
        return jsonify({"deleted": name})
```

- [ ] **Step 4: Register the resources in the entrypoint**

Replace `_register_resources` in `docker/plugins/threatlib/__init__.py` with:

```python
def _register_resources(app_context: "PluginAppContext") -> None:
    from .resource import ThreatListResource, ThreatResource

    app_context.register_resource(ThreatListResource, "/threatlib/threat")
    app_context.register_resource(ThreatResource, "/threatlib/threat/<name>")
    logger.info("threatlib: registered /api/threatlib/threat resources")
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd docker/plugins/threatlib && python -m pytest tests/ -v
```

Expected: all passed.

- [ ] **Step 6: Lint and commit**

```bash
ruff check --fix docker/plugins/threatlib && ruff format docker/plugins/threatlib
git add docker/plugins/threatlib
git commit -m "threatlib: threat list/create/get/update/delete resources

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Sample link/unlink and upload resources

**Files:**
- Modify: `docker/plugins/threatlib/resource.py`
- Modify: `docker/plugins/threatlib/__init__.py`
- Test: `docker/plugins/threatlib/tests/test_resource_samples.py`

**Interfaces:**
- Consumes: `service.link_sample`, `service.unlink_sample`, `mwdb.model.File.access`, `mwdb.model.File.get_or_create`, `mwdb.model.file.EmptyFileError`, `mwdb.resources.get_shares_for_upload`, `mwdb.core.hooks.hooks`.
- Produces: `resource.ThreatSampleListResource` (`POST /threatlib/threat/<name>/sample`, body `{"sha256", "rel_path"}`), `resource.ThreatSampleResource` (`DELETE /threatlib/threat/<name>/sample/<sha256>?rel_path=`), `resource.ThreatUploadResource` (`POST /threatlib/upload`, multipart).
- Upload form fields: `threat` (name, required), `category` + `readme` (used only when the threat does not exist yet), `files` (repeatable), `rel_paths` (repeatable, parallel to `files`), `upload_as` (default `*`). Response: `{"threat": threat_dict, "results": [{"rel_path", "sha256": str|null, "status": "new"|"existing"|"rejected", "reason"?: str}]}`.
- Upload semantics: each file is stored via `File.get_or_create(file_name=basename(rel_path), file_stream=stream, share_3rd_party=False, share_with=shares)`, committed, hooks fired, then linked. A `PathConflict` on link yields `status: "rejected"` with the file left stored but unlinked. An empty file yields `rejected` with reason `empty file`.

- [ ] **Step 1: Write the failing tests**

`docker/plugins/threatlib/tests/test_resource_samples.py`:

```python
import io
import sys
from unittest.mock import MagicMock

import pytest
from flask import Flask

from threatlib.tests.fakes import FakeStore


@pytest.fixture
def app(monkeypatch):
    from threatlib import service

    store = FakeStore()
    monkeypatch.setattr(service, "_load_file", store.load)

    # File.access(sha256) -> FakeFile or None; File.get_or_create -> store
    fake_file_cls = MagicMock()
    fake_file_cls.access.side_effect = lambda ident: store.by_sha.get(ident)

    def _get_or_create(file_name, file_stream, share_3rd_party, share_with=None, **kw):
        return store.get_or_create(file_name, file_stream)

    fake_file_cls.get_or_create.side_effect = _get_or_create
    monkeypatch.setattr("mwdb.model.File", fake_file_cls)
    fake_hooks = MagicMock()
    monkeypatch.setattr("mwdb.core.hooks.hooks", fake_hooks)

    if "threatlib.resource" in sys.modules:
        del sys.modules["threatlib.resource"]
    from threatlib import resource as resource_mod

    monkeypatch.setattr(resource_mod, "File", fake_file_cls)
    monkeypatch.setattr(resource_mod, "hooks", fake_hooks)

    flask_app = Flask(__name__)
    flask_app.add_url_rule(
        "/api/threatlib/threat/<name>/sample",
        view_func=resource_mod.ThreatSampleListResource.as_view("sample_list"),
    )
    flask_app.add_url_rule(
        "/api/threatlib/threat/<name>/sample/<sha256>",
        view_func=resource_mod.ThreatSampleResource.as_view("sample"),
    )
    flask_app.add_url_rule(
        "/api/threatlib/upload",
        view_func=resource_mod.ThreatUploadResource.as_view("upload"),
    )

    fake_user = MagicMock()
    fake_user.login = "alice"

    @flask_app.before_request
    def _auth():
        from flask import g

        g.auth_user = fake_user

    flask_app.store = store
    flask_app.fake_hooks = fake_hooks
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def test_link_existing_sample(client, app):
    from threatlib import service

    service.create_threat("FIO-1", "threats")
    f = app.store.add(b"<?php")
    r = client.post("/api/threatlib/threat/FIO-1/sample", json={"sha256": f.sha256, "rel_path": "a.php"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["sample_count"] == 1 and f.tags == {"threats"}
    # idempotent
    assert client.post("/api/threatlib/threat/FIO-1/sample", json={"sha256": f.sha256, "rel_path": "a.php"}).status_code == 200
    # conflict with another object on the same path
    g = app.store.add(b"other")
    assert client.post("/api/threatlib/threat/FIO-1/sample", json={"sha256": g.sha256, "rel_path": "a.php"}).status_code == 409
    # unknown object / threat / bad path
    assert client.post("/api/threatlib/threat/FIO-1/sample", json={"sha256": "0" * 64, "rel_path": "b.php"}).status_code == 404
    assert client.post("/api/threatlib/threat/nope/sample", json={"sha256": f.sha256, "rel_path": "b.php"}).status_code == 404
    assert client.post("/api/threatlib/threat/FIO-1/sample", json={"sha256": f.sha256, "rel_path": "../b.php"}).status_code == 400


def test_unlink_sample(client, app):
    from threatlib import service

    t = service.create_threat("FIO-1", "threats")
    f = app.store.add(b"<?php")
    service.link_sample(t, f, "sub/a.php")
    r = client.delete(f"/api/threatlib/threat/FIO-1/sample/{f.sha256}?rel_path=sub/a.php")
    assert r.status_code == 200 and r.get_json()["sample_count"] == 0
    assert f.tags == set()
    assert client.delete(f"/api/threatlib/threat/FIO-1/sample/{f.sha256}?rel_path=sub/a.php").status_code == 404
    assert client.delete(f"/api/threatlib/threat/FIO-1/sample/{f.sha256}").status_code == 400


def test_unlink_requires_matching_sha(client, app):
    from threatlib import service

    t = service.create_threat("FIO-1", "threats")
    f = app.store.add(b"<?php")
    service.link_sample(t, f, "a.php")
    assert client.delete(f"/api/threatlib/threat/FIO-1/sample/{'0' * 64}?rel_path=a.php").status_code == 404


def _multipart(threat, files, category=None, readme=None):
    data = {"threat": threat}
    if category:
        data["category"] = category
    if readme is not None:
        data["readme"] = readme
    data["files"] = [(io.BytesIO(content), name) for name, content in files]
    data["rel_paths"] = [name for name, _ in files]
    return data


def test_upload_creates_threat_and_links(client, app):
    data = _multipart(
        "NEW-1",
        [("a.php", b"<?php a();"), ("sub/b.php", b"<?php b();")],
        category="for-later-review",
        readme="# NEW-1",
    )
    r = client.post("/api/threatlib/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["threat"]["name"] == "NEW-1" and body["threat"]["category"] == "for-later-review"
    assert body["threat"]["readme"] == "# NEW-1" and body["threat"]["sample_count"] == 2
    assert [x["status"] for x in body["results"]] == ["new", "new"]
    assert [x["rel_path"] for x in body["results"]] == ["a.php", "sub/b.php"]
    assert app.fake_hooks.on_created_file.call_count == 2
    assert app.fake_hooks.on_created_object.call_count == 2
    for x in body["results"]:
        assert app.store.by_sha[x["sha256"]].tags == {"for-later-review"}


def test_upload_to_existing_threat_ignores_category_and_dedupes(client, app):
    from threatlib import service

    service.create_threat("FIO-1", "threats", readme="# keep")
    data = _multipart("FIO-1", [("a.php", b"same")], category="for-later-review", readme="# ignored")
    r = client.post("/api/threatlib/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 200
    assert r.get_json()["threat"]["category"] == "threats"
    assert r.get_json()["threat"]["readme"] == "# keep"

    data = _multipart("FIO-1", [("copy.php", b"same")])
    r = client.post("/api/threatlib/upload", data=data, content_type="multipart/form-data")
    assert r.get_json()["results"][0]["status"] == "existing"
    assert app.fake_hooks.on_reuploaded_file.call_count == 1
    assert r.get_json()["threat"]["sample_count"] == 2


def test_upload_rejects_path_conflict_and_empty_file(client, app):
    from threatlib import service

    t = service.create_threat("FIO-1", "threats")
    service.link_sample(t, app.store.add(b"first"), "a.php")
    data = _multipart("FIO-1", [("a.php", b"second"), ("empty.php", b"")])
    r = client.post("/api/threatlib/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 200
    results = r.get_json()["results"]
    assert results[0]["status"] == "rejected" and "already" in results[0]["reason"]
    assert results[1]["status"] == "rejected" and "empty" in results[1]["reason"]
    assert r.get_json()["threat"]["sample_count"] == 1


def test_upload_validation(client):
    # new threat without category
    data = _multipart("NEW-2", [("a.php", b"x")])
    assert client.post("/api/threatlib/upload", data=data, content_type="multipart/form-data").status_code == 400
    # mismatched rel_paths
    data = _multipart("NEW-2", [("a.php", b"x")], category="threats")
    data["rel_paths"] = []
    assert client.post("/api/threatlib/upload", data=data, content_type="multipart/form-data").status_code == 400
    # no files
    data = {"threat": "NEW-2", "category": "threats"}
    assert client.post("/api/threatlib/upload", data=data, content_type="multipart/form-data").status_code == 400
    # bad rel_path
    data = _multipart("NEW-2", [("../a.php", b"x")], category="threats")
    assert client.post("/api/threatlib/upload", data=data, content_type="multipart/form-data").status_code == 400
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd docker/plugins/threatlib && python -m pytest tests/test_resource_samples.py -v
```

Expected: FAIL with `AttributeError: module 'threatlib.resource' has no attribute 'ThreatSampleListResource'`.

- [ ] **Step 3: Add the resources**

Append to `docker/plugins/threatlib/resource.py` (and add these imports at the top of the file: `import posixpath`, `from mwdb.core.hooks import hooks`, `from mwdb.model import File`, `from mwdb.model.file import EmptyFileError`, `from mwdb.resources import get_shares_for_upload`, `ThreatSample` from `.model`, and `validate_rel_path` from `.validation`):

```python
def _threat_response(threat: Threat) -> dict:
    return threat_dict(threat, service.sample_count(threat), visible_samples(threat))


class ThreatSampleListResource(Resource):
    @requires_authorization
    @requires_capabilities(Capabilities.adding_files)
    def post(self, name):
        """
        ---
        summary: Link an existing sample to a threat
        security:
            - bearerAuth: []
        tags:
            - threatlib
        """
        threat = _get_or_404(name)
        body = _json_body()
        sha256 = body.get("sha256")
        if not isinstance(sha256, str):
            raise BadRequest("'sha256' is required")
        file_obj = File.access(sha256)
        if file_obj is None:
            raise NotFound("Sample not found or you don't have access to it")
        try:
            service.link_sample(threat, file_obj, body.get("rel_path"))
        except ValidationError as e:
            raise BadRequest(e.message)
        except service.PathConflict:
            raise Conflict("rel_path already used by another sample in this threat")
        return jsonify(_threat_response(threat))


class ThreatSampleResource(Resource):
    @requires_authorization
    @requires_capabilities(Capabilities.adding_files)
    def delete(self, name, sha256):
        """
        ---
        summary: Unlink one path of a sample from a threat
        security:
            - bearerAuth: []
        tags:
            - threatlib
        """
        threat = _get_or_404(name)
        rel_path = request.args.get("rel_path")
        if not rel_path:
            raise BadRequest("'rel_path' query parameter is required")
        try:
            rel_path = validate_rel_path(rel_path)
        except ValidationError as e:
            raise BadRequest(e.message)
        link = db.session.get(ThreatSample, (threat.id, rel_path))
        if link is None or link.object_id is None:
            raise NotFound("Link not found")
        file_obj = service._load_file(link.object_id)
        if file_obj is None or file_obj.dhash != sha256.lower():
            raise NotFound("Link not found")
        service.unlink_sample(threat, rel_path)
        return jsonify(_threat_response(threat))


class ThreatUploadResource(Resource):
    @requires_authorization
    @requires_capabilities(Capabilities.adding_files)
    def post(self):
        """
        ---
        summary: Upload one or more files into a threat, creating it if needed
        description: |
            multipart/form-data with fields: threat (name), category + readme
            (only used when the threat does not exist yet), files (repeatable),
            rel_paths (repeatable, parallel to files), upload_as (default "*").
        security:
            - bearerAuth: []
        tags:
            - threatlib
        """
        form = request.form
        name = form.get("threat")
        files = request.files.getlist("files")
        rel_paths = form.getlist("rel_paths")
        if not files:
            raise BadRequest("At least one file is required")
        if len(files) != len(rel_paths):
            raise BadRequest("'rel_paths' must have one entry per file")
        try:
            rel_paths = [validate_rel_path(p) for p in rel_paths]
        except ValidationError as e:
            raise BadRequest(e.message)

        threat = service.get_threat(name) if isinstance(name, str) else None
        if threat is None:
            category = form.get("category")
            if not category:
                raise BadRequest("'category' is required when creating a new threat")
            try:
                threat = service.create_threat(
                    name,
                    category,
                    readme=form.get("readme") or None,
                    created_by=g.auth_user.login,
                )
            except ValidationError as e:
                raise BadRequest(e.message)

        share_with = get_shares_for_upload(form.get("upload_as", "*"))
        results = []
        for storage, rel_path in zip(files, rel_paths):
            try:
                file_obj, is_new = File.get_or_create(
                    file_name=posixpath.basename(rel_path),
                    file_stream=storage.stream,
                    share_3rd_party=False,
                    share_with=share_with,
                )
            except EmptyFileError:
                results.append(
                    {"rel_path": rel_path, "sha256": None, "status": "rejected", "reason": "empty file"}
                )
                continue
            db.session.commit()
            if is_new:
                hooks.on_created_file(file_obj)
                hooks.on_created_object(file_obj)
            else:
                hooks.on_reuploaded_file(file_obj)
                hooks.on_reuploaded_object(file_obj)
            file_obj.release_after_upload()
            try:
                service.link_sample(threat, file_obj, rel_path)
            except service.PathConflict:
                results.append(
                    {
                        "rel_path": rel_path,
                        "sha256": file_obj.dhash,
                        "status": "rejected",
                        "reason": "rel_path already used by another sample in this threat",
                    }
                )
                continue
            results.append(
                {
                    "rel_path": rel_path,
                    "sha256": file_obj.dhash,
                    "status": "new" if is_new else "existing",
                }
            )
        return jsonify({"threat": _threat_response(threat), "results": results})
```

- [ ] **Step 4: Register the new resources**

In `docker/plugins/threatlib/__init__.py`, `_register_resources` becomes:

```python
def _register_resources(app_context: "PluginAppContext") -> None:
    from .resource import (
        ThreatListResource,
        ThreatResource,
        ThreatSampleListResource,
        ThreatSampleResource,
        ThreatUploadResource,
    )

    app_context.register_resource(ThreatListResource, "/threatlib/threat")
    app_context.register_resource(ThreatResource, "/threatlib/threat/<name>")
    app_context.register_resource(
        ThreatSampleListResource, "/threatlib/threat/<name>/sample"
    )
    app_context.register_resource(
        ThreatSampleResource, "/threatlib/threat/<name>/sample/<sha256>"
    )
    app_context.register_resource(ThreatUploadResource, "/threatlib/upload")
    logger.info("threatlib: registered /api/threatlib/* resources")
```

- [ ] **Step 5: Run all tests**

```bash
cd docker/plugins/threatlib && python -m pytest tests/ -v
```

Expected: all passed.

- [ ] **Step 6: Lint and commit**

```bash
ruff check --fix docker/plugins/threatlib && ruff format docker/plugins/threatlib
git add docker/plugins/threatlib
git commit -m "threatlib: sample link/unlink and multipart upload resources

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Manifest and object store

**Files:**
- Create: `docker/plugins/threatlib/sync/__init__.py` (empty)
- Create: `docker/plugins/threatlib/sync/manifest.py`
- Create: `docker/plugins/threatlib/sync/store.py`
- Test: `docker/plugins/threatlib/tests/test_manifest.py`

**Interfaces:**
- Produces (`manifest.py`): `MANIFEST_NAME = ".mwdb-threatlib.json"`; `@dataclass Manifest(files: dict[str, str], readmes: dict[str, str], generated_at: str | None = None)`; `sha256_bytes(data: bytes) -> str`; `load_manifest(repo_path: Path) -> Manifest | None`; `save_manifest(repo_path: Path, manifest: Manifest) -> None`. Keys in `files` are repo-relative POSIX paths of sample files (`threats/FIO-1/a.php`); keys in `readmes` are threat dirs (`threats/FIO-1`); values are sha256 hex.
- Produces (`store.py`): `class ObjectStore(Protocol)` with `get_or_create(file_name: str, stream: BinaryIO) -> tuple[Any, bool]`, `load(object_id: int) -> Any | None`, `read(object_id: int) -> bytes`; `class MwdbStore(ObjectStore)` with `__init__(self, share_group_name: str | None)`.

- [ ] **Step 1: Write the failing tests**

`docker/plugins/threatlib/tests/test_manifest.py`:

```python
import json

from threatlib.sync.manifest import (
    MANIFEST_NAME,
    Manifest,
    load_manifest,
    save_manifest,
    sha256_bytes,
)


def test_sha256_bytes():
    assert sha256_bytes(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_round_trip(tmp_path):
    assert load_manifest(tmp_path) is None
    m = Manifest(files={"threats/FIO-1/a.php": "aa"}, readmes={"threats/FIO-1": "bb"})
    save_manifest(tmp_path, m)
    raw = json.loads((tmp_path / MANIFEST_NAME).read_text())
    assert raw["version"] == 1 and raw["files"] == m.files and raw["readmes"] == m.readmes
    assert raw["generated_at"]
    loaded = load_manifest(tmp_path)
    assert loaded.files == m.files and loaded.readmes == m.readmes
    assert loaded.generated_at == raw["generated_at"]


def test_save_is_deterministic_and_sorted(tmp_path):
    m = Manifest(files={"b": "2", "a": "1"}, readmes={}, generated_at="fixed")
    save_manifest(tmp_path, m)
    text = (tmp_path / MANIFEST_NAME).read_text()
    assert text.index('"a"') < text.index('"b"')
    assert text.endswith("\n")


def test_load_rejects_unknown_version(tmp_path):
    (tmp_path / MANIFEST_NAME).write_text('{"version": 99, "files": {}, "readmes": {}}')
    try:
        load_manifest(tmp_path)
        assert False
    except ValueError:
        pass
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd docker/plugins/threatlib && python -m pytest tests/test_manifest.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'threatlib.sync'`.

- [ ] **Step 3: Implement manifest.py and store.py**

`docker/plugins/threatlib/sync/manifest.py`:

```python
"""The committed manifest: what the previous export wrote to the repo.

'New in repo' means 'path not in the last manifest', which is what lets a
sample deleted in MWDB stay deleted instead of being re-ingested.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_NAME = ".mwdb-threatlib.json"
MANIFEST_VERSION = 1


@dataclass
class Manifest:
    files: dict[str, str] = field(default_factory=dict)
    readmes: dict[str, str] = field(default_factory=dict)
    generated_at: str | None = None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_manifest(repo_path: Path) -> Manifest | None:
    path = Path(repo_path) / MANIFEST_NAME
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("version") != MANIFEST_VERSION:
        raise ValueError(f"Unsupported manifest version: {raw.get('version')!r}")
    return Manifest(
        files=dict(raw.get("files", {})),
        readmes=dict(raw.get("readmes", {})),
        generated_at=raw.get("generated_at"),
    )


def save_manifest(repo_path: Path, manifest: Manifest) -> None:
    generated_at = manifest.generated_at or datetime.now(timezone.utc).isoformat()
    payload = {
        "version": MANIFEST_VERSION,
        "generated_at": generated_at,
        "files": dict(sorted(manifest.files.items())),
        "readmes": dict(sorted(manifest.readmes.items())),
    }
    text = json.dumps(payload, indent=1, sort_keys=False) + "\n"
    (Path(repo_path) / MANIFEST_NAME).write_text(text, encoding="utf-8")
```

`docker/plugins/threatlib/sync/store.py`:

```python
"""Object storage abstraction so the sync can be tested without MWDB.

Production uses MwdbStore (wraps mwdb.model.File); tests use FakeStore
from tests/conftest.py.
"""

from __future__ import annotations

from typing import Any, BinaryIO, Protocol


class ObjectStore(Protocol):
    def get_or_create(self, file_name: str, stream: BinaryIO) -> tuple[Any, bool]: ...

    def load(self, object_id: int) -> Any | None: ...

    def read(self, object_id: int) -> bytes: ...


class MwdbStore:
    def __init__(self, share_group_name: str | None = "public"):
        self.share_group_name = share_group_name

    def _share_with(self):
        if not self.share_group_name:
            return None
        from mwdb.model import Group

        group = Group.get_by_name(self.share_group_name)
        return [group] if group is not None else None

    def get_or_create(self, file_name, stream):
        from mwdb.model import File, db

        file_obj, is_new = File.get_or_create(
            file_name=file_name,
            file_stream=stream,
            share_3rd_party=False,
            share_with=self._share_with(),
        )
        db.session.commit()
        file_obj.release_after_upload()
        return file_obj, is_new

    def load(self, object_id):
        from mwdb.model import File, db

        return db.session.get(File, object_id)

    def read(self, object_id):
        file_obj = self.load(object_id)
        if file_obj is None:
            raise KeyError(object_id)
        return file_obj.read()
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd docker/plugins/threatlib && python -m pytest tests/test_manifest.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Lint and commit**

```bash
ruff check --fix docker/plugins/threatlib && ruff format docker/plugins/threatlib
git add docker/plugins/threatlib
git commit -m "threatlib: sync manifest and object store abstraction

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Git repository wrapper

**Files:**
- Create: `docker/plugins/threatlib/sync/repo.py`
- Test: `docker/plugins/threatlib/tests/test_repo.py`

**Interfaces:**
- Produces: `class GitError(Exception)`; `class GitRepo` with `__init__(self, path: Path, url: str, branch: str = "trunk", deploy_key: str | None = None, author: str = "mwdb-threatlib-bot <noreply@wafflemakers.xyz>")`; `ensure_clone() -> None`; `reset_to_remote() -> str` (returns the new HEAD sha); `is_dirty() -> bool`; `commit_and_push(message: str, push: bool = True) -> str | None` (returns the commit sha, or `None` when nothing to commit); `head() -> str`.
- `commit_and_push` stages everything (`git add -A`), commits with the bot author, and pushes `HEAD:<branch>`. A rejected push raises `GitError`; the local commit is left in place and the next `reset_to_remote()` discards it.

- [ ] **Step 1: Write the failing tests**

`docker/plugins/threatlib/tests/test_repo.py`:

```python
import subprocess
from pathlib import Path

import pytest

from threatlib.sync.repo import GitError, GitRepo


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def remote(tmp_path):
    """A bare 'origin' with one commit on trunk, plus a scratch working clone
    to simulate teammates pushing."""
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "trunk", str(bare))
    work = tmp_path / "teammate"
    _git(tmp_path, "clone", "-q", "-b", "trunk", str(bare), str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Teammate")
    (work / "threats").mkdir()
    (work / "threats" / "README.md").write_text("root readme\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "init")
    _git(work, "push", "-q", "origin", "trunk")
    return bare, work


def test_clone_reset_and_head(tmp_path, remote):
    bare, work = remote
    repo = GitRepo(tmp_path / "clone", str(bare))
    repo.ensure_clone()
    repo.ensure_clone()  # idempotent
    assert (tmp_path / "clone" / "threats" / "README.md").exists()
    assert repo.head() == _git(work, "rev-parse", "HEAD")

    # teammate pushes; reset picks it up and discards local junk
    (work / "threats" / "new.txt").write_text("x")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "teammate")
    _git(work, "push", "-q", "origin", "trunk")
    (tmp_path / "clone" / "junk.txt").write_text("junk")
    new_head = repo.reset_to_remote()
    assert new_head == _git(work, "rev-parse", "HEAD")
    assert (tmp_path / "clone" / "threats" / "new.txt").exists()
    assert not (tmp_path / "clone" / "junk.txt").exists()
    assert repo.is_dirty() is False


def test_commit_and_push(tmp_path, remote):
    bare, work = remote
    repo = GitRepo(tmp_path / "clone", str(bare), author="Bot <bot@example.com>")
    repo.ensure_clone()
    assert repo.commit_and_push("nothing") is None

    (tmp_path / "clone" / "threats" / "a.php").write_text("<?php")
    assert repo.is_dirty() is True
    sha = repo.commit_and_push("threatlib: add a.php")
    assert sha == repo.head()
    _git(work, "pull", "-q")
    assert (work / "threats" / "a.php").read_text() == "<?php"
    assert _git(work, "log", "-1", "--format=%an <%ae>") == "Bot <bot@example.com>"
    assert _git(work, "log", "-1", "--format=%s") == "threatlib: add a.php"


def test_push_disabled_commits_locally_only(tmp_path, remote):
    bare, work = remote
    repo = GitRepo(tmp_path / "clone", str(bare))
    repo.ensure_clone()
    (tmp_path / "clone" / "threats" / "a.php").write_text("<?php")
    sha = repo.commit_and_push("local only", push=False)
    assert sha is not None
    assert _git(work, "ls-remote", str(bare), "trunk").split()[0] != sha


def test_rejected_push_raises_and_next_reset_recovers(tmp_path, remote):
    bare, work = remote
    repo = GitRepo(tmp_path / "clone", str(bare))
    repo.ensure_clone()
    # remote moves on
    (work / "threats" / "t.txt").write_text("t")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "teammate")
    _git(work, "push", "-q", "origin", "trunk")
    # local diverges
    (tmp_path / "clone" / "threats" / "a.php").write_text("<?php")
    with pytest.raises(GitError):
        repo.commit_and_push("diverged")
    head = repo.reset_to_remote()
    assert head == _git(work, "rev-parse", "HEAD")
    assert not (tmp_path / "clone" / "threats" / "a.php").exists()


def test_deploy_key_sets_ssh_command(tmp_path):
    repo = GitRepo(tmp_path / "c", "git@example.com:x/y.git", deploy_key="/keys/id")
    env = repo._env()
    assert "-i /keys/id" in env["GIT_SSH_COMMAND"]
    assert "StrictHostKeyChecking=accept-new" in env["GIT_SSH_COMMAND"]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd docker/plugins/threatlib && python -m pytest tests/test_repo.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'threatlib.sync.repo'`.

- [ ] **Step 3: Implement GitRepo**

`docker/plugins/threatlib/sync/repo.py`:

```python
"""Thin git wrapper. Every operation shells out to the git CLI."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger("mwdb.plugin.threatlib.sync")


class GitError(Exception):
    pass


class GitRepo:
    def __init__(
        self,
        path: Path,
        url: str,
        branch: str = "trunk",
        deploy_key: str | None = None,
        author: str = "mwdb-threatlib-bot <noreply@wafflemakers.xyz>",
    ):
        self.path = Path(path)
        self.url = url
        self.branch = branch
        self.deploy_key = deploy_key
        self.author = author

    # --- plumbing ---------------------------------------------------------
    def _env(self) -> dict:
        env = dict(os.environ)
        ssh = "ssh -o StrictHostKeyChecking=accept-new"
        if self.deploy_key:
            ssh += f" -i {self.deploy_key} -o IdentitiesOnly=yes"
        env["GIT_SSH_COMMAND"] = ssh
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd or self.path),
            env=self._env(),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout.strip()

    # --- operations -------------------------------------------------------
    def ensure_clone(self) -> None:
        if (self.path / ".git").is_dir():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._git(
            "clone", "-q", "-b", self.branch, self.url, str(self.path),
            cwd=self.path.parent,
        )

    def head(self) -> str:
        return self._git("rev-parse", "HEAD")

    def reset_to_remote(self) -> str:
        self._git("fetch", "-q", "origin", self.branch)
        self._git("reset", "-q", "--hard", f"origin/{self.branch}")
        self._git("clean", "-qfd")
        return self.head()

    def is_dirty(self) -> bool:
        return bool(self._git("status", "--porcelain"))

    def commit_and_push(self, message: str, push: bool = True) -> str | None:
        self._git("add", "-A")
        if not self._git("status", "--porcelain"):
            return None
        name, _, email = self.author.partition(" <")
        email = email.rstrip(">")
        self._git(
            "-c", f"user.name={name}", "-c", f"user.email={email}",
            "commit", "-q", "-m", message,
        )
        sha = self.head()
        if push:
            self._git("push", "-q", "origin", f"HEAD:{self.branch}")
            logger.info("threatlib sync: pushed %s to %s", sha[:12], self.branch)
        return sha
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd docker/plugins/threatlib && python -m pytest tests/test_repo.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Lint and commit**

```bash
ruff check --fix docker/plugins/threatlib && ruff format docker/plugins/threatlib
git add docker/plugins/threatlib
git commit -m "threatlib: git repo wrapper for the sync

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Ingest

**Files:**
- Create: `docker/plugins/threatlib/sync/ingest.py`
- Test: `docker/plugins/threatlib/tests/test_ingest.py`

**Interfaces:**
- Consumes: `service.*`, `manifest.Manifest`, `manifest.sha256_bytes`, `store: ObjectStore`.
- Produces: `@dataclass IngestStats(new_files: int = 0, new_threats: int = 0, readmes_updated: int = 0, unlinked: int = 0, skipped: int = 0, errors: int = 0)`; `ingest(repo_path: Path, manifest: Manifest | None, store: ObjectStore) -> IngestStats`; `classify(repo_path: Path, path: Path) -> tuple[str, str, str, bool, bool] | None` returning `(category, name, rel_path, flat, is_readme)` or `None` for a path the sync does not own.
- Ownership: see Global Constraints. Every file is handled in its own transaction (`commit` per file; `rollback` + count error on failure). The ingest sets `service._load_file = store.load` for its duration.

- [ ] **Step 1: Write the failing tests**

`docker/plugins/threatlib/tests/test_ingest.py`:

```python
from pathlib import Path

import pytest

from threatlib import service
from threatlib.attributes import ATTRIBUTE_KEY
from threatlib.sync.ingest import IngestStats, classify, ingest
from threatlib.sync.manifest import Manifest, sha256_bytes


def w(root: Path, rel: str, data: bytes = b"<?php") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


@pytest.fixture
def repo(tmp_path):
    w(tmp_path, "threats/README.md", b"category readme")
    w(tmp_path, "threats/readme-builder.php", b"builder")
    w(tmp_path, "threats/FIO-1/README.md", b"# FIO-1\n")
    w(tmp_path, "threats/FIO-1/a.php", b"<?php a();")
    w(tmp_path, "threats/FIO-1/.hidden", b"dot")
    w(tmp_path, "threats/sources/0154/wp-admin/menu.php", b"<?php m();")
    w(tmp_path, "threats/sources/0154/README.md", b"deep readme is a sample")
    w(tmp_path, "threats/empty_1/e.php", b"")
    w(tmp_path, "for-later-review/wf-1/x.php", b"<?php x();")
    w(tmp_path, "webshells/c99.php", b"<?php c99();")
    w(tmp_path, "webshells/helper/mass.php", b"<?php mass();")
    w(tmp_path, "escalated_issues_samples/E-1/z.php", b"<?php z();")
    w(tmp_path, "false-positives/wp/index.php", b"benign")
    return tmp_path


def test_classify(repo):
    c = lambda rel: classify(repo, repo / rel)  # noqa: E731
    assert c("threats/FIO-1/a.php") == ("threats", "FIO-1", "a.php", False, False)
    assert c("threats/FIO-1/README.md") == ("threats", "FIO-1", "README.md", False, True)
    assert c("threats/sources/0154/README.md") == ("threats", "sources", "0154/README.md", False, False)
    assert c("threats/sources/0154/wp-admin/menu.php") == ("threats", "sources", "0154/wp-admin/menu.php", False, False)
    assert c("webshells/c99.php") == ("webshells", "c99", "c99.php", True, False)
    assert c("webshells/helper/mass.php") == ("webshells", "helper", "mass.php", False, False)
    assert c("threats/README.md") is None
    assert c("threats/readme-builder.php") is None
    assert c("false-positives/wp/index.php") is None
    assert c(".mwdb-threatlib.json") is None


def test_first_run_imports_everything(repo, store):
    stats = ingest(repo, None, store)
    assert stats == IngestStats(new_files=9, new_threats=7, readmes_updated=1, unlinked=0, skipped=0, errors=0)

    fio1 = service.get_threat("FIO-1")
    assert fio1.category == "threats" and fio1.readme == "# FIO-1\n" and fio1.flat is False
    assert sorted(l.rel_path for l in fio1.samples) == [".hidden", "a.php"]

    sources = service.get_threat("sources")
    assert sorted(l.rel_path for l in sources.samples) == ["0154/README.md", "0154/wp-admin/menu.php"]
    assert sources.readme is None

    empty = service.get_threat("empty_1")
    assert empty.samples[0].object_id is None

    c99 = service.get_threat("c99")
    assert c99.flat is True and c99.category == "webshells"
    assert c99.samples[0].rel_path == "c99.php"
    helper = service.get_threat("helper")
    assert helper.flat is False

    f = store.by_sha[sha256_bytes(b"<?php a();")]
    assert f.tags == {"threats"} and f.attributes[ATTRIBUTE_KEY] == {"FIO-1"}
    assert service.get_threat("wp") is None  # false-positives ignored


def test_first_run_is_idempotent(repo, store):
    ingest(repo, None, store)
    stats = ingest(repo, None, store)
    assert stats.new_files == 0 and stats.new_threats == 0 and stats.readmes_updated == 0


def test_manifest_gates_new_files_and_deletions(repo, store):
    ingest(repo, None, store)
    manifest = Manifest(
        files={
            "threats/FIO-1/a.php": sha256_bytes(b"<?php a();"),
            "threats/FIO-1/.hidden": sha256_bytes(b"dot"),
            "threats/FIO-1/gone.php": sha256_bytes(b"gone"),
        },
        readmes={"threats/FIO-1": sha256_bytes(b"# FIO-1\n")},
    )
    fio1 = service.get_threat("FIO-1")
    service.link_sample(fio1, store.add(b"gone"), "gone.php")
    # a file MWDB deleted (in manifest, still on disk) must NOT come back:
    service.unlink_sample(fio1, "a.php")
    # a new file appears on disk:
    w(repo, "threats/FIO-1/b.php", b"<?php b();")
    # gone.php is in the manifest but no longer on disk => repo-side deletion

    stats = ingest(repo, manifest, store)
    assert stats.unlinked == 1 and stats.new_files >= 1
    names = sorted(l.rel_path for l in service.get_threat("FIO-1").samples)
    assert "b.php" in names and "gone.php" not in names and "a.php" not in names


def test_readme_rules(repo, store):
    ingest(repo, None, store)
    fio1 = service.get_threat("FIO-1")
    old_hash = sha256_bytes(b"# FIO-1\n")
    manifest = Manifest(files={}, readmes={"threats/FIO-1": old_hash})

    # repo edit, MWDB untouched -> repo wins
    w(repo, "threats/FIO-1/README.md", b"# repo edit\n")
    stats = ingest(repo, manifest, store)
    assert stats.readmes_updated == 1
    assert service.get_threat("FIO-1").readme == "# repo edit\n"

    # repo edit, MWDB also edited since manifest -> MWDB wins
    manifest = Manifest(files={}, readmes={"threats/FIO-1": sha256_bytes(b"# repo edit\n")})
    service.set_readme(service.get_threat("FIO-1"), "# mwdb edit\n")
    w(repo, "threats/FIO-1/README.md", b"# repo edit 2\n")
    stats = ingest(repo, manifest, store)
    assert stats.readmes_updated == 0
    assert service.get_threat("FIO-1").readme == "# mwdb edit\n"

    # repo deleted README, MWDB unchanged -> cleared
    manifest = Manifest(files={}, readmes={"threats/FIO-1": sha256_bytes(b"# mwdb edit\n")})
    (repo / "threats/FIO-1/README.md").unlink()
    stats = ingest(repo, manifest, store)
    assert stats.readmes_updated == 1 and service.get_threat("FIO-1").readme is None


def test_errors_are_counted_and_do_not_abort(repo, store, monkeypatch):
    real = store.get_or_create

    def flaky(file_name, stream):
        if file_name == "x.php":
            raise RuntimeError("storage down")
        return real(file_name, stream)

    monkeypatch.setattr(store, "get_or_create", flaky)
    stats = ingest(repo, None, store)
    assert stats.errors == 1 and stats.new_files == 8
    # the failed file's transaction was rolled back (threat creation included);
    # the next run will pick it up. Everything else landed.
    assert service.get_threat("wf-1") is None
    assert service.get_threat("FIO-1") is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd docker/plugins/threatlib && python -m pytest tests/test_ingest.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'threatlib.sync.ingest'`.

- [ ] **Step 3: Implement ingest**

`docker/plugins/threatlib/sync/ingest.py`:

```python
"""Ingest: absorb hand-made repo changes into MWDB before exporting.

Rules (spec §4 step 3, plus round-trip fidelity rules from repo inspection):
- The sync owns every subdirectory of each category dir, plus the regular
  files directly under webshells/ (flat, single-file threats). Regular files
  directly under the other category dirs are neither ingested nor deleted.
- A file is "new" when its repo-relative path is absent from the manifest.
- <category>/<name>/README.md is the threat README; deeper README.md files
  are ordinary samples.
- A path present in the manifest but missing on disk was deleted in the
  repo: unlink it.
- Empty files are linked with object_id NULL.
"""

from __future__ import annotations

import io
import logging
import posixpath
from dataclasses import dataclass
from pathlib import Path

from mwdb.model import db

from .. import service
from ..model import CATEGORIES
from ..validation import ValidationError
from .manifest import MANIFEST_NAME, Manifest, sha256_bytes
from .store import ObjectStore

logger = logging.getLogger("mwdb.plugin.threatlib.sync")


@dataclass
class IngestStats:
    new_files: int = 0
    new_threats: int = 0
    readmes_updated: int = 0
    unlinked: int = 0
    skipped: int = 0
    errors: int = 0


def classify(repo_path: Path, path: Path):
    """Map a repo file to (category, name, rel_path, flat, is_readme), or None
    when the sync does not own that path."""
    rel = path.relative_to(repo_path).as_posix()
    parts = rel.split("/")
    if parts[0] == MANIFEST_NAME or parts[0] not in CATEGORIES:
        return None
    category = parts[0]
    if len(parts) == 2:
        if category != "webshells":
            return None
        return category, posixpath.splitext(parts[1])[0], parts[1], True, False
    name = parts[1]
    rel_path = "/".join(parts[2:])
    is_readme = rel_path == "README.md"
    return category, name, rel_path, False, is_readme


def _iter_files(repo_path: Path):
    for category in CATEGORIES:
        cat_dir = repo_path / category
        if not cat_dir.is_dir():
            continue
        for path in sorted(p for p in cat_dir.rglob("*") if p.is_file()):
            yield path


def _get_or_create_threat(category, name, flat, stats):
    threat = service.get_threat(name)
    if threat is None:
        threat = service.create_threat(name, category, flat=flat, commit=False)
        stats.new_threats += 1
    return threat


def _ingest_sample(repo_path, path, category, name, rel_path, flat, store, stats):
    data = path.read_bytes()
    threat = _get_or_create_threat(category, name, flat, stats)
    if len(data) == 0:
        file_obj = None
    else:
        file_obj, _ = store.get_or_create(posixpath.basename(rel_path), io.BytesIO(data))
    _, created = service.link_sample(threat, file_obj, rel_path, commit=False)
    if created:
        stats.new_files += 1


def _ingest_readme(path, category, name, threat_dir, manifest, stats):
    data = path.read_bytes()
    new_hash = sha256_bytes(data)
    old_hash = manifest.readmes.get(threat_dir) if manifest else None
    if new_hash == old_hash:
        return
    threat = _get_or_create_threat(category, name, False, stats)
    current_hash = (
        None if threat.readme is None else sha256_bytes(threat.readme.encode("utf-8"))
    )
    if threat.readme is None or current_hash == old_hash:
        service.set_readme(threat, data.decode("utf-8", errors="replace"), commit=False)
        stats.readmes_updated += 1


def _clear_deleted_readme(threat_dir, old_hash, stats):
    name = threat_dir.split("/")[1]
    threat = service.get_threat(name)
    if threat is None or threat.readme is None:
        return
    if sha256_bytes(threat.readme.encode("utf-8")) == old_hash:
        service.set_readme(threat, None, commit=False)
        stats.readmes_updated += 1


def _unlink_deleted(manifest_key, stats):
    parts = manifest_key.split("/")
    if len(parts) < 2 or parts[0] not in CATEGORIES:
        return
    if len(parts) == 2:  # flat webshell
        name, rel_path = posixpath.splitext(parts[1])[0], parts[1]
    else:
        name, rel_path = parts[1], "/".join(parts[2:])
    threat = service.get_threat(name)
    if threat is None:
        return
    if service.unlink_sample(threat, rel_path, commit=False):
        stats.unlinked += 1


def ingest(repo_path: Path, manifest: Manifest | None, store: ObjectStore) -> IngestStats:
    repo_path = Path(repo_path)
    stats = IngestStats()
    previous_load = service._load_file
    service._load_file = store.load
    try:
        seen_files: set[str] = set()
        seen_readmes: set[str] = set()
        for path in _iter_files(repo_path):
            info = classify(repo_path, path)
            if info is None:
                continue
            category, name, rel_path, flat, is_readme = info
            key = path.relative_to(repo_path).as_posix()
            try:
                if is_readme:
                    threat_dir = f"{category}/{name}"
                    seen_readmes.add(threat_dir)
                    _ingest_readme(path, category, name, threat_dir, manifest, stats)
                else:
                    seen_files.add(key)
                    if manifest is not None and key in manifest.files:
                        continue
                    _ingest_sample(repo_path, path, category, name, rel_path, flat, store, stats)
                db.session.commit()
            except ValidationError as e:
                db.session.rollback()
                logger.warning("threatlib ingest: skipping %s: %s", key, e.message)
                stats.skipped += 1
            except Exception as e:
                db.session.rollback()
                logger.error("threatlib ingest: error on %s: %s", key, e)
                stats.errors += 1

        if manifest is not None:
            for key in sorted(set(manifest.files) - seen_files):
                try:
                    _unlink_deleted(key, stats)
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    logger.error("threatlib ingest: error unlinking %s: %s", key, e)
                    stats.errors += 1
            for threat_dir, old_hash in sorted(manifest.readmes.items()):
                if threat_dir in seen_readmes:
                    continue
                try:
                    _clear_deleted_readme(threat_dir, old_hash, stats)
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    logger.error("threatlib ingest: error clearing README %s: %s", threat_dir, e)
                    stats.errors += 1
    finally:
        service._load_file = previous_load
    return stats
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd docker/plugins/threatlib && python -m pytest tests/test_ingest.py -v
```

Expected: 6 passed. If `test_first_run_imports_everything` disagrees on counts, recount against the fixture: sample files are `FIO-1/a.php`, `FIO-1/.hidden`, `sources/0154/wp-admin/menu.php`, `sources/0154/README.md`, `empty_1/e.php`, `wf-1/x.php`, `c99.php`, `helper/mass.php`, `E-1/z.php` = 9; threats are `FIO-1, sources, empty_1, wf-1, c99, helper, E-1` = 7; one README.

- [ ] **Step 5: Lint and commit**

```bash
ruff check --fix docker/plugins/threatlib && ruff format docker/plugins/threatlib
git add docker/plugins/threatlib
git commit -m "threatlib: sync ingest with manifest-gated new/deleted detection

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Export and round-trip

**Files:**
- Create: `docker/plugins/threatlib/sync/export.py`
- Test: `docker/plugins/threatlib/tests/test_export.py`

**Interfaces:**
- Consumes: `model.Threat`, `model.ThreatSample`, `store.read`, `manifest.*`.
- Produces: `@dataclass ExportStats(files_written: int = 0, readmes_written: int = 0, threats: int = 0)`; `export(repo_path: Path, store: ObjectStore) -> tuple[Manifest, ExportStats]`; `clear_owned(repo_path: Path) -> None` (deletes every subdirectory of each category dir, and regular files directly under `webshells/`; leaves category-root files elsewhere untouched).
- Layout written: nested threat → `<category>/<name>/<rel_path>` (+ `README.md` when `readme` is set, encoded UTF-8); flat threat → `<category>/<rel_path>`. `Manifest.files` holds every sample path written; `Manifest.readmes` holds `<category>/<name>` → sha256 of the README bytes. `save_manifest` is called by `export` so the manifest is part of the tree.

- [ ] **Step 1: Write the failing tests**

`docker/plugins/threatlib/tests/test_export.py`:

```python
from pathlib import Path

from threatlib import service
from threatlib.sync.export import ExportStats, clear_owned, export
from threatlib.sync.ingest import ingest
from threatlib.sync.manifest import MANIFEST_NAME, load_manifest, sha256_bytes


def w(root: Path, rel: str, data: bytes = b"<?php") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != MANIFEST_NAME
    }


def test_clear_owned_keeps_category_root_files(tmp_path):
    w(tmp_path, "threats/README.md", b"keep")
    w(tmp_path, "threats/FIO-1/a.php")
    w(tmp_path, "webshells/c99.php")
    w(tmp_path, "webshells/helper/x.php")
    w(tmp_path, "false-positives/x.php", b"keep")
    clear_owned(tmp_path)
    assert tree(tmp_path) == {"threats/README.md": b"keep", "false-positives/x.php": b"keep"}
    assert (tmp_path / "threats").is_dir() and (tmp_path / "webshells").is_dir()


def test_export_writes_layout_and_manifest(tmp_path, store):
    t = service.create_threat("FIO-1", "threats", readme="# FIO-1\n")
    service.link_sample(t, store.add(b"<?php a();"), "a.php")
    service.link_sample(t, store.add(b"<?php n();"), "0154/wp-admin/menu.php")
    service.link_sample(t, None, "empty.php")
    c = service.create_threat("c99", "webshells", flat=True)
    service.link_sample(c, store.add(b"<?php c();"), "c99.php")
    w(tmp_path, "threats/README.md", b"root")

    manifest, stats = export(tmp_path, store)
    assert stats == ExportStats(files_written=4, readmes_written=1, threats=2)
    assert tree(tmp_path) == {
        "threats/README.md": b"root",
        "threats/FIO-1/README.md": b"# FIO-1\n",
        "threats/FIO-1/a.php": b"<?php a();",
        "threats/FIO-1/0154/wp-admin/menu.php": b"<?php n();",
        "threats/FIO-1/empty.php": b"",
        "webshells/c99.php": b"<?php c();",
    }
    assert manifest.files == {
        "threats/FIO-1/a.php": sha256_bytes(b"<?php a();"),
        "threats/FIO-1/0154/wp-admin/menu.php": sha256_bytes(b"<?php n();"),
        "threats/FIO-1/empty.php": sha256_bytes(b""),
        "webshells/c99.php": sha256_bytes(b"<?php c();"),
    }
    assert manifest.readmes == {"threats/FIO-1": sha256_bytes(b"# FIO-1\n")}
    assert load_manifest(tmp_path).files == manifest.files


def test_export_removes_stale_files(tmp_path, store):
    t = service.create_threat("FIO-1", "threats")
    service.link_sample(t, store.add(b"x"), "a.php")
    w(tmp_path, "threats/FIO-1/stale.php", b"stale")
    w(tmp_path, "threats/OLD/o.php", b"old")
    export(tmp_path, store)
    assert tree(tmp_path) == {"threats/FIO-1/a.php": b"x"}


def test_round_trip_is_byte_identical(tmp_path, store):
    src = tmp_path / "src"
    w(src, "threats/README.md", b"category readme")
    w(src, "threats/readme-builder.php", b"builder")
    w(src, "threats/FIO-1/README.md", "# FIO-1 ü\n".encode("utf-8"))
    w(src, "threats/FIO-1/a.php", b"<?php a();")
    w(src, "threats/FIO-1/.hidden", b"dot")
    w(src, "threats/FIO-1/dup.php", b"<?php a();")  # same bytes, second path
    w(src, "threats/sources/0154/wp-admin/menu.php", b"<?php m();")
    w(src, "threats/sources/0154/README.md", b"deep readme is a sample")
    w(src, "threats/empty_1/e.php", b"")
    w(src, "for-later-review/wf-1/x.php", b"<?php x();")
    w(src, "for-later-review/README.md", b"flr root")
    w(src, "webshells/c99.php", b"<?php c99();")
    w(src, "webshells/helper/mass.php", b"<?php mass();")
    w(src, "escalated_issues_samples/E-1/z.php", b"<?php z();")
    w(src, "false-positives/wp/index.php", b"benign")
    before = tree(src)

    ingest(src, None, store)
    manifest, _ = export(src, store)
    assert tree(src) == before

    # second cycle from the exported tree changes nothing
    stats = ingest(src, manifest, store)
    assert stats.new_files == 0 and stats.unlinked == 0 and stats.readmes_updated == 0
    export(src, store)
    assert tree(src) == before
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd docker/plugins/threatlib && python -m pytest tests/test_export.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'threatlib.sync.export'`.

- [ ] **Step 3: Implement export**

`docker/plugins/threatlib/sync/export.py`:

```python
"""Export: regenerate the owned parts of the repo tree from the tables."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from mwdb.model import db

from ..model import CATEGORIES, Threat
from .manifest import Manifest, save_manifest, sha256_bytes
from .store import ObjectStore

logger = logging.getLogger("mwdb.plugin.threatlib.sync")


@dataclass
class ExportStats:
    files_written: int = 0
    readmes_written: int = 0
    threats: int = 0


def clear_owned(repo_path: Path) -> None:
    repo_path = Path(repo_path)
    for category in CATEGORIES:
        cat_dir = repo_path / category
        cat_dir.mkdir(exist_ok=True)
        for entry in cat_dir.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry)
            elif category == "webshells" and entry.is_file():
                entry.unlink()


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def export(repo_path: Path, store: ObjectStore) -> tuple[Manifest, ExportStats]:
    repo_path = Path(repo_path)
    clear_owned(repo_path)
    manifest = Manifest()
    stats = ExportStats()
    threats = db.session.query(Threat).order_by(Threat.name).all()
    for threat in threats:
        stats.threats += 1
        base = repo_path / threat.category
        if not threat.flat:
            base = base / threat.name
        for link in sorted(threat.samples, key=lambda l: l.rel_path):
            data = b"" if link.object_id is None else store.read(link.object_id)
            _write(base / link.rel_path, data)
            key = (base / link.rel_path).relative_to(repo_path).as_posix()
            manifest.files[key] = sha256_bytes(data)
            stats.files_written += 1
        if not threat.flat and threat.readme is not None:
            data = threat.readme.encode("utf-8")
            _write(base / "README.md", data)
            manifest.readmes[f"{threat.category}/{threat.name}"] = sha256_bytes(data)
            stats.readmes_written += 1
    save_manifest(repo_path, manifest)
    logger.info(
        "threatlib export: %d threats, %d files, %d READMEs",
        stats.threats, stats.files_written, stats.readmes_written,
    )
    return manifest, stats
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd docker/plugins/threatlib && python -m pytest tests/test_export.py tests/test_ingest.py -v
```

Expected: all passed. The round-trip test is the property the design rests on; if it fails, fix `classify`/`clear_owned`, never the test.

- [ ] **Step 5: Lint and commit**

```bash
ruff check --fix docker/plugins/threatlib && ruff format docker/plugins/threatlib
git add docker/plugins/threatlib
git commit -m "threatlib: sync export with manifest and byte-identical round trip

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Runner (one run, loop, console script)

**Files:**
- Create: `docker/plugins/threatlib/sync/runner.py`
- Test: `docker/plugins/threatlib/tests/test_runner.py`

**Interfaces:**
- Consumes: `GitRepo`, `load_manifest`, `ingest`, `export`, `MwdbStore`, `service.links_for_object` (repair pass), `attributes.ensure_attribute_definition`.
- Produces: `@dataclass SyncConfig(repo_url, clone_dir, deploy_key, interval, push, author, branch="trunk", share_with="public")` with `SyncConfig.from_env() -> SyncConfig`; `run_once(config: SyncConfig, store: ObjectStore, repo: GitRepo | None = None) -> RunResult`; `@dataclass RunResult(head_before: str, ingest: IngestStats, export: ExportStats, commit: str | None, pushed: bool)`; `repair_mirror(store) -> int` (number of files touched); `main(argv=None) -> int` (parses `--once`, boots the Flask app via `mwdb.cli.base.create_app`, loops).
- Commit message format: `threatlib sync: +{new_files} files, -{unlinked} unlinked, {readmes_updated} READMEs ingested; exported {threats} threats / {files_written} files`.

- [ ] **Step 1: Write the failing tests**

`docker/plugins/threatlib/tests/test_runner.py`:

```python
import subprocess
from pathlib import Path

import pytest

from threatlib import service
from threatlib.sync.manifest import MANIFEST_NAME
from threatlib.sync.repo import GitRepo
from threatlib.sync.runner import SyncConfig, repair_mirror, run_once


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def w(root: Path, rel: str, data: bytes = b"<?php") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


@pytest.fixture
def remote(tmp_path):
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "trunk", str(bare))
    work = tmp_path / "teammate"
    _git(tmp_path, "clone", "-q", "-b", "trunk", str(bare), str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Teammate")
    w(work, "threats/README.md", b"root")
    w(work, "threats/FIO-1/README.md", b"# FIO-1\n")
    w(work, "threats/FIO-1/a.php", b"<?php a();")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "init")
    _git(work, "push", "-q", "origin", "trunk")
    return bare, work


def _config(tmp_path, bare, push=True):
    return SyncConfig(
        repo_url=str(bare), clone_dir=tmp_path / "clone", deploy_key=None,
        interval=1, push=push, author="Bot <bot@example.com>",
    )


def test_first_run_ingests_and_commits_manifest_only(tmp_path, remote, store):
    bare, work = remote
    result = run_once(_config(tmp_path, bare), store)
    assert result.ingest.new_files == 1 and result.ingest.new_threats == 1
    assert result.commit is not None and result.pushed is True
    _git(work, "pull", "-q")
    assert _git(work, "show", "--stat", "--format=", "HEAD").count("|") == 1
    assert (work / MANIFEST_NAME).exists()
    assert (work / "threats/FIO-1/a.php").read_bytes() == b"<?php a();"


def test_clean_second_run_makes_no_commit(tmp_path, remote, store):
    bare, work = remote
    run_once(_config(tmp_path, bare), store)
    result = run_once(_config(tmp_path, bare), store)
    assert result.commit is None and result.pushed is False


def test_mwdb_changes_are_exported_and_repo_changes_ingested(tmp_path, remote, store):
    bare, work = remote
    cfg = _config(tmp_path, bare)
    run_once(cfg, store)

    # MWDB: add a sample, delete one, edit a README
    fio1 = service.get_threat("FIO-1")
    service.link_sample(fio1, store.add(b"<?php b();"), "b.php")
    service.unlink_sample(fio1, "a.php")
    service.set_readme(fio1, "# from mwdb\n")
    # repo: teammate adds a threat
    _git(work, "pull", "-q")
    w(work, "for-later-review/wf-9/x.php", b"<?php x();")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "teammate")
    _git(work, "push", "-q", "origin", "trunk")

    result = run_once(cfg, store)
    assert result.ingest.new_files == 1 and result.commit is not None
    _git(work, "pull", "-q")
    assert not (work / "threats/FIO-1/a.php").exists()
    assert (work / "threats/FIO-1/b.php").exists()
    assert (work / "threats/FIO-1/README.md").read_bytes() == b"# from mwdb\n"
    assert (work / "for-later-review/wf-9/x.php").exists()
    assert service.get_threat("wf-9") is not None
    assert "+1 files" in _git(work, "log", "-1", "--format=%s")

    # a third run is clean, and a.php did not come back
    result = run_once(cfg, store)
    assert result.commit is None
    assert "a.php" not in [l.rel_path for l in service.get_threat("FIO-1").samples]


def test_push_disabled_leaves_remote_untouched(tmp_path, remote, store):
    bare, work = remote
    result = run_once(_config(tmp_path, bare, push=False), store)
    assert result.commit is not None and result.pushed is False
    assert _git(work, "ls-remote", str(bare), "trunk").split()[0] == _git(work, "rev-parse", "HEAD")


def test_ingest_exception_skips_export_and_push(tmp_path, remote, store, monkeypatch):
    bare, work = remote
    import threatlib.sync.runner as runner

    def boom(*a, **k):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(runner, "ingest", boom)
    with pytest.raises(RuntimeError):
        run_once(_config(tmp_path, bare), store)
    assert not (tmp_path / "clone" / MANIFEST_NAME).exists()


def test_repair_mirror_reapplies_and_strips(tmp_path, store):
    from threatlib.attributes import ATTRIBUTE_KEY

    t = service.create_threat("FIO-1", "threats")
    f = store.add(b"x")
    service.link_sample(t, f, "a.php")
    f.tags.clear()
    f.attributes[ATTRIBUTE_KEY].add("stale-name")
    touched = repair_mirror(store)
    assert touched == 1
    assert f.tags == {"threats"} and f.attributes[ATTRIBUTE_KEY] == {"FIO-1"}


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("MWDB_THREATLIB_REPO_URL", "git@x:y.git")
    monkeypatch.setenv("MWDB_THREATLIB_DEPLOY_KEY", "/k")
    monkeypatch.setenv("MWDB_THREATLIB_SYNC_INTERVAL", "60")
    monkeypatch.setenv("MWDB_THREATLIB_PUSH", "0")
    monkeypatch.setenv("MWDB_THREATLIB_CLONE_DIR", "/data/r")
    cfg = SyncConfig.from_env()
    assert cfg.repo_url == "git@x:y.git" and cfg.deploy_key == "/k"
    assert cfg.interval == 60 and cfg.push is False and str(cfg.clone_dir) == "/data/r"
    assert cfg.branch == "trunk" and cfg.share_with == "public"
    monkeypatch.delenv("MWDB_THREATLIB_REPO_URL")
    with pytest.raises(SystemExit):
        SyncConfig.from_env()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd docker/plugins/threatlib && python -m pytest tests/test_runner.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'threatlib.sync.runner'`.

- [ ] **Step 3: Implement the runner**

`docker/plugins/threatlib/sync/runner.py`:

```python
"""One sync run and the `threatlib-sync` console script."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .. import service
from ..attributes import ATTRIBUTE_KEY
from ..model import Threat, ThreatSample
from .export import ExportStats, export
from .ingest import IngestStats, ingest
from .manifest import load_manifest
from .repo import GitError, GitRepo
from .store import MwdbStore, ObjectStore

logger = logging.getLogger("mwdb.plugin.threatlib.sync")


@dataclass
class SyncConfig:
    repo_url: str
    clone_dir: Path
    deploy_key: str | None
    interval: int
    push: bool
    author: str
    branch: str = "trunk"
    share_with: str | None = "public"

    @classmethod
    def from_env(cls) -> "SyncConfig":
        url = os.environ.get("MWDB_THREATLIB_REPO_URL")
        if not url:
            print("MWDB_THREATLIB_REPO_URL is required", file=sys.stderr)
            raise SystemExit(2)
        return cls(
            repo_url=url,
            clone_dir=Path(os.environ.get("MWDB_THREATLIB_CLONE_DIR", "/data/repo")),
            deploy_key=os.environ.get("MWDB_THREATLIB_DEPLOY_KEY") or None,
            interval=int(os.environ.get("MWDB_THREATLIB_SYNC_INTERVAL", "900")),
            push=os.environ.get("MWDB_THREATLIB_PUSH", "1") not in ("0", "false", "no"),
            author=os.environ.get(
                "MWDB_THREATLIB_GIT_AUTHOR", "mwdb-threatlib-bot <noreply@wafflemakers.xyz>"
            ),
            branch=os.environ.get("MWDB_THREATLIB_BRANCH", "trunk"),
            share_with=os.environ.get("MWDB_THREATLIB_SHARE_WITH", "public") or None,
        )


@dataclass
class RunResult:
    head_before: str
    ingest: IngestStats
    export: ExportStats
    commit: str | None
    pushed: bool


def repair_mirror(store: ObjectStore) -> int:
    """Ensure every linked file carries its category tag and threat-name
    attribute, and no stale threat-name attribute. Returns files touched."""
    from mwdb.model import db

    touched = 0
    rows = (
        db.session.query(ThreatSample.object_id)
        .filter(ThreatSample.object_id.isnot(None))
        .distinct()
        .all()
    )
    for (object_id,) in rows:
        file_obj = store.load(object_id)
        if file_obj is None:
            continue
        links = service.links_for_object(object_id)
        wanted_tags = {c for c, _ in links}
        wanted_names = {n for _, n in links}
        changed = False
        for tag in wanted_tags:
            if file_obj.get_tag(tag) is None:
                file_obj.add_tag(tag, commit=False)
                changed = True
        current_names = _current_names(file_obj)
        for name in wanted_names - current_names:
            file_obj.add_attribute(ATTRIBUTE_KEY, name, commit=False, check_permissions=False)
            changed = True
        for name in current_names - wanted_names:
            file_obj.remove_attribute(ATTRIBUTE_KEY, name, check_permissions=False)
            changed = True
        if changed:
            touched += 1
            db.session.commit()
    return touched


def _current_names(file_obj) -> set[str]:
    # FakeFile exposes .attributes[key]; mwdb File exposes get_attributes(as_dict=True)
    attrs = getattr(file_obj, "attributes", None)
    if isinstance(attrs, dict):
        return set(attrs.get(ATTRIBUTE_KEY, set()))
    values = file_obj.get_attributes(as_dict=True, check_permissions=False)
    return {str(v) for v in values.get(ATTRIBUTE_KEY, [])}


def _commit_message(ingest_stats: IngestStats, export_stats: ExportStats) -> str:
    return (
        f"threatlib sync: +{ingest_stats.new_files} files, "
        f"-{ingest_stats.unlinked} unlinked, "
        f"{ingest_stats.readmes_updated} READMEs ingested; "
        f"exported {export_stats.threats} threats / {export_stats.files_written} files"
    )


def run_once(config: SyncConfig, store: ObjectStore, repo: GitRepo | None = None) -> RunResult:
    repo = repo or GitRepo(
        config.clone_dir, config.repo_url, branch=config.branch,
        deploy_key=config.deploy_key, author=config.author,
    )
    repo.ensure_clone()
    head_before = repo.reset_to_remote()
    manifest = load_manifest(repo.path)

    ingest_stats = ingest(repo.path, manifest, store)
    repair_mirror(store)
    _, export_stats = export(repo.path, store)

    commit = None
    pushed = False
    if repo.is_dirty():
        commit = repo.commit_and_push(_commit_message(ingest_stats, export_stats), push=config.push)
        pushed = bool(commit) and config.push
    logger.info(
        "threatlib sync: head=%s ingest=%s export=%s commit=%s pushed=%s",
        head_before[:12], ingest_stats, export_stats, (commit or "")[:12], pushed,
    )
    return RunResult(head_before, ingest_stats, export_stats, commit, pushed)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="threatlib-sync")
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config = SyncConfig.from_env()

    from flask import g

    from mwdb.cli.base import create_app

    app = create_app()
    store = MwdbStore(config.share_with)
    while True:
        with app.app_context():
            g.auth_user = None
            try:
                from ..attributes import ensure_attribute_definition

                ensure_attribute_definition()
                run_once(config, store)
            except GitError as e:
                logger.error("threatlib sync: git failure, will retry: %s", e)
            except Exception:
                logger.exception("threatlib sync: run failed, will retry")
                from mwdb.model import db

                db.session.rollback()
        if args.once:
            return 0
        time.sleep(config.interval)
```

Note on `Threat` import: it is used only for typing in this module; if ruff flags it unused, drop it.

- [ ] **Step 4: Run the whole suite**

```bash
cd docker/plugins/threatlib && python -m pytest tests/ -v
```

Expected: all passed.

- [ ] **Step 5: Lint and commit**

```bash
ruff check --fix docker/plugins/threatlib && ruff format docker/plugins/threatlib
git add docker/plugins/threatlib
git commit -m "threatlib: sync runner, mirror repair pass and console script

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: Sidecar image, compose wiring and docs

**Files:**
- Create: `docker/threatlib-sync/Dockerfile`
- Modify: `compose/compose.with-plugins.yml`
- Modify: `docker-compose-prod.yml`
- Create: `docker/plugins/threatlib/README.md`
- Modify: `CLAUDE.md` (Fork-specific additions: plugin list + sidecar)
- Modify: `deploy/DEPLOYMENT.md` (rollout section)

**Interfaces:**
- Produces: service `threatlib-sync` in dev overlay and prod compose; `MWDB_PLUGINS` includes `threatlib` for `mwdb` and `threatlib-sync`; prod volume `threatlib-repo`; deploy key mounted read-only at `/run/secrets/threatlib_deploy_key` from `./secrets/threatlib_deploy_key` (git-ignored).

- [ ] **Step 1: Write the sidecar Dockerfile**

`docker/threatlib-sync/Dockerfile` (context is the repo root; mirrors `deploy/docker/Dockerfile` with git added):

```dockerfile
# syntax=docker/dockerfile:1
# threatlib-sync sidecar: the backend image plus git/ssh, running the
# `threatlib-sync` console script instead of gunicorn. Mirrors
# deploy/docker/Dockerfile; keep the two in step when upstream changes.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app
ENV UV_NO_DEV=1
ENV UV_PYTHON_DOWNLOADS=0
ENV UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ python3-dev \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project

COPY mwdb /app/mwdb
COPY docker/ pyproject.toml uv.lock /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install /app

RUN --mount=type=cache,target=/root/.cache/uv \
    for plugin in $(find /app/plugins \( -name 'setup.py' -o -name 'pyproject.toml' \) -exec dirname {} \; | sort -u); \
    do uv pip --no-cache-dir install $plugin; done

FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libpq5 postgresql-client libmagic1 libfuzzy2 git openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder --chown=nobody:nogroup /app /app
ENV PATH="/app/.venv/bin:$PATH"

RUN mkdir -p /data/repo && chown nobody:nogroup /data /data/repo
USER nobody
RUN mkdir -m 700 /app/uploads

ENV PYTHONPATH=/app
ENV FLASK_APP=/app/mwdb/app.py
WORKDIR /app

CMD ["threatlib-sync"]
```

- [ ] **Step 2: Wire the dev overlay**

`compose/compose.with-plugins.yml` becomes:

```yaml
# Enables Waffle Makers in-tree plugins (yarax_regex, phpdeobf, threatlib) and their sidecars
services:
  mwdb:
    environment:
      MWDB_PLUGINS: "yarax_regex,phpdeobf,threatlib"
      MWDB_PHPDEOBF_URL: "http://phpdeobf:8080"
  phpdeobf:
    build:
      context: ./docker/phpdeobf
      dockerfile: Dockerfile.server
    restart: unless-stopped
  threatlib-sync:
    build:
      context: .
      dockerfile: docker/threatlib-sync/Dockerfile
    depends_on:
      mwdb:
        condition: service_healthy
    restart: unless-stopped
    env_file:
      - mwdb-vars.env
    environment:
      MWDB_PLUGINS: "yarax_regex,phpdeobf,threatlib"
      MWDB_ENABLE_HOOKS: "0"
      # Dev default: sync against a local bare repo mounted at /data/origin.git.
      # Create it with:  git init --bare -b trunk ./dev-threatlib-origin.git
      MWDB_THREATLIB_REPO_URL: "${MWDB_THREATLIB_REPO_URL:-/data/origin.git}"
      MWDB_THREATLIB_SYNC_INTERVAL: "${MWDB_THREATLIB_SYNC_INTERVAL:-60}"
      MWDB_THREATLIB_PUSH: "${MWDB_THREATLIB_PUSH:-1}"
      MWDB_THREATLIB_DEPLOY_KEY: "${MWDB_THREATLIB_DEPLOY_KEY:-}"
    volumes:
      - mwdb-uploads:/app/uploads
      - threatlib-repo:/data/repo
      - ./dev-threatlib-origin.git:/data/origin.git
      - ./docker/plugins:/app/plugins
volumes:
  threatlib-repo:
```

Add `dev-threatlib-origin.git/` to `.gitignore`.

- [ ] **Step 3: Wire prod**

In `docker-compose-prod.yml`, change `MWDB_PLUGINS=yarax_regex,phpdeobf` to `MWDB_PLUGINS=yarax_regex,phpdeobf,threatlib` on the `mwdb` service, and add after `phpdeobf:`:

```yaml
  threatlib-sync:
    build:
      context: .
      dockerfile: docker/threatlib-sync/Dockerfile
    depends_on:
      - mwdb
    restart: unless-stopped
    env_file:
      - mwdb-vars.env
    environment:
      - MWDB_PLUGINS=yarax_regex,phpdeobf,threatlib
      - MWDB_ENABLE_HOOKS=0
      - MWDB_THREATLIB_REPO_URL=git@github.a8c.com:Automattic/jetpack-threat-library.git
      - MWDB_THREATLIB_DEPLOY_KEY=/run/secrets/threatlib_deploy_key
      - MWDB_THREATLIB_SYNC_INTERVAL=900
      # Rollout step 1: keep at 0 until the first export diff has been reviewed.
      - MWDB_THREATLIB_PUSH=${MWDB_THREATLIB_PUSH:-0}
    volumes:
      - mwdb-uploads:/app/uploads
      - threatlib-repo:/data/repo
      - ./secrets/threatlib_deploy_key:/run/secrets/threatlib_deploy_key:ro
```

and add `threatlib-repo:` under the top-level `volumes:`. Add `secrets/` to `.gitignore`.

- [ ] **Step 4: Build and smoke-test the dev stack**

```bash
git init --bare -b trunk ./dev-threatlib-origin.git
./gen_vars.sh test
./compose.sh --with dev --with plugins up --build -d
sleep 30
docker compose logs threatlib-sync | tail -20
docker compose logs mwdb | grep -i threatlib
```

Expected: `mwdb` logs `threatlib: registered /api/threatlib/* resources`; `threatlib-sync` logs one run with `ingest=IngestStats(...)` and a push into the bare repo (verify with `git --git-dir dev-threatlib-origin.git log --oneline`, which shows a `threatlib sync:` commit adding the manifest). If the bare repo has no commits yet, `git clone -b trunk` fails: seed it once with an empty commit from a scratch clone (`git clone dev-threatlib-origin.git /tmp/seed && cd /tmp/seed && git commit --allow-empty -m init && git push origin HEAD:trunk`).

- [ ] **Step 5: Write the plugin README**

`docker/plugins/threatlib/README.md`:

```markdown
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
```

- [ ] **Step 6: Update CLAUDE.md and DEPLOYMENT.md**

In `CLAUDE.md` under "In-tree plugins", add after the `phpdeobf` bullet:

```markdown
- `threatlib` — Threat library: first-class Threat entity, curation API under `/api/threatlib/`, "Threat library" pages in the SPA, and the `threatlib-sync` sidecar (`docker/threatlib-sync/Dockerfile`) that regenerates the four malware dirs of `jetpack-threat-library` from MWDB (ingest-first, manifest `.mwdb-threatlib.json`, bot push to `trunk`). See `docker/plugins/threatlib/README.md`.
```

and change the `MWDB_PLUGINS` sentence to mention that the sidecar service needs the same list. In `deploy/DEPLOYMENT.md`, add a section:

```markdown
## Threat library sync rollout

1. Create a deploy key with write access to `Automattic/jetpack-threat-library`
   on github.a8c.com; place the private key at `./secrets/threatlib_deploy_key`
   (mode 600, git-ignored).
2. `docker compose -f docker-compose-prod.yml up -d --build threatlib-sync`
   with `MWDB_THREATLIB_PUSH=0` (the default). The first run migrates existing
   `jpop_threat_name` samples, ingests everything the April import missed and
   writes the export into the `threatlib-repo` volume without pushing.
3. Review the diff: `docker compose -f docker-compose-prod.yml exec threatlib-sync
   git -C /data/repo status --short | head`. Expected: only `.mwdb-threatlib.json`
   added. Investigate anything else before continuing.
4. `MWDB_THREATLIB_PUSH=1 docker compose -f docker-compose-prod.yml up -d threatlib-sync`.
   The first pushed commit adds only the manifest.
5. Announce that MWDB is canonical for `threats/`, `for-later-review/`,
   `webshells/`, `escalated_issues_samples/`. Hand-made PRs still merge safely
   (ingest runs first); after a quiet period mark those dirs as generated in
   the repo README.
```

- [ ] **Step 7: Lint and commit**

```bash
ruff check --fix docker/plugins/threatlib && ruff format docker/plugins/threatlib
git add docker/threatlib-sync compose/compose.with-plugins.yml docker-compose-prod.yml .gitignore docker/plugins/threatlib/README.md CLAUDE.md deploy/DEPLOYMENT.md
git commit -m "threatlib: sync sidecar image, compose wiring and docs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 13: Backend e2e test over HTTP

**Files:**
- Create: `tests/backend/test_threatlib.py`

**Interfaces:**
- Consumes: the running stack (`./gen_vars.sh test && ./compose.sh --with dev --with plugins up -d`), `admin_session` fixture from `tests/backend/conftest.py` (`MwdbTest` with `.session`, `.mwdb_url`, `.add_sample`, `.get_tags`, `.get_attributes`, `.request`).

- [ ] **Step 1: Write the e2e test**

`tests/backend/test_threatlib.py`:

```python
"""End-to-end test for the threatlib plugin over HTTP.

Requires the dev compose stack with the `threatlib` plugin enabled.

Run from tests/backend/:
    uv run pytest test_threatlib.py -v
"""

import io
import uuid

import pytest


@pytest.fixture
def name():
    return "e2e-" + uuid.uuid4().hex[:10]


def _skip_if_missing(resp):
    if resp.status_code == 404 and "threat" not in resp.text.lower():
        pytest.skip("threatlib plugin endpoints not registered")


def test_upload_creates_threat_links_and_mirrors(admin_session, name):
    url = admin_session.mwdb_url + "/threatlib/upload"
    content_a = f"<?php echo '{name}-a';".encode()
    content_b = f"<?php echo '{name}-b';".encode()
    resp = admin_session.session.post(
        url,
        data={
            "threat": name,
            "category": "for-later-review",
            "readme": f"# {name}\n",
            "rel_paths": ["a.php", "sub/b.php"],
        },
        files=[
            ("files", ("a.php", io.BytesIO(content_a))),
            ("files", ("b.php", io.BytesIO(content_b))),
        ],
    )
    _skip_if_missing(resp)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["threat"]["category"] == "for-later-review"
    assert [r["status"] for r in body["results"]] == ["new", "new"]
    sha_a = body["results"][0]["sha256"]

    tags = [t["tag"] for t in admin_session.get_tags(sha_a)]
    assert "for-later-review" in tags
    attrs = admin_session.get_attributes(sha_a)["attributes"]
    assert any(a["key"] == "jpop_threat_name" and a["value"] == name for a in attrs)

    threat = admin_session.request("GET", f"/threatlib/threat/{name}")
    assert threat["sample_count"] == 2
    assert sorted(s["rel_path"] for s in threat["samples"]) == ["a.php", "sub/b.php"]

    # promote to threats: tag swaps on the samples
    threat = admin_session.request("PUT", f"/threatlib/threat/{name}", json={"category": "threats"})
    assert threat["category"] == "threats"
    tags = [t["tag"] for t in admin_session.get_tags(sha_a)]
    assert "threats" in tags and "for-later-review" not in tags

    # link an existing sample by hash
    other = admin_session.add_sample(filename="c.php", content=f"<?php '{name}-c';".encode())
    threat = admin_session.request(
        "POST", f"/threatlib/threat/{name}/sample", json={"sha256": other["sha256"], "rel_path": "c.php"}
    )
    assert threat["sample_count"] == 3

    # unlink it
    resp = admin_session.session.delete(
        admin_session.mwdb_url + f"/threatlib/threat/{name}/sample/{other['sha256']}",
        params={"rel_path": "c.php"},
    )
    assert resp.status_code == 200 and resp.json()["sample_count"] == 2
    tags = [t["tag"] for t in admin_session.get_tags(other["sha256"])]
    assert "threats" not in tags

    # list finds it by prefix
    listing = admin_session.request("GET", "/threatlib/threat", params={"query": name[:8]})
    assert any(t["name"] == name for t in listing["threats"])

    # delete: samples remain, mirror stripped
    resp = admin_session.session.delete(admin_session.mwdb_url + f"/threatlib/threat/{name}")
    assert resp.status_code == 200
    assert admin_session.session.get(admin_session.mwdb_url + f"/threatlib/threat/{name}").status_code == 404
    assert admin_session.get_sample(sha_a)["sha256"] == sha_a
    tags = [t["tag"] for t in admin_session.get_tags(sha_a)]
    assert "threats" not in tags


def test_validation_over_http(admin_session, name):
    resp = admin_session.session.post(
        admin_session.mwdb_url + "/threatlib/threat", json={"name": "bad name", "category": "threats"}
    )
    _skip_if_missing(resp)
    assert resp.status_code == 400
    resp = admin_session.session.post(
        admin_session.mwdb_url + "/threatlib/threat", json={"name": name, "category": "nope"}
    )
    assert resp.status_code == 400
    assert admin_session.session.post(
        admin_session.mwdb_url + "/threatlib/threat", json={"name": name, "category": "webshells"}
    ).status_code == 200
    assert admin_session.session.post(
        admin_session.mwdb_url + "/threatlib/threat", json={"name": name, "category": "webshells"}
    ).status_code == 409
    admin_session.session.delete(admin_session.mwdb_url + f"/threatlib/threat/{name}")
```

- [ ] **Step 2: Run it against the stack**

```bash
cd tests/backend
export MWDB_ADMIN_LOGIN=admin
export MWDB_ADMIN_PASSWORD=$(grep MWDB_ADMIN_PASSWORD ../../mwdb-vars.env | cut -d= -f2)
export MWDB_URL=http://127.0.0.1/api
uv run pytest test_threatlib.py -v
```

Expected: 2 passed. If `get_tags`/`get_attributes` shapes differ from the assertions, adjust the assertions to the real response (check `utils.py`), not the plugin.

- [ ] **Step 3: Commit**

```bash
git add tests/backend/test_threatlib.py
git commit -m "threatlib: backend e2e test over HTTP

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec coverage.** §3 model → Task 1 (+ `flat` column added for webshells fidelity, documented in Global Constraints). §3 mirror → Tasks 3, 4, 11 (repair pass). §3 catch-up migration → covered by the first run's manifest-less ingest (every repo path is "new", dedup by sha256 links existing April files under their exact paths) plus `repair_mirror`; files carrying `jpop_threat_name` but absent from the repo are left as-is (their attribute is stripped by the repair pass only if they have no links, which is the correct end state for the mirror). §4 sync loop → Tasks 7–12. §5 API → Tasks 5–6. §7 tests → every task; round-trip in Task 10. §8 rollout → Task 12 docs. §6 frontend → Part 2 plan.
- **Type consistency.** `service._load_file` is the single seam for object loading, patched by the `store` fixture and by `ingest`. `classify` and `_unlink_deleted` agree on the flat-webshell mapping. `ExportStats` field names match `_commit_message`.
- **Known deviation from spec §3.** `ThreatSample.object_id` is nullable (empty files), and `Threat.flat` exists. Both come from repo inspection and are required for the byte-identical round trip the spec's rollout depends on.
