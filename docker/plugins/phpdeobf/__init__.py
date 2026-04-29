"""PHP Deobfuscator plugin — runs the sample through the phpdeobf sidecar
and creates a deobfuscated TextBlob child of the sample."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mwdb.core.plugins import PluginAppContext

__author__ = "Waffle Makers"
__version__ = "0.1.0"
__doc__ = "PHP Deobfuscator plugin for the MWDB sample detail page."


logger = logging.getLogger("mwdb.plugin.phpdeobf")


def entrypoint(app_context: "PluginAppContext") -> None:
    """Plugin entrypoint — registers the Flask resource.

    The resource module import is deferred to avoid a circular import when
    this package is loaded before the main MWDB app finishes booting.
    """
    from .resource import PhpDeobfResource

    app_context.register_resource(
        PhpDeobfResource, "/phpdeobf/<hash64:identifier>"
    )
    logger.info("Registered POST /api/phpdeobf/<sample_id>")


__plugin_entrypoint__ = entrypoint
