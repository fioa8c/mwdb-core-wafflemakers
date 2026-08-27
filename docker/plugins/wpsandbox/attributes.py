"""Attribute definitions the worker populates from reports."""

from . import logger

ATTRIBUTE_KEYS = ("c2_host", "dropped_file", "wp_user_added")
DESCRIPTIONS = {
    "c2_host": "Host contacted by the sample during WP sandbox execution",
    "dropped_file": "SHA256 of a file created by the sample during WP sandbox execution",
    "wp_user_added": "WordPress user login created by the sample during WP sandbox execution",
}


def _session():
    from mwdb.model import db

    return db.session


def _definition_cls():
    from mwdb.model.attribute import AttributeDefinition

    return AttributeDefinition


def _definition_exists(session, AttributeDefinition, key: str) -> bool:
    return (
        session.query(AttributeDefinition)
        .filter(AttributeDefinition.key == key)
        .first()
        is not None
    )


def ensure_attribute_definitions() -> None:
    session = _session()
    AttributeDefinition = _definition_cls()
    created = False
    for key in ATTRIBUTE_KEYS:
        if _definition_exists(session, AttributeDefinition, key):
            continue
        session.add(
            AttributeDefinition(
                key=key,
                label=key,
                description=DESCRIPTIONS[key],
                url_template="",
                rich_template="",
                example_value="",
            )
        )
        created = True
        logger.info("wpsandbox: created attribute definition %s", key)
    if created:
        session.commit()
