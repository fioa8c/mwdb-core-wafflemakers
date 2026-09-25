"""End-to-end test for PHP file_type refinement over HTTP.

libmagic only looks at the start of a file, so PHP preceded by HTML or
JavaScript used to be stored as "HTML document"/"JavaScript source".

Run from tests/backend/:
    uv run pytest test_filetype.py -v
"""

import uuid


def test_php_after_html_and_js_is_php_script(admin_session):
    marker = uuid.uuid4().hex
    prefix = (
        "<html><head><title>" + marker + "</title>\n"
        "<script>\n"
        "var cfg = {a: 1, b: 2};\n"
        "function run(x) { return document.getElementById(x); }\n"
        "window.onload = function () { run('main'); };\n"
        "</script></head>\n<body><div id=\"main\">\n"
    )
    prefix += "<p>" + "lorem ipsum dolor sit amet " * 3 + "</p>\n"
    prefix = prefix * (1024 // len(prefix) + 1)
    content = (prefix + "<?php echo 1; ?>\n</div></body></html>\n").encode()
    assert content.index(b"<?php") >= 1000

    sample = admin_session.add_sample(filename="page-" + marker, content=content)
    file_type = admin_session.get_sample(sample["sha256"])["file_type"]
    assert file_type.startswith("PHP script"), file_type


def test_plain_js_is_not_php(admin_session):
    marker = uuid.uuid4().hex
    content = (
        "// " + marker + "\n"
        "var cfg = {a: 1, b: 2};\n"
        "function run(x) { return document.getElementById(x); }\n"
        "window.onload = function () { run('main'); };\n"
    ).encode()

    sample = admin_session.add_sample(filename=marker + ".js", content=content)
    file_type = admin_session.get_sample(sample["sha256"])["file_type"]
    assert not file_type.startswith("PHP"), file_type
