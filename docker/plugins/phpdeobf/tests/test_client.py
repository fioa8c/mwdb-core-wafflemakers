"""Unit tests for phpdeobf.client — the sidecar HTTP wrapper."""
import json

import responses

from phpdeobf.client import (
    deobfuscate,
    health,
    OkResult,
    ErrorResult,
    HealthResult,
    UnavailableResult,
)


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


@responses.activate
def test_options_are_sent_and_extended_fields_parsed():
    responses.add(
        responses.POST,
        f"{SIDECAR}/deobfuscate",
        json={
            "status": "ok",
            "output": "<?php echo 3;",
            "elapsed_ms": 12,
            "peeled": {"output": "<?php echo 1;", "layers": 1, "secs": 0.4},
            "analysis": {
                "version": 1,
                "summary": {"categories_present": ["os_exec"]},
                "analyzed": "peeled",
            },
            "warnings": [{"code": "eval_peel_no_layers", "message": "x"}],
            "options": {"analyze": True},
            "engine": {"phpdeobf": "abc123", "evalhook_available": True},
        },
        status=200,
    )
    result = deobfuscate("<?php", base_url=SIDECAR, options={"analyze": True, "eval_peel": True})
    assert isinstance(result, OkResult)
    sent = json.loads(responses.calls[0].request.body)
    assert sent["options"] == {"analyze": True, "eval_peel": True}
    assert result.peeled["layers"] == 1
    assert result.analysis["analyzed"] == "peeled"
    assert result.warnings[0]["code"] == "eval_peel_no_layers"
    assert result.engine["phpdeobf"] == "abc123"
    assert result.options == {"analyze": True}


@responses.activate
def test_missing_extended_fields_default_safely():
    responses.add(
        responses.POST,
        f"{SIDECAR}/deobfuscate",
        json={"status": "ok", "output": "x", "elapsed_ms": 1},
        status=200,
    )
    result = deobfuscate("<?php", base_url=SIDECAR)
    assert result.peeled is None and result.analysis is None
    assert result.warnings == [] and result.engine == {} and result.options == {}
    sent = json.loads(responses.calls[0].request.body)
    assert "options" not in sent


@responses.activate
def test_health_ok_and_unavailable():
    responses.add(
        responses.GET,
        f"{SIDECAR}/health",
        json={"status": "ok", "evalhook_available": True, "phpdeobf": "abc123"},
        status=200,
    )
    h = health(base_url=SIDECAR)
    assert isinstance(h, HealthResult)
    assert h.evalhook_available is True and h.phpdeobf == "abc123"
    responses.reset()
    responses.add(responses.GET, f"{SIDECAR}/health", status=502)
    assert isinstance(health(base_url=SIDECAR), UnavailableResult)
