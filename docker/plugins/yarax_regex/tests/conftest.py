"""Shared pytest fixtures for the yarax_regex plugin."""
import sys
from pathlib import Path

# Ensure the plugin package is importable when tests run from the repo root.
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))
