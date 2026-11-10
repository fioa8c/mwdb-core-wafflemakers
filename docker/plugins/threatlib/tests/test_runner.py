import subprocess
import sys
from pathlib import Path

import pytest

from threatlib import service
from threatlib.sync.manifest import MANIFEST_NAME
from threatlib.sync.repo import GitRepo
from threatlib.sync.runner import SyncConfig, repair_mirror, run_once


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def w(root: Path, rel: str, data: bytes = b"<?php") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


@pytest.fixture
def remote(tmp_path):
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "trunk", str(bare))
    # git cannot `clone -b trunk` an empty bare repo, so seed it first via a
    # throwaway bootstrap clone (same pattern as tests/test_repo.py).
    bootstrap = tmp_path / "bootstrap"
    _git(tmp_path, "init", "-q", "-b", "trunk", str(bootstrap))
    _git(bootstrap, "config", "user.email", "bootstrap@example.com")
    _git(bootstrap, "config", "user.name", "Bootstrap")
    _git(bootstrap, "remote", "add", "origin", str(bare))
    (bootstrap / ".gitkeep").write_text("")
    _git(bootstrap, "add", "-A")
    _git(bootstrap, "commit", "-q", "-m", "bootstrap")
    _git(bootstrap, "push", "-q", "-u", "origin", "trunk")

    work = tmp_path / "teammate"
    _git(tmp_path, "clone", "-q", "-b", "trunk", str(bare), str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Teammate")
    w(work, "threats/README.md", b"root")
    w(work, "threats/FIO-1/README.md", b"# FIO-1\n")
    w(work, "threats/FIO-1/a.php", b"<?php a();")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "init")
    _git(work, "push", "-q", "origin", "trunk")
    return bare, work


def _config(tmp_path, bare, push=True):
    return SyncConfig(
        repo_url=str(bare), clone_dir=tmp_path / "clone", deploy_key=None,
        interval=1, push=push, author="Bot <bot@example.com>",
    )


def test_first_run_ingests_and_commits_manifest_only(tmp_path, remote, store):
    bare, work = remote
    result = run_once(_config(tmp_path, bare), store)
    assert result.ingest.new_files == 1 and result.ingest.new_threats == 1
    assert result.commit is not None and result.pushed is True
    _git(work, "pull", "-q")
    assert _git(work, "show", "--stat", "--format=", "HEAD").count("|") == 1
    assert (work / MANIFEST_NAME).exists()
    assert (work / "threats/FIO-1/a.php").read_bytes() == b"<?php a();"


def test_clean_second_run_makes_no_commit(tmp_path, remote, store):
    bare, work = remote
    run_once(_config(tmp_path, bare), store)
    result = run_once(_config(tmp_path, bare), store)
    assert result.commit is None and result.pushed is False


def test_mwdb_changes_are_exported_and_repo_changes_ingested(tmp_path, remote, store):
    bare, work = remote
    cfg = _config(tmp_path, bare)
    run_once(cfg, store)

    # MWDB: add a sample, delete one, edit a README
    fio1 = service.get_threat("FIO-1")
    service.link_sample(fio1, store.add(b"<?php b();"), "b.php")
    service.unlink_sample(fio1, "a.php")
    service.set_readme(fio1, "# from mwdb\n")
    # repo: teammate adds a threat
    _git(work, "pull", "-q")
    w(work, "for-later-review/wf-9/x.php", b"<?php x();")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "teammate")
    _git(work, "push", "-q", "origin", "trunk")

    result = run_once(cfg, store)
    assert result.ingest.new_files == 1 and result.commit is not None
    _git(work, "pull", "-q")
    assert not (work / "threats/FIO-1/a.php").exists()
    assert (work / "threats/FIO-1/b.php").exists()
    assert (work / "threats/FIO-1/README.md").read_bytes() == b"# from mwdb\n"
    assert (work / "for-later-review/wf-9/x.php").exists()
    assert service.get_threat("wf-9") is not None
    assert "+1 files" in _git(work, "log", "-1", "--format=%s")

    # a third run is clean, and a.php did not come back
    result = run_once(cfg, store)
    assert result.commit is None
    assert "a.php" not in [l.rel_path for l in service.get_threat("FIO-1").samples]


def test_push_disabled_leaves_remote_untouched(tmp_path, remote, store):
    bare, work = remote
    result = run_once(_config(tmp_path, bare, push=False), store)
    assert result.commit is not None and result.pushed is False
    assert _git(work, "ls-remote", str(bare), "trunk").split()[0] == _git(
        work, "rev-parse", "HEAD"
    )


def test_ingest_exception_skips_export_and_push(tmp_path, remote, store, monkeypatch):
    bare, work = remote
    import threatlib.sync.runner as runner

    def boom(*a, **k):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(runner, "ingest", boom)
    with pytest.raises(RuntimeError):
        run_once(_config(tmp_path, bare), store)
    assert not (tmp_path / "clone" / MANIFEST_NAME).exists()


def test_repair_mirror_reapplies_and_strips(tmp_path, store):
    from threatlib.attributes import ATTRIBUTE_KEY

    t = service.create_threat("FIO-1", "threats")
    f = store.add(b"x")
    service.link_sample(t, f, "a.php")
    f.tags.clear()
    f.attributes[ATTRIBUTE_KEY].add("stale-name")
    touched = repair_mirror(store)
    assert touched == 1
    assert f.tags == {"threats"} and f.attributes[ATTRIBUTE_KEY] == {"FIO-1"}


def _push(work, rel, data):
    _git(work, "pull", "-q")
    w(work, rel, data)
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", f"add {rel}")
    _git(work, "push", "-q", "origin", "trunk")


def _manifest_files(work):
    import json

    _git(work, "pull", "-q")
    return set(json.loads((work / MANIFEST_NAME).read_text())["files"])


