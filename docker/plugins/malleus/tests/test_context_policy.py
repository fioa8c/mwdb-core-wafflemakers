import pytest

from malleus import context_policy


def test_filter_context_strips_unknown_keys():
    assert context_policy.filter_context({"scanner": "v2", "site_id": 1}) == (
        {"scanner": "v2"},
        ["site_id"],
    )


def test_filter_context_keeps_all_allowed_keys():
    value = {key: "x" for key in context_policy.ALLOWED_CONTEXT_KEYS}
    assert context_policy.filter_context(value) == (value, [])


def test_filter_context_sorts_stripped_keys():
    _, stripped = context_policy.filter_context({"zeta": 1, "alpha": 2, "path": "p"})
    assert stripped == ["alpha", "zeta"]


def test_filter_context_leaves_non_dict():
    assert context_policy.filter_context("x") == ("x", [])


@pytest.mark.parametrize("value", [["scanner"], 3, None, True, 1.5])
def test_filter_context_leaves_other_json_types(value):
    assert context_policy.filter_context(value) == (value, [])


def test_constants_are_exact():
    assert context_policy.ALLOWED_CONTEXT_KEYS == (
        "scanner",
        "scanner_version",
        "rules",
        "path",
        "engine",
    )
    assert context_policy.SCANNER_SOURCES == (
        "jetpack-scan",
        "wpcloud-scan",
        "elderward",
    )


def test_source_tag_rules():
    allowed = context_policy.source_tag_allowed
    # scanner login may only set its own source tag
    assert allowed("source:jetpack-scan", "jetpack-scan")
    assert not allowed("source:wpcloud-scan", "jetpack-scan")
    assert not allowed("source:anything", "elderward")
    # researcher login may set any source tag
    assert allowed("source:wpcloud-scan", "researcher")
    # non-source tags are always allowed
    assert allowed("wm:context-stripped", "jetpack-scan")
    assert allowed("malware:webshell", "elderward")
