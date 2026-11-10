"""Attribute definitions written by Malleus, and who may read/set them."""

from typing import Dict, List

from . import logger

# keep in sync with malleus/service/payloads.py
ATTRIBUTE_KEYS = (
    "malleus_verdict",
    "malleus_confidence",
    "malleus_reasons",
    "malleus_stages",
    "malleus_depth",
    "malleus_gate",
    "malleus_gate_disagreement",
    "malleus_rules_commit",
    "malleus_unlocked",
    "malleus_model",
    "malleus_prompt_sha256",
    "malleus_llm",
    "context",
    "layer_variant",
    "layer_unlocked",
    "malleus_analysis",
    "sandbox_level",
    "sandbox_coverage",
    "sandbox_network",
    "sandbox_files_written",
    "sandbox_dangerous_calls",
)

ANALYSIS_KEY = "malleus_analysis"

# Keys the reporter writes per analysis run; a new run replaces all of them.
# `context` is scanner-supplied, the layer_* keys belong to child layer objects.
PIPELINE_KEYS = tuple(
    key
    for key in ATTRIBUTE_KEYS
    if key not in ("context", "layer_variant", "layer_unlocked")
)

DESCRIPTIONS: Dict[str, str] = {
    "malleus_verdict": "Malleus verdict (known-bad, malicious, likely-*, undetermined, no-hit, not-applicable)",
    "malleus_confidence": "Malleus confidence in its verdict (0.0 to 1.0)",
    "malleus_reasons": "Short reasons behind the Malleus verdict",
    "malleus_stages": "Per-stage Malleus results (JSON object)",
    "malleus_depth": "Analysis depth: none, auto or full",
    "malleus_gate": "Malleus static gate decision",
    "malleus_gate_disagreement": "Hub and caller gate hits when they differ (JSON object)",
    "malleus_rules_commit": "Commit of the Malleus rules used for the analysis",
    "malleus_unlocked": "Weak rules a deobfuscated layer matched that the sample did not",
    "malleus_model": "LLM model that produced the Malleus second opinion",
    "malleus_prompt_sha256": "SHA256 of the prompt used for the Malleus LLM call",
    "malleus_llm": "Malleus LLM call details (JSON object)",
    "context": "Scanner-supplied context; only allowed keys are kept (JSON object)",
    "layer_variant": "Variant name of this deobfuscated layer",
    "layer_unlocked": "Rules this layer matched that the sample did not",
    "malleus_analysis": "Karton analysis id of the Malleus run that wrote these attributes",
    "sandbox_level": "Kadath sandbox evidence level (red, amber, green)",
    "sandbox_coverage": "Kadath sandbox coverage (full, stubbed, errored)",
    "sandbox_network": "Outbound hosts and blocked destinations seen in the Kadath sandbox",
    "sandbox_files_written": "Number of files the sample wrote in the Kadath sandbox",
    "sandbox_dangerous_calls": "Number of dangerous PHP calls seen in the Kadath sandbox",
}


def _session():
    from mwdb.model import db

    return db.session


def _models():
    from mwdb.model.attribute import AttributeDefinition, AttributePermission
    from mwdb.model.group import Group

    return AttributeDefinition, AttributePermission, Group


def ensure_attribute_definitions() -> None:
    session = _session()
    AttributeDefinition, _, _ = _models()
    changed = False
    for key in ATTRIBUTE_KEYS:
        exists = (
            session.query(AttributeDefinition)
            .filter(AttributeDefinition.key == key)
            .first()
        )
        if exists is not None:
            if exists.description != DESCRIPTIONS[key]:
                exists.description = DESCRIPTIONS[key]
                changed = True
                logger.info("malleus: updated attribute description %s", key)
            continue
        session.add(
            AttributeDefinition(
                key=key,
                label=key,
                description=DESCRIPTIONS[key],
                url_template="",
                rich_template="",
                example_value="",
                hidden=False,
            )
        )
        changed = True
        logger.info("malleus: created attribute definition %s", key)
    if changed:
        session.commit()


def _grant(session, AttributePermission, key, group, can_read, can_set) -> None:
    row = (
        session.query(AttributePermission)
        .filter(
            AttributePermission.key == key,
            AttributePermission.group_id == group.id,
        )
        .first()
    )
    if row is None:
        session.add(
            AttributePermission(
                key=key, group_id=group.id, can_read=can_read, can_set=can_set
            )
        )
    else:
        row.can_read = can_read
        row.can_set = can_set


def ensure_group_permissions(set_groups: List[str], read_groups: List[str]) -> None:
    """Reporter groups read+set every key; scanner groups read every key and
    may set only `context`. Missing groups are logged and skipped."""
    session = _session()
    _, AttributePermission, Group = _models()

    for names, is_reporter in ((set_groups, True), (read_groups, False)):
        for name in names:
            group = Group.get_by_name(name)
            if group is None:
                logger.warning("malleus: group %r does not exist, skipping", name)
                continue
            for key in ATTRIBUTE_KEYS:
                can_set = is_reporter or key == "context"
                _grant(session, AttributePermission, key, group, True, can_set)
    session.commit()
