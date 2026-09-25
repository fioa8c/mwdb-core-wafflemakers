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


def test_link_failure_rolls_back_the_threat_it_created(repo, store, monkeypatch):
    real = service.link_sample

    def flaky(threat, file_obj, rel_path, **kwargs):
        if rel_path == "x.php":
            raise RuntimeError("link failed")
        return real(threat, file_obj, rel_path, **kwargs)

    monkeypatch.setattr(service, "link_sample", flaky)
    stats = ingest(repo, None, store)
    assert stats.errors == 1
    # the threat created just before the failing link_sample call must not
    # survive: it was created and linked in the same per-file transaction.
    assert service.get_threat("wf-1") is None
    assert stats.new_threats == 6
    assert service.get_threat("FIO-1") is not None


def test_errors_and_skips_are_preserved(repo, store, monkeypatch):
    w(repo, "threats/My Sample (2)/x.php", b"<?php bad-name();")
    w(repo, "threats/My Sample (2)/README.md", b"# bad name\n")
    real = store.get_or_create

    def flaky(file_name, stream):
        if file_name == "a.php":
            raise RuntimeError("storage down")
        return real(file_name, stream)

    monkeypatch.setattr(store, "get_or_create", flaky)
    stats = ingest(repo, None, store)
    assert stats.errors == 1 and stats.skipped == 2
    assert stats.preserved == {
        "threats/FIO-1/a.php",
        "threats/My Sample (2)/x.php",
        "threats/My Sample (2)/README.md",
    }
    # preserved is informational: it does not take part in equality
    assert IngestStats(errors=1) == IngestStats(errors=1, preserved={"x"})


def test_symlinks_are_skipped_and_preserved(repo, store, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    secret = outside / "secret"
    secret.write_bytes(b"deploy key material")
    (repo / "threats/FIO-1/link.php").symlink_to(secret)
    (repo / "threats/linkdir").symlink_to(outside, target_is_directory=True)
    (repo / "threats/root-link").symlink_to(secret)

    stats = ingest(repo, None, store)
    assert stats.skipped == 3
    assert stats.preserved == {
        "threats/FIO-1/link.php",
        "threats/linkdir",
        "threats/root-link",
    }
    assert sha256_bytes(b"deploy key material") not in store.by_sha
    assert service.get_threat("linkdir") is None
    assert "link.php" not in [l.rel_path for l in service.get_threat("FIO-1").samples]


def test_symlinked_category_dir_is_not_walked(tmp_path, store, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    w(outside, "X/secret.php", b"secret")
    (tmp_path / "threats").symlink_to(outside, target_is_directory=True)
    stats = ingest(tmp_path, None, store)
    assert stats.skipped == 1 and stats.preserved == {"threats"}
    assert service.get_threat("X") is None and store.by_sha == {}


def test_same_name_in_other_category_is_skipped(repo, store):
    ingest(repo, None, store)
    w(repo, "for-later-review/FIO-1/b.php", b"<?php other-category();")
    stats = ingest(repo, None, store)
    assert stats.skipped == 1 and stats.preserved == {"for-later-review/FIO-1/b.php"}
    fio1 = service.get_threat("FIO-1")
    assert fio1.category == "threats"
    assert "b.php" not in [l.rel_path for l in fio1.samples]


def test_flat_and_dir_threat_with_same_name_is_skipped(tmp_path, store):
    w(tmp_path, "webshells/foo/x.php", b"<?php dir();")
    w(tmp_path, "webshells/foo.php", b"<?php flat();")
    stats = ingest(tmp_path, None, store)
    assert stats.new_threats == 1 and stats.skipped == 1
    assert stats.preserved == {"webshells/foo.php"}
    foo = service.get_threat("foo")
    assert foo.flat is False and [l.rel_path for l in foo.samples] == ["x.php"]
