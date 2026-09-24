from pathlib import Path

import pytest

from threatlib import service
from threatlib.attributes import ATTRIBUTE_KEY
from threatlib.sync.ingest import IngestStats, classify, ingest
from threatlib.sync.manifest import Manifest, sha256_bytes


def w(root: Path, rel: str, data: bytes = b"<?php") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


@pytest.fixture
def repo(tmp_path):
    w(tmp_path, "threats/README.md", b"category readme")
    w(tmp_path, "threats/readme-builder.php", b"builder")
    w(tmp_path, "threats/FIO-1/README.md", b"# FIO-1\n")
    w(tmp_path, "threats/FIO-1/a.php", b"<?php a();")
    w(tmp_path, "threats/FIO-1/.hidden", b"dot")
    w(tmp_path, "threats/sources/0154/wp-admin/menu.php", b"<?php m();")
    w(tmp_path, "threats/sources/0154/README.md", b"deep readme is a sample")
    w(tmp_path, "threats/empty_1/e.php", b"")
    w(tmp_path, "for-later-review/wf-1/x.php", b"<?php x();")
    w(tmp_path, "webshells/c99.php", b"<?php c99();")
    w(tmp_path, "webshells/helper/mass.php", b"<?php mass();")
    w(tmp_path, "escalated_issues_samples/E-1/z.php", b"<?php z();")
    w(tmp_path, "false-positives/wp/index.php", b"benign")
    return tmp_path


def test_classify(repo):
    c = lambda rel: classify(repo, repo / rel)  # noqa: E731
    assert c("threats/FIO-1/a.php") == ("threats", "FIO-1", "a.php", False, False)
    assert c("threats/FIO-1/README.md") == ("threats", "FIO-1", "README.md", False, True)
    assert c("threats/sources/0154/README.md") == ("threats", "sources", "0154/README.md", False, False)
    assert c("threats/sources/0154/wp-admin/menu.php") == ("threats", "sources", "0154/wp-admin/menu.php", False, False)
    assert c("webshells/c99.php") == ("webshells", "c99", "c99.php", True, False)
    assert c("webshells/helper/mass.php") == ("webshells", "helper", "mass.php", False, False)
    assert c("threats/README.md") is None
    assert c("threats/readme-builder.php") is None
    assert c("false-positives/wp/index.php") is None
    assert c(".mwdb-threatlib.json") is None


def test_first_run_imports_everything(repo, store):
    stats = ingest(repo, None, store)
    assert stats == IngestStats(new_files=9, new_threats=7, readmes_updated=1, unlinked=0, skipped=0, errors=0)

    fio1 = service.get_threat("FIO-1")
    assert fio1.category == "threats" and fio1.readme == "# FIO-1\n" and fio1.flat is False
    assert sorted(l.rel_path for l in fio1.samples) == [".hidden", "a.php"]

    sources = service.get_threat("sources")
    assert sorted(l.rel_path for l in sources.samples) == ["0154/README.md", "0154/wp-admin/menu.php"]
    assert sources.readme is None

    empty = service.get_threat("empty_1")
    assert empty.samples[0].object_id is None

    c99 = service.get_threat("c99")
    assert c99.flat is True and c99.category == "webshells"
    assert c99.samples[0].rel_path == "c99.php"
    helper = service.get_threat("helper")
    assert helper.flat is False

    f = store.by_sha[sha256_bytes(b"<?php a();")]
    assert f.tags == {"threats"} and f.attributes[ATTRIBUTE_KEY] == {"FIO-1"}
    assert service.get_threat("wp") is None  # false-positives ignored


def test_first_run_is_idempotent(repo, store):
    ingest(repo, None, store)
    stats = ingest(repo, None, store)
    assert stats.new_files == 0 and stats.new_threats == 0 and stats.readmes_updated == 0


def test_manifest_gates_new_files_and_deletions(repo, store):
    ingest(repo, None, store)
    manifest = Manifest(
        files={
            "threats/FIO-1/a.php": sha256_bytes(b"<?php a();"),
            "threats/FIO-1/.hidden": sha256_bytes(b"dot"),
            "threats/FIO-1/gone.php": sha256_bytes(b"gone"),
        },
        readmes={"threats/FIO-1": sha256_bytes(b"# FIO-1\n")},
    )
    fio1 = service.get_threat("FIO-1")
    service.link_sample(fio1, store.add(b"gone"), "gone.php")
    # a file MWDB deleted (in manifest, still on disk) must NOT come back:
    service.unlink_sample(fio1, "a.php")
    # a new file appears on disk:
    w(repo, "threats/FIO-1/b.php", b"<?php b();")
    # gone.php is in the manifest but no longer on disk => repo-side deletion

    stats = ingest(repo, manifest, store)
    assert stats.unlinked == 1 and stats.new_files >= 1
    names = sorted(l.rel_path for l in service.get_threat("FIO-1").samples)
    assert "b.php" in names and "gone.php" not in names and "a.php" not in names


def test_readme_rules(repo, store):
    ingest(repo, None, store)
    fio1 = service.get_threat("FIO-1")
    old_hash = sha256_bytes(b"# FIO-1\n")
    manifest = Manifest(files={}, readmes={"threats/FIO-1": old_hash})

    # repo edit, MWDB untouched -> repo wins
    w(repo, "threats/FIO-1/README.md", b"# repo edit\n")
    stats = ingest(repo, manifest, store)
    assert stats.readmes_updated == 1
    assert service.get_threat("FIO-1").readme == "# repo edit\n"

    # repo edit, MWDB also edited since manifest -> MWDB wins
    manifest = Manifest(files={}, readmes={"threats/FIO-1": sha256_bytes(b"# repo edit\n")})
    service.set_readme(service.get_threat("FIO-1"), "# mwdb edit\n")
    w(repo, "threats/FIO-1/README.md", b"# repo edit 2\n")
    stats = ingest(repo, manifest, store)
    assert stats.readmes_updated == 0
    assert service.get_threat("FIO-1").readme == "# mwdb edit\n"

    # repo deleted README, MWDB unchanged -> cleared
    manifest = Manifest(files={}, readmes={"threats/FIO-1": sha256_bytes(b"# mwdb edit\n")})
    (repo / "threats/FIO-1/README.md").unlink()
    stats = ingest(repo, manifest, store)
    assert stats.readmes_updated == 1 and service.get_threat("FIO-1").readme is None


def test_errors_are_counted_and_do_not_abort(repo, store, monkeypatch):
    real = store.get_or_create

    def flaky(file_name, stream):
        if file_name == "x.php":
            raise RuntimeError("storage down")
        return real(file_name, stream)

    monkeypatch.setattr(store, "get_or_create", flaky)
    stats = ingest(repo, None, store)
    assert stats.errors == 1 and stats.new_files == 8
    # the failed file's transaction was rolled back (threat creation included);
    # the next run will pick it up. Everything else landed.
    assert service.get_threat("wf-1") is None
    assert service.get_threat("FIO-1") is not None
