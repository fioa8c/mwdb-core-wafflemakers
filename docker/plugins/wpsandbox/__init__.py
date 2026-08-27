"""WordPress sandbox plugin — runs samples in a SecEx WordPress microVM
and attaches behavioural reports to the sample."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mwdb.core.plugins import PluginAppContext

__author__ = "Waffle Makers"
__version__ = "0.1.0"
__doc__ = "WordPress sandbox (SecEx) plugin for the MWDB sample detail page."

logger = logging.getLogger("mwdb.plugin.wpsandbox")


def entrypoint(app_context: "PluginAppContext") -> None:
    # Deferred imports: this package is loaded before the MWDB app finishes booting.
    from . import attributes, model
    from .resource import WpSandboxRunListResource, WpSandboxRunResource

    try:
        model.ensure_schema()
        attributes.ensure_attribute_definitions()
    except Exception as e:  # schema may not exist yet during `mwdb-core configure`
        logger.warning("wpsandbox: deferred setup until first request: %s", e)

    app_context.register_resource(WpSandboxRunListResource, "/wpsandbox/<hash64:identifier>")
    app_context.register_resource(WpSandboxRunResource, "/wpsandbox/run/<run_id>")
    logger.info("Registered /api/wpsandbox/<sample> and /api/wpsandbox/run/<id>")


__plugin_entrypoint__ = entrypoint
