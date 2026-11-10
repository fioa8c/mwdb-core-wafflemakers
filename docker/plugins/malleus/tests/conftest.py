"""Shared pytest fixtures for the malleus plugin.

Adds the plugin's parent directory to sys.path so `import malleus...` works
without installing it, and stubs the `mwdb.*` modules (and flask, when it is
not installed) with MagicMock and, when werkzeug is missing, its BadRequest
with a real exception class, so the unit tests need no MWDB stack.
Mirrors phpdeobf/tests/conftest.py.
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

PLUGINS_DIR = Path(__file__).resolve().parents[2]
if str(PLUGINS_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGINS_DIR))


class _FakeHookHandler:
    """Real class so `class MalleusHookHandler(HookHandler)` subclasses cleanly."""


if "mwdb" not in sys.modules:
    sys.modules["mwdb"] = MagicMock()

if "mwdb.core" not in sys.modules:
    _stub = MagicMock()
    sys.modules["mwdb.core"] = _stub
    sys.modules["mwdb"].core = _stub

if "mwdb.core.hooks" not in sys.modules:
    _stub = MagicMock()
    _stub.HookHandler = _FakeHookHandler
    sys.modules["mwdb.core.hooks"] = _stub
    sys.modules["mwdb.core"].hooks = _stub

if "mwdb.core.app" not in sys.modules:
    _stub = MagicMock()
    sys.modules["mwdb.core.app"] = _stub
    sys.modules["mwdb.core"].app = _stub

if "mwdb.model" not in sys.modules:
    _stub = MagicMock()
    sys.modules["mwdb.model"] = _stub
    sys.modules["mwdb"].model = _stub

try:
    import flask  # noqa: F401
except ImportError:  # the hooks and guard tests monkeypatch `g` / `request`
    sys.modules["flask"] = MagicMock()

try:
    import werkzeug.exceptions  # noqa: F401
except ImportError:

    class _FakeBadRequest(Exception):
        code = 400

        def __init__(self, description=None):
            super().__init__(description)
            self.description = description

    _stub = types.ModuleType("werkzeug.exceptions")
    _stub.BadRequest = _FakeBadRequest
    sys.modules["werkzeug"] = types.ModuleType("werkzeug")
    sys.modules["werkzeug"].exceptions = _stub
    sys.modules["werkzeug.exceptions"] = _stub
