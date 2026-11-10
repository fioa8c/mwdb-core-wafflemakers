import pytest

from threatlib import service
from threatlib.attributes import ATTRIBUTE_KEY
from threatlib.model import Threat, ThreatSample
from threatlib.validation import ValidationError


def test_create_get_and_conflict():
    t = service.create_threat("FIO-1", "threats", readme="# FIO-1", created_by="admin")
    assert t.id is not None and t.flat is False
    assert service.get_threat("FIO-1") is t
    assert service.get_threat("nope") is None
    with pytest.raises(service.NameConflict):
        service.create_threat("FIO-1", "threats")
    with pytest.raises(ValidationError):
        service.create_threat("bad name", "threats")
    with pytest.raises(ValidationError):
        service.create_threat("ok", "not-a-category")


def test_link_applies_mirror_and_is_idempotent(store):
    t = service.create_threat("FIO-1", "threats")
    f = store.add(b"<?php a();")
    link, created = service.link_sample(t, f, "a.php")
    assert created is True and link.object_id == f.id
    assert f.tags == {"threats"} and f.attributes[ATTRIBUTE_KEY] == {"FIO-1"}
    link2, created2 = service.link_sample(t, f, "a.php")
    assert created2 is False and link2 is link
    assert service.sample_count(t) == 1


def test_link_conflict_on_same_path_other_object(store):
    t = service.create_threat("FIO-1", "threats")
    f1 = store.add(b"one")
    f2 = store.add(b"two")
    service.link_sample(t, f1, "a.php")
    with pytest.raises(service.PathConflict):
        service.link_sample(t, f2, "a.php")


def test_link_normalises_rel_path_and_rejects_bad(store):
    t = service.create_threat("FIO-1", "threats")
    f = store.add(b"x")
    link, _ = service.link_sample(t, f, "./sub/./a.php")
    assert link.rel_path == "sub/a.php"
    with pytest.raises(ValidationError):
        service.link_sample(t, f, "../a.php")


def test_link_empty_file_has_null_object():
    t = service.create_threat("FIO-1", "threats")
    link, created = service.link_sample(t, None, "empty.php")
    assert created is True and link.object_id is None


def test_unlink_removes_mirror_only_when_no_links_remain(store):
    t1 = service.create_threat("FIO-1", "threats")
    t2 = service.create_threat("FIO-2", "threats")
    f = store.add(b"x")
    service.link_sample(t1, f, "a.php")
    service.link_sample(t2, f, "b.php")
    assert service.links_for_object(f.id) == [("threats", "FIO-1"), ("threats", "FIO-2")]

    assert service.unlink_sample(t1, "a.php") is True
    assert f.tags == {"threats"} and f.attributes[ATTRIBUTE_KEY] == {"FIO-2"}
    assert service.unlink_sample(t1, "a.php") is False

    assert service.unlink_sample(t2, "b.php") is True
    assert f.tags == set() and f.attributes[ATTRIBUTE_KEY] == set()


def test_set_category_swaps_tag_on_all_samples(store):
    t = service.create_threat("FIO-1", "for-later-review")
    f1 = store.add(b"one")
    f2 = store.add(b"two")
    service.link_sample(t, f1, "a.php")
    service.link_sample(t, f2, "b.php")
    service.set_category(t, "threats")
    assert t.category == "threats"
    assert f1.tags == {"threats"} and f2.tags == {"threats"}
    with pytest.raises(ValidationError):
        service.set_category(t, "nope")


def test_set_category_keeps_old_tag_if_another_threat_uses_it(store):
    t1 = service.create_threat("FIO-1", "for-later-review")
    t2 = service.create_threat("FIO-2", "for-later-review")
    f = store.add(b"x")
    service.link_sample(t1, f, "a.php")
    service.link_sample(t2, f, "a.php")
    service.set_category(t1, "threats")
    assert f.tags == {"threats", "for-later-review"}


def test_set_readme_touches_updated_at():
    t = service.create_threat("FIO-1", "threats")
    before = t.updated_at
    service.set_readme(t, "# new")
    assert t.readme == "# new" and t.updated_at >= before


def test_delete_threat_unlinks_with_mirror_and_keeps_object(store):
    from mwdb.model import db

    t = service.create_threat("FIO-1", "threats")
    f = store.add(b"x")
    service.link_sample(t, f, "a.php")
    service.delete_threat(t)
    assert service.get_threat("FIO-1") is None
    assert db.session.query(ThreatSample).count() == 0
    assert f.tags == set()
    assert store.load(f.id) is f


def test_list_threats_filters_and_counts(store):
    a = service.create_threat("alpha", "threats")
    service.create_threat("alpine", "for-later-review")
    service.create_threat("beta", "threats")
    service.link_sample(a, store.add(b"1"), "1.php")
    service.link_sample(a, store.add(b"2"), "2.php")

    items, total = service.list_threats()
    assert total == 3
    assert [(t.name, n) for t, n in items] == [("alpha", 2), ("alpine", 0), ("beta", 0)]

    items, total = service.list_threats(query="alp")
    assert total == 2 and [t.name for t, _ in items] == ["alpha", "alpine"]

    items, total = service.list_threats(category="for-later-review")
    assert total == 1 and items[0][0].name == "alpine"

    items, total = service.list_threats(page=2, per_page=2)
    assert total == 3 and [t.name for t, _ in items] == ["beta"]


def test_flat_threat_takes_one_sample_named_after_it(store):
    t = service.create_threat("c99", "webshells", flat=True)
    f = store.add(b"<?php c99();")
    for bad in ["sub/c99.php", "other.php", "c99/x.php"]:
        with pytest.raises(ValidationError):
            service.link_sample(t, f, bad)
    service.link_sample(t, f, "c99.php")
    # idempotent re-link is fine, a second link is not
    assert service.link_sample(t, f, "c99.php")[1] is False
    with pytest.raises(ValidationError):
        service.link_sample(t, store.add(b"other"), "c99.txt")
    assert service.sample_count(t) == 1


def test_flat_threat_cannot_change_category(store):
    t = service.create_threat("c99", "webshells", flat=True)
    service.link_sample(t, store.add(b"x"), "c99.php")
    with pytest.raises(ValidationError, match="flat threats cannot change category"):
        service.set_category(t, "threats")
    assert t.category == "webshells"
    service.set_category(t, "webshells")  # no-op is allowed


def test_readme_path_is_not_a_sample_of_a_dir_threat(store):
    t = service.create_threat("FIO-1", "threats")
    f = store.add(b"x")
    for bad in ["README.md", "./README.md"]:
        with pytest.raises(ValidationError):
            service.link_sample(t, f, bad)
    link, _ = service.link_sample(t, f, "sub/README.md")  # deeper is a sample
    assert link.rel_path == "sub/README.md"
    assert t.readme is None
