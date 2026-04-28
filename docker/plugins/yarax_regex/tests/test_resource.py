"""Integration tests for yarax_regex.resource using a Flask test client.

The MWDB Flask app is large and slow to construct in tests. Instead, we
build a minimal Flask app with our resource registered and a stubbed
File.access function injected via monkeypatch — same pattern used by
upstream MWDB's lighter resource tests.
"""
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from flask.views import MethodView


@pytest.fixture
def app(monkeypatch):
    """Minimal Flask app with the yarax_regex resource registered.

    Stubs out the @requires_authorization decorator (treats every request
    as authenticated) and the File.access model lookup.
    """
    # Stub requires_authorization to a no-op decorator BEFORE importing
    # the resource module — the decorator is applied at class-body time.
    import functools

    def noop_decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            return f(*args, **kwargs)

        return wrapper

    import mwdb.resources
    monkeypatch.setattr(mwdb.resources, "requires_authorization", noop_decorator)

    # Stub File.access — return a MagicMock with .read() per test
    fake_file_access = MagicMock()
    monkeypatch.setattr("mwdb.model.File.access", fake_file_access)

    # Re-import the resource module so it picks up the patched decorator.
    import importlib
    if "yarax_regex.resource" in list(__import__("sys").modules):
        del __import__("sys").modules["yarax_regex.resource"]
    from yarax_regex.resource import YaraXRegexResource

    flask_app = Flask(__name__)
    flask_app.add_url_rule(
        "/api/yarax/regex",
        view_func=YaraXRegexResource.as_view("yarax_regex"),
    )
    flask_app.fake_file_access = fake_file_access
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _set_sample(app, sample_bytes: bytes):
    fake_file = MagicMock()
    fake_file.read.return_value = sample_bytes
    fake_file.file_size = len(sample_bytes)
    app.fake_file_access.return_value = fake_file


def test_post_with_valid_regex_returns_matches(app, client):
    _set_sample(app, b"hello foo123 world")
    resp = client.post(
        "/api/yarax/regex",
        json={"sample_id": "abc", "regex": r"foo[0-9]+"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert len(body["matches"]) == 1
    assert body["matches"][0]["text"] == "foo123"


def test_post_with_invalid_regex_returns_compile_error_200(app, client):
    _set_sample(app, b"abc")
    resp = client.post(
        "/api/yarax/regex",
        json={"sample_id": "abc", "regex": r"[abc"},
    )
    # compile_error is still 200 — the request was well-formed
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "compile_error"


def test_missing_sample_id_returns_400(client):
    resp = client.post("/api/yarax/regex", json={"regex": r"foo"})
    assert resp.status_code == 400


def test_missing_regex_returns_400(client):
    resp = client.post("/api/yarax/regex", json={"sample_id": "abc"})
    assert resp.status_code == 400


def test_empty_regex_returns_400(client):
    resp = client.post(
        "/api/yarax/regex", json={"sample_id": "abc", "regex": ""}
    )
    assert resp.status_code == 400


def test_regex_over_4kb_returns_413(client):
    huge = "a" * 4097
    resp = client.post(
        "/api/yarax/regex", json={"sample_id": "abc", "regex": huge}
    )
    assert resp.status_code == 413


def test_sample_not_found_returns_404(app, client):
    app.fake_file_access.return_value = None  # File.access returns None
    resp = client.post(
        "/api/yarax/regex", json={"sample_id": "missing", "regex": r"foo"}
    )
    assert resp.status_code == 404


def test_sample_too_large_returns_sample_too_large_status(app, client):
    fake_file = MagicMock()
    fake_file.file_size = 100 * 1024 * 1024  # 100 MB > 50 MB cap
    app.fake_file_access.return_value = fake_file
    resp = client.post(
        "/api/yarax/regex", json={"sample_id": "huge", "regex": r"foo"}
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "sample_too_large"


def test_non_json_body_returns_400(client):
    resp = client.post(
        "/api/yarax/regex",
        data="not json",
        content_type="application/octet-stream",
    )
    assert resp.status_code == 400
