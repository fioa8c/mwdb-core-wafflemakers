"""Threat library plugin — first-class Threat entity, curation API and
jetpack-threat-library repo sync."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mwdb.core.plugins import PluginAppContext

__author__ = "Waffle Makers"
__version__ = "0.1.0"
__doc__ = "Threat library plugin (Threat entity, curation API, repo sync)."

logger = logging.getLogger("mwdb.plugin.threatlib")


def entrypoint(app_context: "PluginAppContext") -> None:
    # Deferred imports: this package is loaded before the MWDB app finishes booting.
    from . import attributes, model

    try:
        model.ensure_schema()
        attributes.ensure_attribute_definition()
    except Exception as e:  # schema may not exist yet during `mwdb-core configure`
        logger.warning("threatlib: deferred setup until first request: %s", e)
        try:
            from mwdb.model import db

            db.session.rollback()
        except Exception:
            pass

    _register_resources(app_context)


def _register_resources(app_context: "PluginAppContext") -> None:
    from .resource import (
        ThreatListResource,
        ThreatResource,
        ThreatSampleListResource,
        ThreatSampleResource,
        ThreatUploadResource,
    )

    app_context.register_resource(ThreatListResource, "/threatlib/threat")
    app_context.register_resource(ThreatResource, "/threatlib/threat/<name>")
    app_context.register_resource(
        ThreatSampleListResource, "/threatlib/threat/<name>/sample"
    )
    app_context.register_resource(
        ThreatSampleResource, "/threatlib/threat/<name>/sample/<sha256>"
    )
    app_context.register_resource(ThreatUploadResource, "/threatlib/upload")
    logger.info("threatlib: registered /api/threatlib/* resources")


__plugin_entrypoint__ = entrypoint
