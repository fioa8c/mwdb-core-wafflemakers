"""Integration tests for phpdeobf.resource using a Flask test client.

The MWDB Flask app is heavyweight — we build a minimal Flask app with our
resource registered, stubbing File.access, TextBlob.get_or_create, and the
sidecar client. Mirrors yarax_regex's test_resource.py.
"""
import sys
from unittest.mock import MagicMock

import pytest
from flask import Flask


@pytest.fixture
def app(monkeypatch):
    import functools

    def noop_decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            return f(*args, **kwargs)
        return wrapper

    import mwdb.resources
    monkeypatch.setattr(mwdb.resources, "requires_authorization", noop_decorator)

    fake_file_access = MagicMock()
    monkeypatch.setattr("mwdb.model.File.access", fake_file_access)
    fake_textblob = MagicMock()
    monkeypatch.setattr("mwdb.model.TextBlob", fake_textblob)
    # Stub db.session.commit so the resource's explicit commit is a no-op
    # under unit tests (no real DB to commit to).
    fake_db = MagicMock()
    monkeypatch.setattr("mwdb.model.db", fake_db)

    fake_client = MagicMock()
    if "phpdeobf.resource" in sys.modules:
        del sys.modules["phpdeobf.resource"]
    from phpdeobf import resource as resource_mod
    monkeypatch.setattr(resource_mod, "client", fake_client)

    flask_app = Flask(__name__)
    flask_app.add_url_rule(
        "/api/phpdeobf/<identifier>",
        view_func=resource_mod.PhpDeobfResource.as_view("phpdeobf"),
    )

    # Stub g.auth_user with one workspace group so share_with construction
    # in the resource succeeds. Real MWDB sets this in the require_auth
    # before_request hook; here we mimic it with our own.
    fake_workspace_group = MagicMock()
    fake_workspace_group.workspace = True
    fake_non_workspace_group = MagicMock()
    fake_non_workspace_group.workspace = False
    fake_auth_user = MagicMock()
    fake_auth_user.groups = [fake_workspace_group, fake_non_workspace_group]

    @flask_app.before_request
    def _stub_auth_user():
        from flask import g
        g.auth_user = fake_auth_user

    flask_app.fake_file_access = fake_file_access
    flask_app.fake_textblob = fake_textblob
    flask_app.fake_client = fake_client
    flask_app.fake_db = fake_db
    flask_app.fake_workspace_group = fake_workspace_group
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _set_sample(app, sample_bytes: bytes):
    fake_file = MagicMock()
    fake_file.read.return_value = sample_bytes
    fake_file.file_size = len(sample_bytes)
    fake_file.share_3rd_party = False
    app.fake_file_access.return_value = fake_file
    return fake_file


def _set_sidecar_ok(app, output: str, elapsed_ms: int = 5):
    from phpdeobf.client import OkResult
    app.fake_client.deobfuscate.return_value = OkResult(output=output, elapsed_ms=elapsed_ms)


def _set_sidecar_error(app, code: str, message: str):
    from phpdeobf.client import ErrorResult
    app.fake_client.deobfuscate.return_value = ErrorResult(code=code, message=message)


def _set_sidecar_unavailable(app):
    from phpdeobf.client import UnavailableResult
    app.fake_client.deobfuscate.return_value = UnavailableResult(detail="x")


def test_404_when_sample_missing(app, client):
    app.fake_file_access.return_value = None
    resp = client.post("/api/phpdeobf/abc123")
    assert resp.status_code == 404


def test_413_when_sample_too_large(app, client):
    fake_file = _set_sample(app, b"\x00" * 10)
    fake_file.file_size = 10 * 1024 * 1024  # 10 MB > 5 MB cap
    resp = client.post("/api/phpdeobf/abc123")
    assert resp.status_code == 413


def test_503_when_sidecar_unavailable(app, client):
    _set_sample(app, b"<?php echo 1;")
    _set_sidecar_unavailable(app)
    resp = client.post("/api/phpdeobf/abc123")
    assert resp.status_code == 503


def test_sidecar_error_passed_through_at_200(app, client):
    _set_sample(app, b"not really php")
    _set_sidecar_error(app, "parse_error", "syntax error at line 1")
    resp = client.post("/api/phpdeobf/abc123")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "error"
    assert body["code"] == "parse_error"
    assert body["message"] == "syntax error at line 1"


def test_ok_creates_blob_and_returns_id(app, client):
    _set_sample(app, b"<?php echo 1+2;")
    _set_sidecar_ok(app, output="<?php\n\necho 3;", elapsed_ms=7)

    fake_blob = MagicMock()
    fake_blob.dhash = "deadbeef" * 8
    app.fake_textblob.get_or_create.return_value = (fake_blob, True)

    resp = client.post("/api/phpdeobf/abc123")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["blob_id"] == "deadbeef" * 8
    assert body["created"] is True
    assert body["elapsed_ms"] == 7

    # get_or_create called with our content + canonical blob_type
    call_kwargs = app.fake_textblob.get_or_create.call_args.kwargs
    call_args = app.fake_textblob.get_or_create.call_args.args
    # First positional args: (content, blob_name, blob_type, ...)
    assert call_args[0] == "<?php\n\necho 3;"
    assert call_args[2] == "deobfuscated-php"
    # share_with must include only workspace groups (regression test:
    # without share_with the blob has no permission rows and is invisible)
    assert call_kwargs["share_with"] == [app.fake_workspace_group]
    # The session must be committed — the create-and-return path goes
    # through a plugin resource that doesn't get the regular ObjectUploader
    # commit, so the resource has to do it explicitly.
    app.fake_db.session.commit.assert_called_once()


def test_dedupe_returns_existing_blob_with_created_false(app, client):
    _set_sample(app, b"<?php echo 1+2;")
    _set_sidecar_ok(app, output="<?php\n\necho 3;")

    fake_blob = MagicMock()
    fake_blob.dhash = "cafebabe" * 8
    app.fake_textblob.get_or_create.return_value = (fake_blob, False)

    resp = client.post("/api/phpdeobf/abc123")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["blob_id"] == "cafebabe" * 8
    assert body["created"] is False
