"""Pure rules for the `context` attribute and scanner `source:` tags."""

from typing import Any, Iterable, List, Tuple

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


def source_tag_allowed(
    tag: str, login: str, reporter_logins: Iterable[str] = ()
) -> bool:
    """A scanner account may only apply `source:<its own login>`; a reporter
    account may never create a `source:` tag.

    The reporter replays the tags of the Karton payload, which was built at
    upload before the scanner-side strip ran, so a `source:` tag it creates is
    always a forged one coming back. Replaying a tag that is still present
    creates nothing, so legitimate tags are unaffected.
    """
    if not tag.startswith("source:"):
        return True
    if login in reporter_logins:
        return False
    if login not in SCANNER_SOURCES:
        return True
    return tag == f"source:{login}"
