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
