"""Input validation for names and paths that become filesystem paths in the export."""

from __future__ import annotations

import posixpath
import re

from .model import CATEGORIES

NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
MAX_NAME_LEN = 255
MAX_REL_PATH_LEN = 1024


class ValidationError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def validate_name(name) -> str:
    if not isinstance(name, str) or not name:
        raise ValidationError("Threat name is required")
    if len(name) > MAX_NAME_LEN:
        raise ValidationError(f"Threat name longer than {MAX_NAME_LEN} characters")
    if name in (".", ".."):
        raise ValidationError("Threat name cannot be '.' or '..'")
    if name.lower() == ".git":
        raise ValidationError("Threat name cannot be '.git'")
    if not NAME_RE.fullmatch(name):
        raise ValidationError("Threat name may contain only A-Z a-z 0-9 . _ -")
    return name


def validate_rel_path(rel_path) -> str:
    if not isinstance(rel_path, str) or not rel_path:
        raise ValidationError("rel_path is required")
    if len(rel_path) > MAX_REL_PATH_LEN:
        raise ValidationError(f"rel_path longer than {MAX_REL_PATH_LEN} characters")
    if "\\" in rel_path or "\x00" in rel_path:
        raise ValidationError("rel_path must use '/' separators")
    if rel_path.startswith("/"):
        raise ValidationError("rel_path must be relative")
    if rel_path.endswith("/"):
        raise ValidationError("rel_path must name a file")
    if "//" in rel_path:
        raise ValidationError("rel_path contains an empty segment")
    segments = rel_path.split("/")
    if any(seg == ".." for seg in segments):
        raise ValidationError("rel_path cannot contain '..'")
    if any(seg.lower() == ".git" for seg in segments):
        raise ValidationError("rel_path cannot contain a '.git' segment")
    normalised = posixpath.normpath(rel_path)
    if normalised in (".", "") or normalised.startswith("../"):
        raise ValidationError("rel_path is invalid")
    return normalised


def validate_category(category) -> str:
    if category not in CATEGORIES:
        raise ValidationError(f"category must be one of {', '.join(CATEGORIES)}")
    return category
