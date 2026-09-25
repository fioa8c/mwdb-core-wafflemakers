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
    from . import attributes
    from .resource import PhpDeobfHealthResource, PhpDeobfResource

    try:
        attributes.ensure_attribute_definition()
    except Exception as e:  # schema may not exist yet during `mwdb-core configure`
        logger.warning("phpdeobf: deferred attribute setup until first request: %s", e)
        try:
            from mwdb.model import db

            db.session.rollback()
        except Exception:
            pass

    # Registered before the <hash64:identifier> route so "health" is never
    # parsed as a hash; hash64 only matches hex digests or "root", but keep
    # the order explicit.
    app_context.register_resource(PhpDeobfHealthResource, "/phpdeobf/health")
    app_context.register_resource(PhpDeobfResource, "/phpdeobf/<hash64:identifier>")
    logger.info("Registered POST /api/phpdeobf/<sample_id>")


__plugin_entrypoint__ = entrypoint
