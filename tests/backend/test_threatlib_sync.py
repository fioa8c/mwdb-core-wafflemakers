"""End-to-end test of the threatlib-sync sidecar against a real git remote.

Pushes a sample into the sync's bare repo, runs one sync pass, checks the
threat over the API and the manifest in trunk, then unlinks the sample via
the API and checks the next pass removes it from trunk.

Skipped unless both are set:
    MWDB_THREATLIB_E2E_REPO      path to the bare repo the sync pulls/pushes
                                 (dev: <worktree>/dev-threatlib-origin.git)
    MWDB_THREATLIB_E2E_SYNC_CMD  shell command running one sync pass, e.g.
        ./compose.sh --with dev --with plugins run --rm \
            -e MWDB_THREATLIB_CLONE_DIR=/tmp/clone threatlib-sync threatlib-sync --once
Optional:
    MWDB_THREATLIB_E2E_CWD       directory the command runs in (default: repo root)
"""

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

REPO = os.environ.get("MWDB_THREATLIB_E2E_REPO")
SYNC_CMD = os.environ.get("MWDB_THREATLIB_E2E_SYNC_CMD")
SYNC_CWD = os.environ.get(
    "MWDB_THREATLIB_E2E_CWD", str(Path(__file__).resolve().parents[2])
)
MANIFEST = ".mwdb-threatlib.json"

pytestmark = pytest.mark.skipif(
    not (REPO and SYNC_CMD),
    reason="set MWDB_THREATLIB_E2E_REPO and MWDB_THREATLIB_E2E_SYNC_CMD",
)


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _sync():
    proc = subprocess.run(
        SYNC_CMD, shell=True, cwd=SYNC_CWD, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout + proc.stderr


def _trunk(clone):
    _git(clone, "fetch", "-q", "origin", "trunk")
    _git(clone, "reset", "-q", "--hard", "origin/trunk")
    manifest_path = clone / MANIFEST
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    return manifest.get("files", {}), manifest.get("readmes", {})


def test_sync_round_trip(admin_session, tmp_path):
    name = "e2e-sync-" + uuid.uuid4().hex[:8]
    content = f"<?php echo '{name}';".encode()
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", "-b", "trunk", REPO, str(clone))
    _git(clone, "config", "user.email", "e2e@example.com")
    _git(clone, "config", "user.name", "e2e")
    threat_dir = clone / "threats" / name
    threat_dir.mkdir(parents=True)
    (threat_dir / "a.php").write_bytes(content)
    (threat_dir / "README.md").write_text(f"# {name}\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", f"e2e: add {name}")
    _git(clone, "push", "-q", "origin", "trunk")

    try:
        # repo -> MWDB
        _sync()
        threat = admin_session.request("GET", f"/threatlib/threat/{name}")
        assert threat["category"] == "threats" and threat["readme"] == f"# {name}\n"
        assert threat["sample_count"] == 1
        assert [s["rel_path"] for s in threat["samples"]] == ["a.php"]
        sha256 = threat["samples"][0]["sha256"]
        attrs = admin_session.get_attributes(sha256)["attributes"]
        assert any(a["key"] == "jpop_threat_name" and a["value"] == name for a in attrs)

        files, readmes = _trunk(clone)
        assert f"threats/{name}/a.php" in files
        assert f"threats/{name}" in readmes
        assert (threat_dir / "a.php").read_bytes() == content

        # MWDB -> repo: unlink via the API, next pass removes the file
        resp = admin_session.session.delete(
            admin_session.mwdb_url + f"/threatlib/threat/{name}/sample/{sha256}",
            params={"rel_path": "a.php"},
        )
        assert resp.status_code == 200, resp.text
        _sync()
        files, _ = _trunk(clone)
        assert f"threats/{name}/a.php" not in files
        assert not (threat_dir / "a.php").exists()
        assert (threat_dir / "README.md").exists()
    finally:
        # leave the shared dev repo clean: delete the threat, sync it out
        admin_session.session.delete(admin_session.mwdb_url + f"/threatlib/threat/{name}")
        _sync()
    files, readmes = _trunk(clone)
    assert not any(k.startswith(f"threats/{name}/") for k in files)
    assert f"threats/{name}" not in readmes
