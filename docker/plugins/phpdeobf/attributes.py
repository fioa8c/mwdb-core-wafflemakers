"""The phpdeobf_sink attribute definition the resource writes sink categories to."""

from __future__ import annotations

import logging

ATTRIBUTE_KEY = "phpdeobf_sink"
logger = logging.getLogger("mwdb.plugin.phpdeobf")


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
            label="PHP sink category",
            description="Dangerous-sink categories found by the PHP deobfuscator's analysis (managed by phpdeobf)",
            url_template="",
            rich_template="",
            example_value="os_exec",
        )
    )
    db.session.commit()
    logger.info("phpdeobf: created attribute definition %s", ATTRIBUTE_KEY)
