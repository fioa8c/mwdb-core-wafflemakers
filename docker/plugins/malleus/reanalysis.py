"""Reanalysis cleanup: a new Malleus analysis replaces the previous one.

karton-mwdb-reporter only adds: it posts tags, then each attribute value, and
skips values the object already has. Without this, a reanalysis leaves two
`verdict:*` tags and two values of every pipeline attribute.
"""

from typing import List

from . import logger
from .attributes import PIPELINE_KEYS

REVIEW_TAG = "wm:needs-review"
VERDICT_PREFIX = "verdict:"


def _attribute_model():
    # Deferred, like attributes._models(): the unit tests have no MWDB stack.
    from mwdb.model.attribute import Attribute

    return Attribute


def _session():
    from mwdb.model import db

    return db.session


def clear_previous_verdict(object, new_tag: str) -> List[str]:
    """Remove every `verdict:*` tag except `new_tag`, and `wm:needs-review`.

    The caller commits. The reporter re-adds `wm:needs-review` right after
    when the new verdict needs review. `yara:*` tags are left alone.
    """
    stale = sorted(
        {
            tag.tag
            for tag in object.tags
            if tag.tag != new_tag
            and (tag.tag.startswith(VERDICT_PREFIX) or tag.tag == REVIEW_TAG)
        }
    )
    for name in stale:
        object.remove_tag(name, commit=False)
    return stale


def _stale_attributes(object, anchor):
    Attribute = _attribute_model()
    return (
        _session()
        .query(Attribute)
        .filter(
            Attribute.object_id == object.id,
            Attribute.key.in_(PIPELINE_KEYS),
            Attribute.id != anchor.id,
        )
        .all()
    )


def clear_previous_analysis(object, anchor) -> int:
    """Remove every pipeline-owned attribute value of `object` except `anchor`.

    `anchor` is the new `malleus_analysis` value, which the reporter posts
    before the rest of the run's attributes. Each removal commits on its own.
    """
    stale = _stale_attributes(object, anchor)
    # Snapshot ids first: removing commits, which expires the loaded rows.
    ids = [attribute.id for attribute in stale]
    for attribute_id in ids:
        object.remove_attribute_by_id(attribute_id, check_permissions=False)
    if ids:
        logger.debug("malleus: removed %d stale attribute values", len(ids))
    return len(ids)
