"""Reject uploads whose `context` attribute carries disallowed keys.

MWDB spawns the Karton task inside the upload request, before the post-commit
hooks run, and the task payload carries the attributes given at upload. The
strip hook in `hooks.py` therefore comes too late for upload-time attributes:
the disallowed keys would already have left MWDB. This guard runs as an API
`before_request` and answers 400 before anything is created or spawned.
Attributes added after upload spawn nothing and are still handled by the hook.
"""

import json
from typing import Any, Iterable, List, Optional

from flask import g, request
from werkzeug.exceptions import BadRequest

from .context_policy import ALLOWED_CONTEXT_KEYS

# Upload endpoints (blueprint "api", endpoint = resource class name lowercased).
# FileResource reads the JSON `options` form field; ConfigResource and
# TextBlobResource read a JSON body; the deprecated *ItemResource uploads read a
# JSON body, or the `json` and `metakeys` form fields.
UPLOAD_ENDPOINTS = frozenset(
    {
        "api.fileresource",
        "api.configresource",
        "api.textblobresource",
        "api.fileitemresource",
        "api.configitemresource",
        "api.textblobitemresource",
    }
)
FORM_FIELDS = ("options", "json", "metakeys")


def _attribute_entries(options: Any) -> Iterable[Any]:
    if not isinstance(options, dict):
        return []
    entries: List[Any] = []
    for field in ("attributes", "metakeys"):
        value = options.get(field)
        if isinstance(value, list):
            entries.extend(value)
    return entries


def disallowed_context_keys(options_json: Optional[str]) -> List[str]:
    """Sorted keys outside ALLOWED_CONTEXT_KEYS in any `context` attribute.

    `options_json` is an upload's options as JSON text: an object with an
    `attributes` (or legacy `metakeys`) list of `{"key", "value"}` entries.
    Missing or malformed input, and a `context` value that is not a JSON
    object, yield no keys: the resource's own schema and the post-commit hook
    deal with those.
    """
    if not options_json:
        return []
    try:
        options = json.loads(options_json)
    except (TypeError, ValueError):
        return []
    found = set()
    for entry in _attribute_entries(options):
        if not isinstance(entry, dict) or entry.get("key") != "context":
            continue
        value = entry.get("value")
        if isinstance(value, dict):
            found.update(str(k) for k in value if k not in ALLOWED_CONTEXT_KEYS)
    return sorted(found)


def _request_payloads() -> List[str]:
    if request.is_json:
        return [request.get_data(as_text=True)]
    return [request.form[name] for name in FORM_FIELDS if request.form.get(name)]


def guard_upload() -> None:
    """Flask before_request for the API blueprint."""
    if request.method != "POST" or request.endpoint not in UPLOAD_ENDPOINTS:
        return
    # Unauthenticated requests get the resource's own 401.
    if getattr(g, "auth_user", None) is None:
        return
    keys = set()
    for payload in _request_payloads():
        keys.update(disallowed_context_keys(payload))
    if keys:
        raise BadRequest(
            "context attribute has disallowed keys: " + ", ".join(sorted(keys))
        )


def register() -> None:
    """Attach the guard to the API blueprint (runs after auth and rate limit).

    Must be called from the plugin entrypoint: plugins load before the
    blueprint is registered on the app, after which it can no longer change.
    """
    from mwdb.core.app import api

    api.blueprint.before_request(guard_upload)
