import json

from threatlib.sync.manifest import (
    MANIFEST_NAME,
    Manifest,
    load_manifest,
    save_manifest,
    sha256_bytes,
)


def test_sha256_bytes():
    assert sha256_bytes(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_round_trip(tmp_path):
    assert load_manifest(tmp_path) is None
    m = Manifest(files={"threats/FIO-1/a.php": "aa"}, readmes={"threats/FIO-1": "bb"})
    save_manifest(tmp_path, m)
    raw = json.loads((tmp_path / MANIFEST_NAME).read_text())
    assert raw["version"] == 1 and raw["files"] == m.files and raw["readmes"] == m.readmes
    assert raw["generated_at"]
    loaded = load_manifest(tmp_path)
    assert loaded.files == m.files and loaded.readmes == m.readmes
    assert loaded.generated_at == raw["generated_at"]


def test_save_is_deterministic_and_sorted(tmp_path):
    m = Manifest(files={"b": "2", "a": "1"}, readmes={}, generated_at="fixed")
    save_manifest(tmp_path, m)
    text = (tmp_path / MANIFEST_NAME).read_text()
    assert text.index('"a"') < text.index('"b"')
    assert text.endswith("\n")


def test_load_rejects_unknown_version(tmp_path):
    (tmp_path / MANIFEST_NAME).write_text('{"version": 99, "files": {}, "readmes": {}}')
    try:
        load_manifest(tmp_path)
        assert False
    except ValueError:
        pass
