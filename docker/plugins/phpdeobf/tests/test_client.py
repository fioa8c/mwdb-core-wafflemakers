"""Unit tests for phpdeobf.client — the sidecar HTTP wrapper."""
import responses

from phpdeobf.client import deobfuscate, OkResult, ErrorResult, UnavailableResult


SIDECAR = "http://phpdeobf:8080"


@responses.activate
def test_ok_response_parsed():
    responses.add(
        responses.POST,
        f"{SIDECAR}/deobfuscate",
        json={"status": "ok", "output": "<?php echo 3;", "elapsed_ms": 12},
        status=200,
    )
    result = deobfuscate("<?php echo 1+2;", base_url=SIDECAR)
    assert isinstance(result, OkResult)
    assert result.output == "<?php echo 3;"
    assert result.elapsed_ms == 12


@responses.activate
def test_error_response_parsed():
    responses.add(
        responses.POST,
        f"{SIDECAR}/deobfuscate",
        json={"status": "error", "code": "parse_error", "message": "bad token"},
        status=200,
    )
    result = deobfuscate("not php", base_url=SIDECAR)
    assert isinstance(result, ErrorResult)
    assert result.code == "parse_error"
    assert result.message == "bad token"


@responses.activate
def test_5xx_returns_unavailable():
    responses.add(
        responses.POST, f"{SIDECAR}/deobfuscate", status=502
    )
    result = deobfuscate("<?php echo 1;", base_url=SIDECAR)
    assert isinstance(result, UnavailableResult)


@responses.activate
def test_connection_error_returns_unavailable():
    # No responses.add() → ConnectionError is raised
    result = deobfuscate("<?php echo 1;", base_url=SIDECAR)
    assert isinstance(result, UnavailableResult)


@responses.activate
def test_timeout_returns_unavailable():
    from requests.exceptions import ReadTimeout

    def raise_timeout(request):
        raise ReadTimeout()

    responses.add_callback(
        responses.POST, f"{SIDECAR}/deobfuscate", callback=raise_timeout
    )
    result = deobfuscate("<?php echo 1;", base_url=SIDECAR, timeout=0.001)
    assert isinstance(result, UnavailableResult)


@responses.activate
def test_filename_passed_through():
    captured = {}

    def callback(request):
        import json as _json
        captured.update(_json.loads(request.body))
        return (200, {}, '{"status":"ok","output":"x","elapsed_ms":1}')

    responses.add_callback(
        responses.POST, f"{SIDECAR}/deobfuscate", callback=callback
    )
    deobfuscate("<?php echo 1;", filename="evil.php", base_url=SIDECAR)
    assert captured["filename"] == "evil.php"
    assert captured["source"] == "<?php echo 1;"
