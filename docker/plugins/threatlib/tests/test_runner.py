import subprocess
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
    monkeypatch.delenv("MWDB_THREATLIB_REPO_URL")
    with pytest.raises(SystemExit):
        SyncConfig.from_env()
