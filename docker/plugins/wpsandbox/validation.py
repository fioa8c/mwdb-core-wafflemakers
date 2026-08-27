"""Request validation for run creation. Pure functions, no Flask."""

import posixpath
import re

MODES = ("webroot", "plugin")
METHODS = ("GET", "POST")
DEFAULT_TIMEOUT = 120
MAX_TEXT_BYTES = 65536
_PATH_RE = re.compile(r"[A-Za-z0-9._/-]{1,255}")


class ValidationError(ValueError):
    pass


def _timeout(value, max_timeout: int) -> int:
    if value is None:
        return DEFAULT_TIMEOUT
    try:
        t = int(value)
    except (TypeError, ValueError):
        raise ValidationError("timeout must be an integer")
    if t < 1 or t > max_timeout:
        raise ValidationError(f"timeout must be between 1 and {max_timeout}")
    return t


def _path(value, sha256: str) -> str:
    if not value:
        return f"wp-content/uploads/{sha256[:8]}.php"
    p = str(value).strip().lstrip("/")
    p = posixpath.normpath(p)
    # Ensure path doesn't escape the root
    if (
        p == ".."
        or p.startswith("../")
        or "/../" in p
        or posixpath.isabs(p)
        or ".." in p
    ):
        raise ValidationError("path must stay inside the WordPress root")
    if not _PATH_RE.fullmatch(p):
        raise ValidationError(
            "path may only contain letters, digits, '.', '_', '-' and '/'"
        )
    if not p.lower().endswith((".php", ".phtml", ".php5", ".php7", ".inc")):
        raise ValidationError("path must end with a PHP extension")
    return p


def _text_field(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError("query/body must be a string")
    if len(value.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValidationError(f"query/body must be at most {MAX_TEXT_BYTES} bytes")
    return value


def normalize_params(
    body: dict, *, max_timeout: int, sample_sha256: str, sample_name: str | None
) -> tuple[str, dict]:
    body = body or {}
    mode = body.get("mode")
    if mode not in MODES:
        raise ValidationError(f"mode must be one of {', '.join(MODES)}")
    timeout = _timeout(body.get("timeout"), max_timeout)
    if mode == "plugin":
        if not (sample_name or "").lower().endswith(".zip"):
            raise ValidationError("plugin mode requires a .zip sample")
        return mode, {"timeout": timeout}
    method = str(body.get("method") or "GET").upper()
    if method not in METHODS:
        raise ValidationError("method must be GET or POST")
    return mode, {
        "path": _path(body.get("path"), sample_sha256),
        "method": method,
        "query": _text_field(body.get("query")),
        "body": _text_field(body.get("body")),
        "timeout": timeout,
    }