def test_failed_ingest_is_preserved_in_trunk_and_retried(tmp_path, remote, store, monkeypatch, caplog):
    bare, work = remote
    cfg = _config(tmp_path, bare)
    run_once(cfg, store)
    _push(work, "threats/B/b.php", b"<?php b();")

    real = store.get_or_create

    def flaky(file_name, stream):
        if file_name == "b.php":
            raise RuntimeError("storage down")
        return real(file_name, stream)

    monkeypatch.setattr(store, "get_or_create", flaky)
    result = run_once(cfg, store)
    assert result.ingest.errors == 1 and result.ingest.preserved == {"threats/B/b.php"}
    assert "threats/B/b.php" in caplog.text and "preserved" in caplog.text
    _git(work, "pull", "-q")
    assert (work / "threats/B/b.php").read_bytes() == b"<?php b();"
    assert "threats/B/b.php" not in _manifest_files(work)
    assert service.get_threat("B") is None

    monkeypatch.setattr(store, "get_or_create", real)
    result = run_once(cfg, store)
    assert result.ingest.new_files == 1 and result.ingest.preserved == set()
    assert [l.rel_path for l in service.get_threat("B").samples] == ["b.php"]
    assert "threats/B/b.php" in _manifest_files(work)
    assert (work / "threats/B/b.php").read_bytes() == b"<?php b();"


def test_invalid_threat_dir_is_preserved_across_runs(tmp_path, remote, store):
    bare, work = remote
    cfg = _config(tmp_path, bare)
    _push(work, "threats/My Sample (2)/x.php", b"<?php x();")
    for _ in range(3):
        result = run_once(cfg, store)
        assert result.ingest.skipped == 1
        assert result.ingest.preserved == {"threats/My Sample (2)/x.php"}
        _git(work, "pull", "-q")
        assert (work / "threats/My Sample (2)/x.php").read_bytes() == b"<?php x();"
        assert not any("My Sample" in k for k in _manifest_files(work))
    first_commit = _git(work, "log", "--format=%s", "-1")
    assert "1 paths preserved" in first_commit


def test_push_rejection_leaves_clone_to_be_reset(tmp_path, remote, store, monkeypatch):
    from threatlib.sync.repo import GitError

    bare, work = remote
    cfg = _config(tmp_path, bare)
    real_reset = GitRepo.reset_to_remote

    def reset_then_teammate_pushes(self):
        head = real_reset(self)
        _push(work, "threats/FIO-1/race.php", b"<?php race();")
        return head

    monkeypatch.setattr(GitRepo, "reset_to_remote", reset_then_teammate_pushes)
    with pytest.raises(GitError):
        run_once(cfg, store)
    local_commit = _git(tmp_path / "clone", "rev-parse", "HEAD")
    assert local_commit != _git(work, "rev-parse", "HEAD")

    monkeypatch.setattr(GitRepo, "reset_to_remote", real_reset)
    result = run_once(cfg, store)
    assert result.pushed is True
    clone_log = _git(tmp_path / "clone", "log", "--format=%H")
    assert local_commit not in clone_log.split()
    _git(work, "pull", "-q")
    assert (work / "threats/FIO-1/race.php").exists() and (work / MANIFEST_NAME).exists()


def test_main_sets_sync_user_and_fails_fast_without_one(monkeypatch):
    from flask import Flask, g

    import threatlib.attributes as attributes
    import threatlib.sync.runner as runner

    monkeypatch.setenv("MWDB_THREATLIB_REPO_URL", "/nowhere.git")
    monkeypatch.setattr(sys.modules["mwdb.cli.base"], "create_app", lambda: Flask("t"))
    monkeypatch.setattr(attributes, "ensure_attribute_definition", lambda: None)
    seen = {}

    def fake_run_once(config, store):
        seen["user"] = g.auth_user

    monkeypatch.setattr(runner, "run_once", fake_run_once)

    lookups = []
    bot = object()

    def resolve(login):
        lookups.append(login)
        return bot

    monkeypatch.setattr(runner, "_resolve_user", resolve)
    assert runner.main(["--once"]) == 0
    assert seen["user"] is bot and lookups == [None]

    monkeypatch.setenv("MWDB_THREATLIB_USER", "threatlib-bot")
    seen.clear()
    monkeypatch.setattr(runner, "_resolve_user", lambda login: None)
    assert runner.main(["--once"]) == 2
    assert seen == {}


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("MWDB_THREATLIB_REPO_URL", "git@x:y.git")
    monkeypatch.setenv("MWDB_THREATLIB_DEPLOY_KEY", "/k")
    monkeypatch.setenv("MWDB_THREATLIB_SYNC_INTERVAL", "60")
    monkeypatch.setenv("MWDB_THREATLIB_PUSH", "0")
    monkeypatch.setenv("MWDB_THREATLIB_CLONE_DIR", "/data/r")
    cfg = SyncConfig.from_env()
    assert cfg.repo_url == "git@x:y.git" and cfg.deploy_key == "/k"
    assert cfg.interval == 60 and cfg.push is False and str(cfg.clone_dir) == "/data/r"
    assert cfg.branch == "trunk" and cfg.share_with == "public"
    assert cfg.user is None and cfg.known_hosts is None
    monkeypatch.setenv("MWDB_THREATLIB_USER", "threatlib-bot")
    monkeypatch.setenv("MWDB_THREATLIB_KNOWN_HOSTS", "/run/secrets/kh")
    cfg = SyncConfig.from_env()
    assert cfg.user == "threatlib-bot" and cfg.known_hosts == "/run/secrets/kh"
    monkeypatch.delenv("MWDB_THREATLIB_REPO_URL")
    with pytest.raises(SystemExit):
        SyncConfig.from_env()
