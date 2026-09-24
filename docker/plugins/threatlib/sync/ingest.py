"""Ingest: absorb hand-made repo changes into MWDB before exporting.

Rules (spec §4 step 3, plus round-trip fidelity rules from repo inspection):
- The sync owns every subdirectory of each category dir, plus the regular
  files directly under webshells/ (flat, single-file threats). Regular files
  directly under the other category dirs are neither ingested nor deleted.
- A file is "new" when its repo-relative path is absent from the manifest.
- <category>/<name>/README.md is the threat README; deeper README.md files
  are ordinary samples.
- A path present in the manifest but missing on disk was deleted in the
  repo: unlink it.
- Empty files are linked with object_id NULL.
"""

from __future__ import annotations

import io
import logging
import posixpath
from dataclasses import dataclass
from pathlib import Path

from mwdb.model import db

from .. import service
from ..model import CATEGORIES
from ..validation import ValidationError
from .manifest import MANIFEST_NAME, Manifest, sha256_bytes
from .store import ObjectStore

logger = logging.getLogger("mwdb.plugin.threatlib.sync")


@dataclass
class IngestStats:
    new_files: int = 0
    new_threats: int = 0
    readmes_updated: int = 0
    unlinked: int = 0
    skipped: int = 0
    errors: int = 0


def classify(repo_path: Path, path: Path):
    """Map a repo file to (category, name, rel_path, flat, is_readme), or None
    when the sync does not own that path."""
    rel = path.relative_to(repo_path).as_posix()
    parts = rel.split("/")
    if parts[0] == MANIFEST_NAME or parts[0] not in CATEGORIES:
        return None
    category = parts[0]
    if len(parts) == 2:
        if category != "webshells":
            return None
        return category, posixpath.splitext(parts[1])[0], parts[1], True, False
    name = parts[1]
    rel_path = "/".join(parts[2:])
    is_readme = rel_path == "README.md"
    return category, name, rel_path, False, is_readme


def _iter_files(repo_path: Path):
    for category in CATEGORIES:
        cat_dir = repo_path / category
        if not cat_dir.is_dir():
            continue
        for path in sorted(p for p in cat_dir.rglob("*") if p.is_file()):
            yield path


def _get_or_create_threat(category, name, flat):
    """Returns (threat, created). Does not commit or touch stats: the caller
    only counts it once the whole per-file transaction has committed."""
    threat = service.get_threat(name)
    if threat is None:
        threat = service.create_threat(name, category, flat=flat, commit=False)
        return threat, True
    return threat, False


def _ingest_sample(path, category, name, rel_path, flat, store):
    """Returns (new_threat, new_file). Stores the file BEFORE creating the
    threat: both `MwdbStore.get_or_create` and `FakeStore.get_or_create`
    commit the current session internally (they only durably persist the
    File row, but a commit flushes everything pending on the session). If
    the threat were created first and `link_sample` then failed, the
    caller's rollback would have nothing left to undo and an orphan,
    zero-sample threat would survive. Creating the threat only once the
    file is safely stored keeps the threat-creation + link in one
    transaction that a later failure can still roll back in full."""
    data = path.read_bytes()
    if len(data) == 0:
        file_obj = None
    else:
        file_obj, _ = store.get_or_create(
            posixpath.basename(rel_path), io.BytesIO(data)
        )
    threat, new_threat = _get_or_create_threat(category, name, flat)
    _, new_file = service.link_sample(threat, file_obj, rel_path, commit=False)
    return new_threat, new_file


def _ingest_readme(path, category, name, threat_dir, manifest):
    """Returns (new_threat, updated)."""
    data = path.read_bytes()
    new_hash = sha256_bytes(data)
    old_hash = manifest.readmes.get(threat_dir) if manifest else None
    if new_hash == old_hash:
        return False, False
    threat, new_threat = _get_or_create_threat(category, name, False)
    current_hash = (
        None if threat.readme is None else sha256_bytes(threat.readme.encode("utf-8"))
    )
    if threat.readme is None or current_hash == old_hash:
        service.set_readme(threat, data.decode("utf-8", errors="replace"), commit=False)
        return new_threat, True
    return new_threat, False


def _clear_deleted_readme(threat_dir, old_hash, stats):
    name = threat_dir.split("/")[1]
    threat = service.get_threat(name)
    if threat is None or threat.readme is None:
        return
    if sha256_bytes(threat.readme.encode("utf-8")) == old_hash:
        service.set_readme(threat, None, commit=False)
        stats.readmes_updated += 1


def _unlink_deleted(manifest_key, stats):
    parts = manifest_key.split("/")
    if len(parts) < 2 or parts[0] not in CATEGORIES:
        return
    if len(parts) == 2:  # flat webshell
        name, rel_path = posixpath.splitext(parts[1])[0], parts[1]
    else:
        name, rel_path = parts[1], "/".join(parts[2:])
    threat = service.get_threat(name)
    if threat is None:
        return
    if service.unlink_sample(threat, rel_path, commit=False):
        stats.unlinked += 1


def ingest(
    repo_path: Path, manifest: Manifest | None, store: ObjectStore
) -> IngestStats:
    repo_path = Path(repo_path)
    stats = IngestStats()
    previous_load = service._load_file
    service._load_file = store.load
    try:
        seen_files: set[str] = set()
        seen_readmes: set[str] = set()
        for path in _iter_files(repo_path):
            info = classify(repo_path, path)
            if info is None:
                continue
            category, name, rel_path, flat, is_readme = info
            key = path.relative_to(repo_path).as_posix()
            try:
                if is_readme:
                    threat_dir = f"{category}/{name}"
                    seen_readmes.add(threat_dir)
                    new_threat, readme_updated = _ingest_readme(
                        path, category, name, threat_dir, manifest
                    )
                    new_file = False
                else:
                    seen_files.add(key)
                    if manifest is not None and key in manifest.files:
                        continue
                    new_threat, new_file = _ingest_sample(
                        path, category, name, rel_path, flat, store
                    )
                    readme_updated = False
                db.session.commit()
                # Only count effects of a file whose transaction actually
                # committed, so a rolled-back threat/link/readme is never
                # counted (see _ingest_sample's docstring for why the order
                # of operations matters here).
                if new_threat:
                    stats.new_threats += 1
                if new_file:
                    stats.new_files += 1
                if readme_updated:
                    stats.readmes_updated += 1
            except ValidationError as e:
                db.session.rollback()
                logger.warning("threatlib ingest: skipping %s: %s", key, e.message)
                stats.skipped += 1
            except Exception as e:
                db.session.rollback()
                logger.error("threatlib ingest: error on %s: %s", key, e)
                stats.errors += 1

        if manifest is not None:
            for key in sorted(set(manifest.files) - seen_files):
                try:
                    _unlink_deleted(key, stats)
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    logger.error("threatlib ingest: error unlinking %s: %s", key, e)
                    stats.errors += 1
            for threat_dir, old_hash in sorted(manifest.readmes.items()):
                if threat_dir in seen_readmes:
                    continue
                try:
                    _clear_deleted_readme(threat_dir, old_hash, stats)
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    logger.error(
                        "threatlib ingest: error clearing README %s: %s", threat_dir, e
                    )
                    stats.errors += 1
    finally:
        service._load_file = previous_load
    return stats
