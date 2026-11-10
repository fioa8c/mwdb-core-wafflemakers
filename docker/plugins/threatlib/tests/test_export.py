import os
from pathlib import Path

import pytest

from threatlib import service
from threatlib.sync.export import ExportError, ExportStats, clear_owned, export
from threatlib.sync.ingest import ingest
from threatlib.sync.manifest import MANIFEST_NAME, load_manifest, sha256_bytes


def w(root: Path, rel: str, data: bytes = b"<?php") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != MANIFEST_NAME
    }


def test_clear_owned_keeps_category_root_files(tmp_path):
    w(tmp_path, "threats/README.md", b"keep")
    w(tmp_path, "threats/FIO-1/a.php")
    w(tmp_path, "webshells/c99.php")
    w(tmp_path, "webshells/helper/x.php")
    w(tmp_path, "false-positives/x.php", b"keep")
    clear_owned(tmp_path)
    assert tree(tmp_path) == {"threats/README.md": b"keep", "false-positives/x.php": b"keep"}
    assert (tmp_path / "threats").is_dir() and (tmp_path / "webshells").is_dir()


def test_clear_owned_unlinks_symlinked_dir_without_touching_target(tmp_path, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    w(outside, "keep.php", b"keep")
    repo = tmp_path / "repo"
    (repo / "threats").mkdir(parents=True)
    (repo / "threats/linkdir").symlink_to(outside, target_is_directory=True)
    (repo / "webshells").mkdir()
    (repo / "webshells/link.php").symlink_to(outside / "keep.php")
    clear_owned(repo)
    assert not os.path.lexists(repo / "threats/linkdir")
    assert not os.path.lexists(repo / "webshells/link.php")
    assert (outside / "keep.php").read_bytes() == b"keep"


def test_export_writes_back_preserved_paths(tmp_path, store):
    t = service.create_threat("FIO-1", "threats")
    service.link_sample(t, store.add(b"<?php a();"), "a.php")
    w(tmp_path, "threats/FIO-1/a.php", b"<?php a();")
    w(tmp_path, "threats/B/b.php", b"<?php b();")  # failed to ingest
    (tmp_path / "threats/B/b.php").chmod(0o755)
    w(tmp_path, "threats/My Sample (2)/x.php", b"bad name")
    w(tmp_path, "threats/OLD/o.php", b"stale")  # not preserved -> removed
    # a preserved path that collides with a table-driven one: table wins
    w(tmp_path, "threats/FIO-1/a.php", b"repo version")
    preserve = {"threats/B/b.php", "threats/My Sample (2)/x.php", "threats/FIO-1/a.php"}

    manifest, _ = export(tmp_path, store, preserve=preserve)
    assert tree(tmp_path) == {
        "threats/FIO-1/a.php": b"<?php a();",
        "threats/B/b.php": b"<?php b();",
        "threats/My Sample (2)/x.php": b"bad name",
    }
    assert (tmp_path / "threats/B/b.php").stat().st_mode & 0o777 == 0o755
    assert set(manifest.files) == {"threats/FIO-1/a.php"}
    assert set(load_manifest(tmp_path).files) == {"threats/FIO-1/a.php"}


def test_export_round_trips_symlinks(tmp_path, store, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret").write_bytes(b"deploy key material")
    repo = tmp_path / "repo"
    w(repo, "threats/FIO-1/a.php", b"<?php a();")
    (repo / "threats/FIO-1/link.php").symlink_to(outside / "secret")
    (repo / "threats/linkdir").symlink_to(outside, target_is_directory=True)

    stats = ingest(repo, None, store)
    assert stats.skipped == 2
    assert all(f.content != b"deploy key material" for f in store.by_sha.values())
    manifest, _ = export(repo, store, preserve=stats.preserved)
    assert os.readlink(repo / "threats/FIO-1/link.php") == str(outside / "secret")
    assert os.readlink(repo / "threats/linkdir") == str(outside)
    assert (outside / "secret").read_bytes() == b"deploy key material"
    assert set(manifest.files) == {"threats/FIO-1/a.php"}


def test_export_writes_layout_and_manifest(tmp_path, store):
    t = service.create_threat("FIO-1", "threats", readme="# FIO-1\n")
    service.link_sample(t, store.add(b"<?php a();"), "a.php")
    service.link_sample(t, store.add(b"<?php n();"), "0154/wp-admin/menu.php")
    service.link_sample(t, None, "empty.php")
    c = service.create_threat("c99", "webshells", flat=True)
    service.link_sample(c, store.add(b"<?php c();"), "c99.php")
    w(tmp_path, "threats/README.md", b"root")

    manifest, stats = export(tmp_path, store)
    assert stats == ExportStats(files_written=4, readmes_written=1, threats=2)
    assert tree(tmp_path) == {
        "threats/README.md": b"root",
        "threats/FIO-1/README.md": b"# FIO-1\n",
        "threats/FIO-1/a.php": b"<?php a();",
        "threats/FIO-1/0154/wp-admin/menu.php": b"<?php n();",
        "threats/FIO-1/empty.php": b"",
        "webshells/c99.php": b"<?php c();",
    }
    assert manifest.files == {
        "threats/FIO-1/a.php": sha256_bytes(b"<?php a();"),
        "threats/FIO-1/0154/wp-admin/menu.php": sha256_bytes(b"<?php n();"),
        "threats/FIO-1/empty.php": sha256_bytes(b""),
        "webshells/c99.php": sha256_bytes(b"<?php c();"),
    }
    assert manifest.readmes == {"threats/FIO-1": sha256_bytes(b"# FIO-1\n")}
    assert load_manifest(tmp_path).files == manifest.files


def test_export_refuses_threat_name_colliding_with_root_file(tmp_path, store):
    w(tmp_path, "threats/README.md", b"root")
    t = service.create_threat("README.md", "threats")
    service.link_sample(t, store.add(b"<?php a();"), "a.php")
    other = service.create_threat("FIO-1", "threats")
    service.link_sample(other, store.add(b"<?php b();"), "b.php")

    with pytest.raises(ExportError):
        export(tmp_path, store)

    assert tree(tmp_path) == {"threats/README.md": b"root"}
    assert not (tmp_path / MANIFEST_NAME).exists()


def test_export_removes_stale_files(tmp_path, store):
    t = service.create_threat("FIO-1", "threats")
    service.link_sample(t, store.add(b"x"), "a.php")
    w(tmp_path, "threats/FIO-1/stale.php", b"stale")
    w(tmp_path, "threats/OLD/o.php", b"old")
    export(tmp_path, store)
    assert tree(tmp_path) == {"threats/FIO-1/a.php": b"x"}


def test_round_trip_is_byte_identical(tmp_path, store):
    src = tmp_path / "src"
    w(src, "threats/README.md", b"category readme")
    w(src, "threats/readme-builder.php", b"builder")
    w(src, "threats/FIO-1/README.md", "# FIO-1 ü\n".encode("utf-8"))
    w(src, "threats/FIO-1/a.php", b"<?php a();")
    w(src, "threats/FIO-1/.hidden", b"dot")
    w(src, "threats/FIO-1/dup.php", b"<?php a();")  # same bytes, second path
    w(src, "threats/sources/0154/wp-admin/menu.php", b"<?php m();")
    w(src, "threats/sources/0154/README.md", b"deep readme is a sample")
    w(src, "threats/empty_1/e.php", b"")
    w(src, "for-later-review/wf-1/x.php", b"<?php x();")
    w(src, "for-later-review/README.md", b"flr root")
    w(src, "webshells/c99.php", b"<?php c99();")
    w(src, "webshells/helper/mass.php", b"<?php mass();")
    w(src, "escalated_issues_samples/E-1/z.php", b"<?php z();")
    w(src, "false-positives/wp/index.php", b"benign")
    before = tree(src)

    ingest(src, None, store)
    manifest, _ = export(src, store)
    assert tree(src) == before

    # second cycle from the exported tree changes nothing
    stats = ingest(src, manifest, store)
    assert stats.new_files == 0 and stats.unlinked == 0 and stats.readmes_updated == 0
    export(src, store)
    assert tree(src) == before
