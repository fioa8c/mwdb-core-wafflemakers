"""Shared pytest fixtures for the malleus plugin.

Adds the plugin's parent directory to sys.path so `import malleus...` works
without installing it, and stubs the `mwdb.*` modules (and flask, when it is
not installed) with MagicMock so the unit tests need no MWDB stack.
Mirrors phpdeobf/tests/conftest.py.
"""

import sys
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

if "mwdb.model" not in sys.modules:
    _stub = MagicMock()
    sys.modules["mwdb.model"] = _stub
    sys.modules["mwdb"].model = _stub

try:
    import flask  # noqa: F401
except ImportError:  # the hooks tests monkeypatch `g` anyway
    sys.modules["flask"] = MagicMock()
