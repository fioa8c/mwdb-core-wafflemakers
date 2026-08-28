"""Shared pytest fixtures for the wpsandbox plugin.

Adds the plugin package to sys.path so `import wpsandbox...` works without
installing it. Mirrors yarax_regex/tests/conftest.py.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def _identity_decorator(f):
    return f


# Stub out the top-level `mwdb` package first so sub-module stubs can attach.
if "mwdb" not in sys.modules:
    sys.modules["mwdb"] = MagicMock()

# Pre-stub mwdb.resources / mwdb.model so the resource module's top-level
# imports succeed without the full MWDB stack (libmagic etc. on macOS hosts).
# test_resource.py monkeypatches these stubs with per-test behaviour.
if "mwdb.resources" not in sys.modules:
    _stub = MagicMock()
    _stub.requires_authorization = _identity_decorator
    sys.modules["mwdb.resources"] = _stub
    import mwdb as _mwdb_pkg
    _mwdb_pkg.resources = _stub

if "mwdb.model" not in sys.modules:
    _stub = MagicMock()
    sys.modules["mwdb.model"] = _stub
    import mwdb as _mwdb_pkg
    _mwdb_pkg.model = _stub

if "mwdb.core" not in sys.modules:
    _stub = MagicMock()
    sys.modules["mwdb.core"] = _stub
    import mwdb as _mwdb_pkg
    _mwdb_pkg.core = _stub

if "mwdb.core.service" not in sys.modules:
    from flask.views import MethodView

    # Resource must be a real MethodView subclass so that as_view() and
    # HTTP-method dispatch work correctly in the test Flask app.
    class _FakeResource(MethodView):
        pass

    _stub = MagicMock()
    _stub.Resource = _FakeResource
    sys.modules["mwdb.core.service"] = _stub
    import mwdb.core as _mwdb_core
    _mwdb_core.service = _stub

if "mwdb.core.hooks" not in sys.modules:
    _stub = MagicMock()
    sys.modules["mwdb.core.hooks"] = _stub
    import mwdb.core as _mwdb_core
    _mwdb_core.hooks = _stub

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

# Physically create these two stand-in tables now (declaring them in metadata
# is not enough — resource tests execute raw SQL against `object` directly).
# wpsandbox_run is created later by model.ensure_schema().
_Base.metadata.create_all(
    _engine,
    tables=[_Base.metadata.tables["object"], _Base.metadata.tables["user"]],
)

if "mwdb.core.capabilities" not in sys.modules:
    _caps = MagicMock()
    _caps.Capabilities.adding_blobs = "adding_blobs"
    _caps.Capabilities.manage_users = "manage_users"
    sys.modules["mwdb.core.capabilities"] = _caps
    import mwdb.core as _mwdb_core
    _mwdb_core.capabilities = _caps
