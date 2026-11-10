"""Export: regenerate the owned parts of the repo tree from the tables."""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from mwdb.model import db

from ..model import CATEGORIES, Threat
from .manifest import Manifest, save_manifest, sha256_bytes
from .store import ObjectStore

logger = logging.getLogger("mwdb.plugin.threatlib.sync")


class ExportError(Exception):
    pass


@dataclass
class ExportStats:
    files_written: int = 0
    readmes_written: int = 0
    threats: int = 0


def clear_owned(repo_path: Path) -> None:
    repo_path = Path(repo_path)
    for category in CATEGORIES:
        cat_dir = repo_path / category
        cat_dir.mkdir(exist_ok=True)
        for entry in cat_dir.iterdir():
            # Symlinks first: is_dir() follows them, and rmtree must never
            # walk into a symlinked directory's target.
            if entry.is_symlink():
                entry.unlink()
            elif entry.is_dir():
                shutil.rmtree(entry)
            elif category == "webshells" and entry.is_file():
                entry.unlink()


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@dataclass
class _Snapshot:
    data: bytes | None = None
    mode: int = 0o644
    link_target: str | None = None


def _snapshot(repo_path: Path, preserve: Iterable[str]) -> dict[str, _Snapshot]:
    """Read every preserved path as it is on disk, without following
    symlinks. Paths that no longer exist are dropped."""
    snapshots: dict[str, _Snapshot] = {}
    for rel in sorted(set(preserve)):
        path = repo_path / rel
        if path.is_symlink():
            snapshots[rel] = _Snapshot(link_target=os.readlink(path))
        elif path.is_file():
            mode = path.stat().st_mode & 0o7777
            snapshots[rel] = _Snapshot(data=path.read_bytes(), mode=mode)
    return snapshots


def _restore(repo_path: Path, snapshots: dict[str, _Snapshot]) -> int:
    """Write the snapshots back after the table-driven rewrite. A path the
    tables already wrote wins; the collision is logged."""
    restored = 0
    root = repo_path.resolve()
    for rel, snap in snapshots.items():
        path = repo_path / rel
        if os.path.lexists(path):
            logger.warning(
                "threatlib export: preserved path %s collides with a "
                "table-driven path; keeping the table version",
                rel,
            )
            continue
        try:
            if not path.parent.resolve().is_relative_to(root):
                raise OSError("parent directory resolves outside the repo")
            path.parent.mkdir(parents=True, exist_ok=True)
            if snap.link_target is not None:
                os.symlink(snap.link_target, path)
            else:
                path.write_bytes(snap.data)
                path.chmod(snap.mode)
        except OSError as e:
            logger.warning(
                "threatlib export: could not write back preserved path %s: %s",
                rel,
                e,
            )
            continue
        restored += 1
    return restored


def _check_for_collisions(repo_path: Path, threats: list[Threat]) -> None:
    """Refuse to touch the tree when a nested threat's directory name would
    collide with a preserved category-root regular file (e.g. a threat named
    'README.md'): clear_owned only removes subdirectories of category dirs
    (plus webshells root files), so such a threat's `mkdir` would raise
    FileExistsError mid-export, leaving a half-cleared tree with no manifest.
    """
    collisions = [
        f"{threat.category}/{threat.name}"
        for threat in threats
        if not threat.flat and (repo_path / threat.category / threat.name).is_file()
    ]
    if collisions:
        raise ExportError(
            "threat name(s) collide with existing category-root file(s): "
            + ", ".join(repr(c) for c in collisions)
        )


def export(
    repo_path: Path, store: ObjectStore, preserve: Iterable[str] = ()
) -> tuple[Manifest, ExportStats]:
    """Regenerate the owned dirs from the tables. `preserve` lists
    repo-relative paths (ingest errors/skips, symlinks) written back exactly
    as they were and left out of the manifest, so the next run retries them."""
    repo_path = Path(repo_path)
    threats = db.session.query(Threat).order_by(Threat.name).all()
    _check_for_collisions(repo_path, threats)
    for category in CATEGORIES:
        if (repo_path / category).is_symlink():
            raise ExportError(f"category dir {category!r} is a symlink")
    snapshots = _snapshot(repo_path, preserve)
    clear_owned(repo_path)
    manifest = Manifest()
    stats = ExportStats()
    for threat in threats:
        stats.threats += 1
        base = repo_path / threat.category
        if not threat.flat:
            base = base / threat.name
        for link in sorted(threat.samples, key=lambda link: link.rel_path):
            data = b"" if link.object_id is None else store.read(link.object_id)
            _write(base / link.rel_path, data)
            key = (base / link.rel_path).relative_to(repo_path).as_posix()
            manifest.files[key] = sha256_bytes(data)
            stats.files_written += 1
        if not threat.flat and threat.readme is not None:
            data = threat.readme.encode("utf-8")
            _write(base / "README.md", data)
            manifest.readmes[f"{threat.category}/{threat.name}"] = sha256_bytes(data)
            stats.readmes_written += 1
    _restore(repo_path, snapshots)
    save_manifest(repo_path, manifest)
    logger.info(
        "threatlib export: %d threats, %d files, %d READMEs",
        stats.threats,
        stats.files_written,
        stats.readmes_written,
    )
    return manifest, stats
