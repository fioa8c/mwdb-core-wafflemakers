"""The committed manifest: what the previous export wrote to the repo.

'New in repo' means 'path not in the last manifest', which is what lets a
sample deleted in MWDB stay deleted instead of being re-ingested.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_NAME = ".mwdb-threatlib.json"
MANIFEST_VERSION = 1


@dataclass
class Manifest:
    files: dict[str, str] = field(default_factory=dict)
    readmes: dict[str, str] = field(default_factory=dict)
    generated_at: str | None = None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_manifest(repo_path: Path) -> Manifest | None:
    path = Path(repo_path) / MANIFEST_NAME
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("version") != MANIFEST_VERSION:
        raise ValueError(f"Unsupported manifest version: {raw.get('version')!r}")
    return Manifest(
        files=dict(raw.get("files", {})),
        readmes=dict(raw.get("readmes", {})),
        generated_at=raw.get("generated_at"),
    )


def save_manifest(repo_path: Path, manifest: Manifest) -> None:
    generated_at = manifest.generated_at or datetime.now(timezone.utc).isoformat()
    payload = {
        "version": MANIFEST_VERSION,
        "generated_at": generated_at,
        "files": dict(sorted(manifest.files.items())),
        "readmes": dict(sorted(manifest.readmes.items())),
    }
    text = json.dumps(payload, indent=1, sort_keys=False) + "\n"
    (Path(repo_path) / MANIFEST_NAME).write_text(text, encoding="utf-8")
