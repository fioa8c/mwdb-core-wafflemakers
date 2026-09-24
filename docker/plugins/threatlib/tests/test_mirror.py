from threatlib.attributes import ATTRIBUTE_KEY
from threatlib.mirror import apply_mirror, remove_mirror
from threatlib.tests.fakes import FakeFile


def test_apply_adds_tag_and_attribute_without_commit():
    f = FakeFile(1, b"x", "x.php")
    apply_mirror(f, "threats", "FIO-1")
    assert f.tags == {"threats"}
    assert f.attributes[ATTRIBUTE_KEY] == {"FIO-1"}
    assert ("add_tag", "threats") in f.calls
    assert ("add_attribute", ATTRIBUTE_KEY, "FIO-1") in f.calls


def test_remove_drops_both_when_no_links_remain():
    f = FakeFile(1, b"x", "x.php")
    apply_mirror(f, "threats", "FIO-1")
    remove_mirror(f, "threats", "FIO-1", remaining_links=[])
    assert f.tags == set()
    assert f.attributes[ATTRIBUTE_KEY] == set()


def test_remove_keeps_tag_when_another_link_shares_category():
    f = FakeFile(1, b"x", "x.php")
    apply_mirror(f, "threats", "FIO-1")
    apply_mirror(f, "threats", "FIO-2")
    remove_mirror(f, "threats", "FIO-1", remaining_links=[("threats", "FIO-2")])
    assert f.tags == {"threats"}
    assert f.attributes[ATTRIBUTE_KEY] == {"FIO-2"}


def test_remove_keeps_attribute_when_same_threat_still_linked_by_other_path():
    f = FakeFile(1, b"x", "x.php")
    apply_mirror(f, "threats", "FIO-1")
    remove_mirror(f, "threats", "FIO-1", remaining_links=[("threats", "FIO-1")])
    assert f.tags == {"threats"}
    assert f.attributes[ATTRIBUTE_KEY] == {"FIO-1"}


def test_apply_and_remove_tolerate_none_file():
    apply_mirror(None, "threats", "x")
    remove_mirror(None, "threats", "x", remaining_links=[])
