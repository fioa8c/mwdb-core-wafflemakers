"""Shared pytest fixtures for the yarax_regex plugin."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Ensure the plugin package is importable when tests run from the repo root.
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# On the host (macOS), libmagic may not be on the default search path used by
# the `magic` package even though `brew install libmagic` is present.  This
# causes `mwdb.model` and `mwdb.resources` to fail to import before pytest
# even collects the test module.  Pre-register lightweight stubs so that
# (a) the test fixture can monkeypatch them, and (b) the resource module can
# import `from mwdb.resources import requires_authorization` and
# `from mwdb.model import File`.

def _identity_decorator(f):
    return f


if "mwdb.resources" not in sys.modules:
    _mwdb_resources_stub = MagicMock()
    _mwdb_resources_stub.requires_authorization = _identity_decorator
    sys.modules["mwdb.resources"] = _mwdb_resources_stub
    # Also set as an attribute on the mwdb package so that
    # `import mwdb.resources; mwdb.resources.attr` works correctly.
    import mwdb as _mwdb_pkg
    _mwdb_pkg.resources = _mwdb_resources_stub

if "mwdb.model" not in sys.modules:
    _mwdb_model_stub = MagicMock()
    sys.modules["mwdb.model"] = _mwdb_model_stub
    import mwdb as _mwdb_pkg
    _mwdb_pkg.model = _mwdb_model_stub
