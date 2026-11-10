"""Pure rules for the `context` attribute and scanner `source:` tags."""

from typing import Any, List, Tuple

ALLOWED_CONTEXT_KEYS = ("scanner", "scanner_version", "rules", "path", "engine")
SCANNER_SOURCES = ("jetpack-scan", "wpcloud-scan", "elderward")


def filter_context(value: Any) -> Tuple[Any, List[str]]:
    """Keep only allowed keys of a JSON-object context.

    Anything that is not a JSON object is returned untouched.
    Returns (kept value, sorted stripped key names).
    """
    if not isinstance(value, dict):
        return value, []
    kept = {k: v for k, v in value.items() if k in ALLOWED_CONTEXT_KEYS}
    stripped = sorted(str(k) for k in value if k not in ALLOWED_CONTEXT_KEYS)
    return kept, stripped


def source_tag_allowed(tag: str, login: str) -> bool:
    """A scanner account may only apply `source:<its own login>`."""
    if not tag.startswith("source:"):
        return True
    if login not in SCANNER_SOURCES:
        return True
    return tag == f"source:{login}"
