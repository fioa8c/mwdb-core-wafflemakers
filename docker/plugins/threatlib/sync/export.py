"""Export: regenerate the owned parts of the repo tree from the tables."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from mwdb.model import db

from ..model import CATEGORIES, Threat
from .manifest import Manifest, save_manifest, sha256_bytes
from .store import ObjectStore

logger = logging.getLogger("mwdb.plugin.threatlib.sync")


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
            if entry.is_dir():
                shutil.rmtree(entry)
            elif category == "webshells" and entry.is_file():
                entry.unlink()


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def export(repo_path: Path, store: ObjectStore) -> tuple[Manifest, ExportStats]:
    repo_path = Path(repo_path)
    clear_owned(repo_path)
    manifest = Manifest()
    stats = ExportStats()
    threats = db.session.query(Threat).order_by(Threat.name).all()
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
    save_manifest(repo_path, manifest)
    logger.info(
        "threatlib export: %d threats, %d files, %d READMEs",
        stats.threats,
        stats.files_written,
        stats.readmes_written,
    )
    return manifest, stats
