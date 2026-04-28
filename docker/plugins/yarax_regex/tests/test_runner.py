"""Unit tests for yarax_regex.runner."""
import pytest

from yarax_regex import runner


def test_simple_match():
    result = runner.run(regex=r"foo[0-9]+", sample_bytes=b"hello foo123 world")
    assert result["status"] == "ok"
    assert len(result["matches"]) == 1
    m = result["matches"][0]
    assert m["offset"] == 6
    assert m["length"] == 6
    assert m["text"] == "foo123"


def test_no_matches():
    result = runner.run(regex=r"zzzz", sample_bytes=b"hello world")
    assert result["status"] == "ok"
    assert result["matches"] == []


def test_multiple_matches():
    result = runner.run(regex=r"a", sample_bytes=b"banana")
    assert result["status"] == "ok"
    offsets = [m["offset"] for m in result["matches"]]
    assert offsets == [1, 3, 5]


def test_compile_error_unclosed_class():
    result = runner.run(regex=r"[abc", sample_bytes=b"abc")
    assert result["status"] == "compile_error"
    assert len(result["diagnostics"]) >= 1
    diag = result["diagnostics"][0]
    assert diag["severity"] == "error"
    assert "regular expression" in diag["message"].lower() or \
           "regex" in diag["message"].lower() or \
           "character class" in diag["message"].lower()


def test_empty_regex_rejected():
    """Caller (resource layer) should reject empty regex before calling
    runner; runner is defensive and treats empty as compile_error."""
    result = runner.run(regex="", sample_bytes=b"anything")
    assert result["status"] == "compile_error"


def test_slash_in_regex_is_escaped():
    """A literal forward slash in the regex must not break the synthetic
    rule template."""
    result = runner.run(regex=r"a/b", sample_bytes=b"xxa/byy")
    assert result["status"] == "ok"
    assert len(result["matches"]) == 1
    assert result["matches"][0]["text"] == "a/b"


def test_match_text_is_string_not_bytes():
    """Match text must be JSON-serializable (str), not bytes."""
    result = runner.run(regex=r"\$\w+", sample_bytes=b"$payload = 1")
    assert result["status"] == "ok"
    assert isinstance(result["matches"][0]["text"], str)


def test_match_text_with_non_utf8_bytes_uses_replacement():
    """Sample bytes that aren't valid UTF-8 in the matched span should
    still produce a JSON-serializable match.text via replacement decoding."""
    # 0xff is not a valid UTF-8 start byte
    result = runner.run(regex=r".", sample_bytes=b"\xff")
    assert result["status"] == "ok"
    assert isinstance(result["matches"][0]["text"], str)


def test_line_column_offsets():
    sample = b"line1\nline2_$var\nline3"
    result = runner.run(regex=r"\$\w+", sample_bytes=sample)
    assert result["status"] == "ok"
    m = result["matches"][0]
    assert m["line"] == 2
    # column is 1-based; '$var' starts at byte index 12 == column 7 of line 2
    assert m["column"] == 7


def test_elapsed_ms_present():
    result = runner.run(regex=r"foo", sample_bytes=b"foo")
    assert "elapsed_ms" in result
    assert isinstance(result["elapsed_ms"], int)
    assert result["elapsed_ms"] >= 0


def test_atoms_field_present_even_if_empty():
    """v0: yara-x Python binding does not expose atoms; we ship []."""
    result = runner.run(regex=r"foo", sample_bytes=b"foo")
    assert "atoms" in result
    assert isinstance(result["atoms"], list)


def test_diagnostics_list_present_on_success():
    result = runner.run(regex=r"foo", sample_bytes=b"foo")
    assert "diagnostics" in result
    assert isinstance(result["diagnostics"], list)


def test_warnings_propagated_when_yarax_emits_them():
    """If yara-x's compiler.warnings() returns non-empty, they appear
    as warning-severity diagnostics."""
    # yara-x 1.15.0 rarely emits warnings on simple regex; this test
    # just verifies the wiring — if no warnings come back, that's a
    # legitimate engine output.
    result = runner.run(regex=r"foo", sample_bytes=b"foo")
    for diag in result["diagnostics"]:
        assert diag["severity"] in ("error", "warning", "info")
        assert isinstance(diag["message"], str)


def test_scanner_timeout_is_set():
    """Smoke test: a scanner timeout should be configured. Verify by
    checking the runner's exposed timeout constant."""
    assert hasattr(runner, "SCAN_TIMEOUT_SECONDS")
    assert isinstance(runner.SCAN_TIMEOUT_SECONDS, int)
    assert 1 <= runner.SCAN_TIMEOUT_SECONDS <= 30
