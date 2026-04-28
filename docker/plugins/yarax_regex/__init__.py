"""YARA-X live regex playground for the MWDB sample detail page."""
import logging

from mwdb.core.plugins import PluginAppContext

__author__ = "Waffle Makers"
__version__ = "0.1.0"
__doc__ = "YARA-X live regex playground for the MWDB sample detail page."


logger = logging.getLogger("mwdb.plugin.yarax_regex")


def entrypoint(app_context: PluginAppContext) -> None:
    """Plugin entrypoint — wires up the Flask resource.

    Importing the resource module is deferred to avoid a circular import
    when this package is loaded before the main MWDB app finishes booting.
    """
    from .resource import YaraXRegexResource

    app_context.register_resource(YaraXRegexResource, "/yarax/regex")
    logger.info("Registered POST /api/yarax/regex")


__plugin_entrypoint__ = entrypoint
