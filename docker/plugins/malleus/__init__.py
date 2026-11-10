"""Malleus plugin: attribute definitions, reporter permissions and the
`context` / `source:` contracts for the Malleus Karton service.

The service itself (`malleus-static`) runs outside MWDB and reports through
karton-mwdb-reporter; this plugin only prepares MWDB to receive its output.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from mwdb.core.plugins import PluginAppContext

__author__ = "Waffle Makers"
__version__ = "0.1.0"
__doc__ = "Malleus second-opinion attributes and contracts for MWDB."

logger = logging.getLogger("mwdb.plugin.malleus")


DEFAULT_SET_GROUPS = "karton"
DEFAULT_READ_GROUPS = "jetpack-scan,wpcloud-scan,elderward"
DEFAULT_REPORTER_LOGINS = "karton"


def _split(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _env_list(name: str, default: str) -> List[str]:
    return _split(os.environ.get(name, default))


def configure() -> None:
    """Create the attribute definitions and sync the group permissions.

    Called by MWDB through `__plugin_configure__` once per `mwdb-core
    configure`, after the migrations; `start.sh` runs that on every start.
    """
    from mwdb.model import db

    from . import attributes

    try:
        attributes.ensure_attribute_definitions()
        attributes.ensure_group_permissions(
            _env_list("MWDB_MALLEUS_SET_GROUPS", DEFAULT_SET_GROUPS),
            _env_list("MWDB_MALLEUS_READ_GROUPS", DEFAULT_READ_GROUPS),
        )
    except Exception as e:
        # Never block MWDB from starting over this; the next start retries.
        logger.warning("malleus: setup failed, retried on next start: %s", e)
        try:
            db.session.rollback()
        except Exception:
            pass


def entrypoint(app_context: "PluginAppContext") -> None:
    # Deferred imports: this package is loaded before the MWDB app finishes booting.
    from . import upload_guard
    from .hooks import MalleusHookHandler

    MalleusHookHandler.reporter_logins = tuple(
        _env_list("MWDB_MALLEUS_REPORTER_LOGINS", DEFAULT_REPORTER_LOGINS)
    )
    app_context.register_hook_handler(MalleusHookHandler)
    upload_guard.register()
    logger.info("Registered malleus hook handler and upload guard")


__plugin_entrypoint__ = entrypoint
__plugin_configure__ = configure
