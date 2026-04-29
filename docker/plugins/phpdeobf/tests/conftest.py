"""Shared pytest fixtures for the phpdeobf plugin.

Adds the plugin package to sys.path so `import phpdeobf...` works without
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
