"""The phpdeobf_sink attribute definition the resource writes sink categories to."""

from __future__ import annotations

import logging

ATTRIBUTE_KEY = "phpdeobf_sink"
READER_GROUP = "registered"
logger = logging.getLogger("mwdb.plugin.phpdeobf")


def ensure_attribute_definition() -> None:
    """Create the phpdeobf_sink definition and grant `registered` read access.

    Idempotent: safe to call on every startup. Without the permission row
    non-admin users get "No such attribute" when reading or searching it.
    """
    from mwdb.model import AttributeDefinition, AttributePermission, Group, db

    existing = (
        db.session.query(AttributeDefinition)
        .filter(AttributeDefinition.key == ATTRIBUTE_KEY)
        .first()
    )
    if not existing:
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

    group = Group.get_by_name(READER_GROUP)
    if group is None:
        logger.warning(
            "phpdeobf: group %r not found; %s read permission not granted",
            READER_GROUP,
            ATTRIBUTE_KEY,
        )
        return
    permission = (
        db.session.query(AttributePermission)
        .filter(AttributePermission.key == ATTRIBUTE_KEY)
        .filter(AttributePermission.group_id == group.id)
        .first()
    )
    if permission:
        return
    db.session.add(
        AttributePermission(
            key=ATTRIBUTE_KEY, group_id=group.id, can_read=True, can_set=False
        )
    )
    db.session.commit()
    logger.info(
        "phpdeobf: granted %s read permission on %s", READER_GROUP, ATTRIBUTE_KEY
    )
