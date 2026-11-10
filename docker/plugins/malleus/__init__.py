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


def _split(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def entrypoint(app_context: "PluginAppContext") -> None:
    # Deferred imports: this package is loaded before the MWDB app finishes booting.
    from . import attributes
    from .hooks import MalleusHookHandler

    try:
        attributes.ensure_attribute_definitions()
        attributes.ensure_group_permissions(
            _split(os.environ.get("MWDB_MALLEUS_SET_GROUPS", "karton")),
            _split(
                os.environ.get(
                    "MWDB_MALLEUS_READ_GROUPS", "jetpack-scan,wpcloud-scan,elderward"
                )
            ),
        )
    except Exception as e:  # schema may not exist yet during `mwdb-core configure`
        logger.warning("malleus: deferred setup until next start: %s", e)
        try:
            from mwdb.model import db

            db.session.rollback()
        except Exception:
            pass

    app_context.register_hook_handler(MalleusHookHandler)
    logger.info("Registered malleus hook handler")


__plugin_entrypoint__ = entrypoint
