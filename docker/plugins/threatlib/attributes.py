"""The jpop_threat_name attribute definition the mirror relies on."""

from __future__ import annotations

import logging

ATTRIBUTE_KEY = "jpop_threat_name"

logger = logging.getLogger("mwdb.plugin.threatlib")


def ensure_attribute_definition() -> None:
    from mwdb.model import db
    from mwdb.model.attribute import AttributeDefinition

    existing = (
        db.session.query(AttributeDefinition)
        .filter(AttributeDefinition.key == ATTRIBUTE_KEY)
        .first()
    )
    if existing:
        return
    db.session.add(
        AttributeDefinition(
            key=ATTRIBUTE_KEY,
            label=ATTRIBUTE_KEY,
            description="Threat name in the jetpack threat library (managed by threatlib)",
            url_template="",
            rich_template="",
            example_value="",
        )
    )
    db.session.commit()
    logger.info("threatlib: created attribute definition %s", ATTRIBUTE_KEY)
