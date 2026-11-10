import subprocess
from pathlib import Path

import pytest

from threatlib.sync.repo import GitError, GitRepo


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def remote(tmp_path):
    """A bare 'origin' with one commit on trunk, plus a scratch working clone
    to simulate teammates pushing."""
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "trunk", str(bare))
    # Bootstrap: create initial commit in a temporary clone
    temp = tmp_path / "temp-bootstrap"
    _git(tmp_path, "init", "-b", "trunk", str(temp))
    _git(temp, "config", "user.email", "bootstrap@example.com")
    _git(temp, "config", "user.name", "Bootstrap")
    _git(temp, "remote", "add", "origin", str(bare))
    (temp / "threats").mkdir()
    (temp / "threats" / "README.md").write_text("root readme\n")
    _git(temp, "add", "-A")
    _git(temp, "commit", "-q", "-m", "init")
    _git(temp, "push", "-q", "-u", "origin", "trunk")
    # Now create the teammate's working clone
    work = tmp_path / "teammate"
    _git(tmp_path, "clone", "-q", "-b", "trunk", str(bare), str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Teammate")
    return bare, work


def test_clone_reset_and_head(tmp_path, remote):
    bare, work = remote
    repo = GitRepo(tmp_path / "clone", str(bare))
    repo.ensure_clone()
    repo.ensure_clone()  # idempotent
    assert (tmp_path / "clone" / "threats" / "README.md").exists()
    assert repo.head() == _git(work, "rev-parse", "HEAD")

    # teammate pushes; reset picks it up and discards local junk
    (work / "threats" / "new.txt").write_text("x")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "teammate")
    _git(work, "push", "-q", "origin", "trunk")
    (tmp_path / "clone" / "junk.txt").write_text("junk")
    new_head = repo.reset_to_remote()
    assert new_head == _git(work, "rev-parse", "HEAD")
    assert (tmp_path / "clone" / "threats" / "new.txt").exists()
    assert not (tmp_path / "clone" / "junk.txt").exists()
    assert repo.is_dirty() is False


def test_commit_and_push(tmp_path, remote):
    bare, work = remote
    repo = GitRepo(tmp_path / "clone", str(bare), author="Bot <bot@example.com>")
    repo.ensure_clone()
    assert repo.commit_and_push("nothing") is None

    (tmp_path / "clone" / "threats" / "a.php").write_text("<?php")
    assert repo.is_dirty() is True
    sha = repo.commit_and_push("threatlib: add a.php")
    assert sha == repo.head()
    _git(work, "pull", "-q")
    assert (work / "threats" / "a.php").read_text() == "<?php"
    assert _git(work, "log", "-1", "--format=%an <%ae>") == "Bot <bot@example.com>"
    assert _git(work, "log", "-1", "--format=%s") == "threatlib: add a.php"


def test_push_disabled_commits_locally_only(tmp_path, remote):
    bare, work = remote
    repo = GitRepo(tmp_path / "clone", str(bare))
    repo.ensure_clone()
    (tmp_path / "clone" / "threats" / "a.php").write_text("<?php")
    sha = repo.commit_and_push("local only", push=False)
    assert sha is not None
    assert _git(work, "ls-remote", str(bare), "trunk").split()[0] != sha


def test_rejected_push_raises_and_next_reset_recovers(tmp_path, remote):
    bare, work = remote
    repo = GitRepo(tmp_path / "clone", str(bare))
    repo.ensure_clone()
    # remote moves on
    (work / "threats" / "t.txt").write_text("t")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "teammate")
    _git(work, "push", "-q", "origin", "trunk")
    # local diverges
    (tmp_path / "clone" / "threats" / "a.php").write_text("<?php")
    with pytest.raises(GitError):
        repo.commit_and_push("diverged")
    head = repo.reset_to_remote()
    assert head == _git(work, "rev-parse", "HEAD")
    assert not (tmp_path / "clone" / "threats" / "a.php").exists()


def test_deploy_key_sets_ssh_command(tmp_path):
    repo = GitRepo(tmp_path / "c", "git@example.com:x/y.git", deploy_key="/keys/id")
    env = repo._env()
    assert "-i /keys/id" in env["GIT_SSH_COMMAND"]
    assert "StrictHostKeyChecking=accept-new" in env["GIT_SSH_COMMAND"]
    assert "UserKnownHostsFile" not in env["GIT_SSH_COMMAND"]


def test_known_hosts_pins_host_key_and_paths_are_quoted(tmp_path):
    repo = GitRepo(
        tmp_path / "c",
        "git@example.com:x/y.git",
        deploy_key="/keys/my key",
        known_hosts="/run/secrets/known hosts",
    )
    ssh = repo._env()["GIT_SSH_COMMAND"]
    assert "-i '/keys/my key'" in ssh
    assert "-o UserKnownHostsFile='/run/secrets/known hosts'" in ssh
    assert "StrictHostKeyChecking=yes" in ssh and "accept-new" not in ssh


def test_reset_removes_ignored_files(tmp_path, remote):
    bare, work = remote
    (work / ".gitignore").write_text("*.cache\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "ignore")
    _git(work, "push", "-q", "origin", "trunk")
    repo = GitRepo(tmp_path / "clone", str(bare))
    repo.ensure_clone()
    (tmp_path / "clone" / "threats" / "x.cache").write_text("junk")
    repo.reset_to_remote()
    assert not (tmp_path / "clone" / "threats" / "x.cache").exists()


def test_stale_index_lock_is_removed(tmp_path, remote, caplog):
    import os
    import time

    bare, work = remote
    repo = GitRepo(tmp_path / "clone", str(bare))
    repo.ensure_clone()
    lock = tmp_path / "clone" / ".git" / "index.lock"
    lock.write_text("")
    old = time.time() - 11 * 60
    os.utime(lock, (old, old))
    repo.reset_to_remote()
    assert not lock.exists()
    assert "index.lock" in caplog.text

    # a fresh lock belongs to a live git process: leave it (git then fails)
    lock.write_text("")
    with pytest.raises(GitError):
        repo.reset_to_remote()
    assert lock.exists()
