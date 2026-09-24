"""One sync run and the `threatlib-sync` console script."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .. import service
from ..attributes import ATTRIBUTE_KEY
from ..model import ThreatSample
from .export import ExportStats, export
from .ingest import IngestStats, ingest
from .manifest import load_manifest, save_manifest
from .repo import GitError, GitRepo
from .store import MwdbStore, ObjectStore

logger = logging.getLogger("mwdb.plugin.threatlib.sync")


@dataclass
class SyncConfig:
    repo_url: str
    clone_dir: Path
    deploy_key: str | None
    interval: int
    push: bool
    author: str
    branch: str = "trunk"
    share_with: str | None = "public"

    @classmethod
    def from_env(cls) -> "SyncConfig":
        url = os.environ.get("MWDB_THREATLIB_REPO_URL")
        if not url:
            print("MWDB_THREATLIB_REPO_URL is required", file=sys.stderr)
            raise SystemExit(2)
        return cls(
            repo_url=url,
            clone_dir=Path(os.environ.get("MWDB_THREATLIB_CLONE_DIR", "/data/repo")),
            deploy_key=os.environ.get("MWDB_THREATLIB_DEPLOY_KEY") or None,
            interval=int(os.environ.get("MWDB_THREATLIB_SYNC_INTERVAL", "900")),
            push=os.environ.get("MWDB_THREATLIB_PUSH", "1") not in ("0", "false", "no"),
            author=os.environ.get(
                "MWDB_THREATLIB_GIT_AUTHOR",
                "mwdb-threatlib-bot <noreply@wafflemakers.xyz>",
            ),
            branch=os.environ.get("MWDB_THREATLIB_BRANCH", "trunk"),
            share_with=os.environ.get("MWDB_THREATLIB_SHARE_WITH", "public") or None,
        )


@dataclass
class RunResult:
    head_before: str
    ingest: IngestStats
    export: ExportStats
    commit: str | None
    pushed: bool


def repair_mirror(store: ObjectStore) -> int:
    """Ensure every linked file carries its category tag and threat-name
    attribute, and no stale threat-name attribute. Returns files touched."""
    from mwdb.model import db

    touched = 0
    rows = (
        db.session.query(ThreatSample.object_id)
        .filter(ThreatSample.object_id.isnot(None))
        .distinct()
        .all()
    )
    for (object_id,) in rows:
        file_obj = store.load(object_id)
        if file_obj is None:
            continue
        links = service.links_for_object(object_id)
        wanted_tags = {c for c, _ in links}
        wanted_names = {n for _, n in links}
        changed = False
        for tag in wanted_tags:
            if file_obj.get_tag(tag) is None:
                file_obj.add_tag(tag, commit=False)
                changed = True
        current_names = _current_names(file_obj)
        for name in wanted_names - current_names:
            file_obj.add_attribute(
                ATTRIBUTE_KEY, name, commit=False, check_permissions=False
            )
            changed = True
        for name in current_names - wanted_names:
            file_obj.remove_attribute(ATTRIBUTE_KEY, name, check_permissions=False)
            changed = True
        if changed:
            touched += 1
            db.session.commit()
    return touched


def _current_names(file_obj) -> set[str]:
    # FakeFile exposes .attributes[key]; mwdb File exposes get_attributes(as_dict=True)
    attrs = getattr(file_obj, "attributes", None)
    if isinstance(attrs, dict):
        return set(attrs.get(ATTRIBUTE_KEY, set()))
    values = file_obj.get_attributes(as_dict=True, check_permissions=False)
    return {str(v) for v in values.get(ATTRIBUTE_KEY, [])}


def _commit_message(ingest_stats: IngestStats, export_stats: ExportStats) -> str:
    return (
        f"threatlib sync: +{ingest_stats.new_files} files, "
        f"-{ingest_stats.unlinked} unlinked, "
        f"{ingest_stats.readmes_updated} READMEs ingested; "
        f"exported {export_stats.threats} threats / {export_stats.files_written} files"
    )


def run_once(
    config: SyncConfig, store: ObjectStore, repo: GitRepo | None = None
) -> RunResult:
    repo = repo or GitRepo(
        config.clone_dir,
        config.repo_url,
        branch=config.branch,
        deploy_key=config.deploy_key,
        author=config.author,
    )
    repo.ensure_clone()
    head_before = repo.reset_to_remote()
    manifest = load_manifest(repo.path)

    ingest_stats = ingest(repo.path, manifest, store)
    repair_mirror(store)
    new_manifest, export_stats = export(repo.path, store)

    # export() always stamps a fresh generated_at, which would make every run
    # dirty even when nothing actually changed. When the file/readme content
    # matches the manifest this run started from, put the old timestamp back
    # so the manifest is byte-identical and a clean pass makes no commit.
    if (
        manifest is not None
        and new_manifest.files == manifest.files
        and new_manifest.readmes == manifest.readmes
    ):
        new_manifest.generated_at = manifest.generated_at
        save_manifest(repo.path, new_manifest)

    commit = None
    pushed = False
    if repo.is_dirty():
        commit = repo.commit_and_push(
            _commit_message(ingest_stats, export_stats), push=config.push
        )
        pushed = bool(commit) and config.push
    logger.info(
        "threatlib sync: head=%s ingest=%s export=%s commit=%s pushed=%s",
        head_before[:12],
        ingest_stats,
        export_stats,
        (commit or "")[:12],
        pushed,
    )
    return RunResult(head_before, ingest_stats, export_stats, commit, pushed)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="threatlib-sync")
    parser.add_argument(
        "--once", action="store_true", help="run a single pass and exit"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    config = SyncConfig.from_env()

    from flask import g

    from mwdb.cli.base import create_app

    app = create_app()
    store = MwdbStore(config.share_with)
    while True:
        with app.app_context():
            g.auth_user = None
            try:
                from ..attributes import ensure_attribute_definition

                ensure_attribute_definition()
                run_once(config, store)
            except GitError as e:
                logger.error("threatlib sync: git failure, will retry: %s", e)
            except Exception:
                logger.exception("threatlib sync: run failed, will retry")
                from mwdb.model import db

                db.session.rollback()
        if args.once:
            return 0
        time.sleep(config.interval)
