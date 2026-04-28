"""Shared pytest fixtures for the yarax_regex plugin."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Ensure the plugin package is importable when tests run from the repo root.
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# Pre-register lightweight stubs for `mwdb.resources` and `mwdb.model` in
# sys.modules so the resource module's top-level imports
# (`from mwdb.resources import requires_authorization`,
#  `from mwdb.model import File`) succeed without requiring the full MWDB
# dependency stack on the test host. The `app` fixture in test_resource.py
# then monkeypatches these stubs with per-test behavior.
#
# Note: conftest runs before pytest collects test modules, so the
# `if "mwdb.X" not in sys.modules` guard always fires here — the stubs are
# effectively unconditional. The guard exists as a safety net in case
# someone refactors test discovery to import the real modules earlier.
#
# The motivating issue: `magic` (the libmagic Python binding) doesn't find
# libmagic on Apple Silicon Homebrew paths, which would otherwise make
# `import mwdb.model` raise on macOS dev hosts. Inside the Debian-slim
# Docker container the real modules import fine — but Task 4's tests run
# on the host (the Docker container is exercised in Task 5's smoke test).

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
