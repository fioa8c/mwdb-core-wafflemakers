import io

from mwdb.core.filetype import CHUNK_SIZE, contains_php_tag, refine_file_type

JS_VERDICT = "JavaScript source, Unicode text, UTF-8 text, with very long lines (914)"


def test_tag_at_byte_zero():
    assert contains_php_tag(io.BytesIO(b"<?php echo 1;"))


def test_tag_deep_after_html_filler():
    filler = b"<html><body>" + b"<p>hello</p>\n" * (3 * 1024 * 1024 // 13 + 1)
    assert len(filler) > 3 * 1024 * 1024
    assert contains_php_tag(io.BytesIO(filler + b"<?php echo 1;"))


def test_tag_split_across_chunk_boundary():
    assert CHUNK_SIZE == 1024 * 1024
    data = b"a" * (CHUNK_SIZE - 3) + b"<?p" + b"hp echo 1;"
    assert data[CHUNK_SIZE - 3 : CHUNK_SIZE] == b"<?p"
    assert data[CHUNK_SIZE : CHUNK_SIZE + 2] == b"hp"
    assert contains_php_tag(io.BytesIO(data))


def test_uppercase_tag():
    assert contains_php_tag(io.BytesIO(b"<html><?PHP echo 1; ?></html>"))


def test_short_echo_tag():
    assert contains_php_tag(io.BytesIO(b"<p><?= $x ?></p>"))


def test_no_tag_in_text():
    assert not contains_php_tag(
        io.BytesIO(b"var x = 1;\nconsole.log('<? not php');\n")
    )


def test_binary_without_tag():
    data = bytes(range(256)) * 4096
    assert not contains_php_tag(io.BytesIO(data))


def test_contains_php_tag_rewinds_stream():
    stream = io.BytesIO(b"xxxx<?php echo 1;" + b"y" * 100)
    stream.seek(7)
    assert contains_php_tag(stream)
    assert stream.tell() == 0


def test_contains_php_tag_rewinds_stream_when_no_tag():
    stream = io.BytesIO(b"plain text" * 100)
    stream.seek(7)
    assert not contains_php_tag(stream)
    assert stream.tell() == 0


def test_contains_php_tag_on_real_file(tmp_path):
    path = tmp_path / "sample.bin"
    path.write_bytes(b"<html>" * 1000 + b"<?php echo 1;")
    with open(path, "rb") as fh:
        assert contains_php_tag(fh)
        assert fh.tell() == 0


def test_refine_keeps_existing_php_verdict():
    stream = io.BytesIO(b"<?php echo 1;")
    verdict = "PHP script text, ASCII text"
    assert refine_file_type(stream, verdict) == verdict


def test_refine_replaces_first_segment_and_keeps_rest():
    stream = io.BytesIO(b"<?php echo 1;")
    assert (
        refine_file_type(stream, JS_VERDICT)
        == "PHP script, Unicode text, UTF-8 text, with very long lines (914)"
    )


def test_refine_commaless_verdict_becomes_php_script():
    stream = io.BytesIO(b"<html><?php echo 1; ?></html>")
    assert refine_file_type(stream, "HTML document") == "PHP script"


def test_refine_leaves_verdict_without_tag():
    stream = io.BytesIO(b"var x = 1;\n")
    assert refine_file_type(stream, JS_VERDICT) == JS_VERDICT


def test_refine_stream_position_is_zero_after_call():
    stream = io.BytesIO(b"<?php echo 1;" + b"z" * 50)
    stream.seek(5)
    refine_file_type(stream, JS_VERDICT)
    assert stream.tell() == 0
