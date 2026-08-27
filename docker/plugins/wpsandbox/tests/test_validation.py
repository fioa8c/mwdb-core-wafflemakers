import pytest

from wpsandbox.validation import ValidationError, normalize_params

SHA = "ab" * 32


def test_webroot_defaults():
    mode, p = normalize_params({"mode": "webroot"}, max_timeout=300, sample_sha256=SHA, sample_name="x.php")
    assert mode == "webroot"
    assert p == {"path": f"wp-content/uploads/{SHA[:8]}.php", "method": "GET",
                 "query": "", "body": "", "timeout": 120}


def test_webroot_explicit_values_normalised():
    _, p = normalize_params(
        {"mode": "webroot", "path": "/wp-content/uploads/../x.php ", "method": "post",
         "query": "a=1", "body": "b", "timeout": 30},
        max_timeout=300, sample_sha256=SHA, sample_name=None)
    assert p["path"] == "wp-content/x.php"  # normalized via posixpath
    assert p["method"] == "POST" and p["timeout"] == 30


def test_plugin_mode_only_keeps_timeout():
    mode, p = normalize_params({"mode": "plugin", "path": "ignored", "timeout": 10},
                               max_timeout=300, sample_sha256=SHA, sample_name="p.zip")
    assert mode == "plugin" and p == {"timeout": 10}


def test_plugin_mode_requires_zip_name():
    with pytest.raises(ValidationError, match="zip"):
        normalize_params({"mode": "plugin"}, max_timeout=300, sample_sha256=SHA, sample_name="p.php")


@pytest.mark.parametrize("body,msg", [
    ({"mode": "nope"}, "mode"),
    ({"mode": "webroot", "method": "PUT"}, "method"),
    ({"mode": "webroot", "timeout": 301}, "timeout"),
    ({"mode": "webroot", "timeout": 0}, "timeout"),
    ({"mode": "webroot", "timeout": "abc"}, "timeout"),
    ({"mode": "webroot", "path": "wp-content/uploads/x.txt"}, "path"),
    ({"mode": "webroot", "path": "....//....//x.php"}, "root"),
    ({"mode": "webroot", "path": "../x.php"}, "root"),
    ({"mode": "webroot", "path": "wp-content/../../x.php"}, "root"),
    ({"mode": "webroot", "path": "wp-content/uploads/a;curl x|sh.php"}, "letters"),
    ({"mode": "webroot", "path": "x\x00.php"}, "letters"),
    ({"mode": "webroot", "path": "a b.php"}, "letters"),
    ({"mode": "webroot", "body": "x" * 65537}, "bytes"),
    ({"mode": "webroot", "query": ["a"]}, "string"),
])
def test_rejects(body, msg):
    with pytest.raises(ValidationError, match=msg):
        normalize_params(body, max_timeout=300, sample_sha256=SHA, sample_name="x.php")


def test_accepts_path_with_allowed_characters_only():
    _, p = normalize_params(
        {"mode": "webroot", "path": "wp-content/uploads/ok_file-1.php"},
        max_timeout=300, sample_sha256=SHA, sample_name="x.php")
    assert p["path"] == "wp-content/uploads/ok_file-1.php"
