import pytest

from threatlib.validation import (
    ValidationError,
    validate_category,
    validate_name,
    validate_rel_path,
)


@pytest.mark.parametrize("name", ["FIO-7243", "php_uploader_generic_019_2", "sources", "a.b", "_x"])
def test_valid_names(name):
    assert validate_name(name) == name


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a b", "a\\b", "ü", "x" * 256, None])
def test_invalid_names(name):
    with pytest.raises(ValidationError):
        validate_name(name)


@pytest.mark.parametrize(
    "rel_path,expected",
    [
        ("1.php", "1.php"),
        ("0154/wp-admin/menu.php", "0154/wp-admin/menu.php"),
        ("./a/./b.php", "a/b.php"),
        (".hidden", ".hidden"),
        ("dir/README.md", "dir/README.md"),
        ("with space.php", "with space.php"),
    ],
)
def test_valid_rel_paths(rel_path, expected):
    assert validate_rel_path(rel_path) == expected


@pytest.mark.parametrize(
    "rel_path", ["", "/abs.php", "../x.php", "a/../../x.php", "a//b.php", "a/", "x" * 1025, None, "a\\b.php"]
)
def test_invalid_rel_paths(rel_path):
    with pytest.raises(ValidationError):
        validate_rel_path(rel_path)


def test_categories():
    assert validate_category("for-later-review") == "for-later-review"
    with pytest.raises(ValidationError):
        validate_category("false-positives")
