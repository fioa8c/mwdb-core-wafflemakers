"""Thin git wrapper. Every operation shells out to the git CLI."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger("mwdb.plugin.threatlib.sync")


class GitError(Exception):
    pass


class GitRepo:
    def __init__(
        self,
        path: Path,
        url: str,
        branch: str = "trunk",
        deploy_key: str | None = None,
        author: str = "mwdb-threatlib-bot <noreply@wafflemakers.xyz>",
    ):
        self.path = Path(path)
        self.url = url
        self.branch = branch
        self.deploy_key = deploy_key
        self.author = author

    # --- plumbing ---------------------------------------------------------
    def _env(self) -> dict:
        env = dict(os.environ)
        ssh = "ssh -o StrictHostKeyChecking=accept-new"
        if self.deploy_key:
            ssh += f" -i {self.deploy_key} -o IdentitiesOnly=yes"
        env["GIT_SSH_COMMAND"] = ssh
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd or self.path),
            env=self._env(),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout.strip()

    # --- operations -------------------------------------------------------
    def ensure_clone(self) -> None:
        if (self.path / ".git").is_dir():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._git(
            "clone",
            "-q",
            "-b",
            self.branch,
            self.url,
            str(self.path),
            cwd=self.path.parent,
        )

    def head(self) -> str:
        return self._git("rev-parse", "HEAD")

    def reset_to_remote(self) -> str:
        self._git("fetch", "-q", "origin", self.branch)
        self._git("reset", "-q", "--hard", f"origin/{self.branch}")
        self._git("clean", "-qfd")
        return self.head()

    def is_dirty(self) -> bool:
        return bool(self._git("status", "--porcelain"))

    def commit_and_push(self, message: str, push: bool = True) -> str | None:
        self._git("add", "-A")
        if not self._git("status", "--porcelain"):
            return None
        name, _, email = self.author.partition(" <")
        email = email.rstrip(">")
        self._git(
            "-c",
            f"user.name={name}",
            "-c",
            f"user.email={email}",
            "commit",
            "-q",
            "-m",
            message,
        )
        sha = self.head()
        if push:
            self._git("push", "-q", "origin", f"HEAD:{self.branch}")
            logger.info("threatlib sync: pushed %s to %s", sha[:12], self.branch)
        return sha
