"""Map the tab's preset / options body onto the sidecar's option set."""

OPTION_KEYS = (
    "strip_comments",
    "execute_pure",
    "eval_peel",
    "remove_dead_code",
    "rename_vars",
    "analyze",
)
PRESETS = ("full", "static", "custom")


def resolve(body, sample: bytes) -> dict:
    body = body or {}
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    preset = body.get("preset", "full")
    if preset not in PRESETS:
        raise ValueError(f"preset must be one of {', '.join(PRESETS)}")
    if preset == "full":
        opts = {k: True for k in OPTION_KEYS}
        opts["eval_peel"] = b"eval" in sample.lower()
        return opts
    if preset == "static":
        return {k: False for k in OPTION_KEYS}
    given = body.get("options", {})
    if not isinstance(given, dict):
        raise ValueError("options must be an object")
    opts = {k: False for k in OPTION_KEYS}
    for key, value in given.items():
        if key not in OPTION_KEYS:
            raise ValueError(f"unknown option {key!r}")
        if not isinstance(value, bool):
            raise ValueError(f"option {key!r} must be a boolean")
        opts[key] = value
    return opts
