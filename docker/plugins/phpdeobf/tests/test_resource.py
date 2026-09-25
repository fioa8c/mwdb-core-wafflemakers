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
    # Stub the blob hooks so we can assert which one was called.
    fake_hooks = MagicMock()
    monkeypatch.setattr("mwdb.core.hooks.hooks", fake_hooks)

    fake_client = MagicMock()
    if "phpdeobf.resource" in sys.modules:
        del sys.modules["phpdeobf.resource"]
    from phpdeobf import resource as resource_mod
    monkeypatch.setattr(resource_mod, "client", fake_client)
    monkeypatch.setattr(resource_mod, "hooks", fake_hooks)

    flask_app = Flask(__name__)
    flask_app.add_url_rule(
        "/api/phpdeobf/<identifier>",
        view_func=resource_mod.PhpDeobfResource.as_view("phpdeobf"),
    )
    flask_app.add_url_rule(
        "/api/phpdeobf/health",
        view_func=resource_mod.PhpDeobfHealthResource.as_view("phpdeobf_health"),
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
    flask_app.fake_hooks = fake_hooks
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
    fake_file.dhash = "ab" * 32
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
    # The on_created_text_blob hook fires for new blobs (Karton routing etc.).
    app.fake_hooks.on_created_text_blob.assert_called_once()
    app.fake_hooks.on_reuploaded_text_blob.assert_not_called()


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
    # The on_reuploaded_text_blob hook fires for the dedupe path.
    app.fake_hooks.on_reuploaded_text_blob.assert_called_once()
    app.fake_hooks.on_created_text_blob.assert_not_called()


def _set_sidecar_full(app, categories=("os_exec", "code_exec"), with_peel=True):
    from phpdeobf.client import OkResult
    app.fake_client.deobfuscate.return_value = OkResult(
        output="<?php\n\necho 3;", elapsed_ms=7,
        peeled={"output": "<?php echo 1;", "layers": 2, "secs": 0.5} if with_peel else None,
        analysis={"version": 1, "sources": [], "sinks": [], "errors": [],
                  "summary": {"source_count": 1, "sink_count": 2, "auto_exec_count": 1,
                              "categories_present": list(categories)},
                  "analyzed": "peeled" if with_peel else "static"},
        warnings=[{"code": "eval_peel_no_layers", "message": "n"}] if not with_peel else [],
        engine={"phpdeobf": "abc123", "evalhook_available": True},
        options={"analyze": True},
    )


def _blobs(app):
    """Make get_or_create return a distinct blob per blob_type, all created."""
    def _make(content, blob_name, blob_type, **kw):
        b = MagicMock(); b.dhash = (blob_type + "0" * 64)[:64]; return (b, True)
    app.fake_textblob.get_or_create.side_effect = _make


def test_full_preset_stores_three_blobs_and_attributes(app, client):
    fake_file = _set_sample(app, b"<?php eval($x);")
    _set_sidecar_full(app)
    _blobs(app)
    resp = client.post("/api/phpdeobf/abc123", json={})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["blob_id"].startswith("deobfuscated-php0")
    assert body["peeled_blob_id"].startswith("deobfuscated-php-evalpeel")
    assert body["analysis_blob_id"].startswith("phpdeobf-analysis")
    assert body["analysis_summary"]["categories_present"] == ["os_exec", "code_exec"]
    assert body["engine"]["phpdeobf"] == "abc123"
    # options sent to the sidecar came from the full preset with eval sniffed on
    sent = app.fake_client.deobfuscate.call_args.kwargs["options"]
    assert sent["eval_peel"] is True and sent["analyze"] is True
    # three get_or_create calls with the right names/types
    calls = app.fake_textblob.get_or_create.call_args_list
    names = [(c.args[1], c.args[2]) for c in calls]
    assert ("deobfuscated-abc123.php", "deobfuscated-php") in names
    assert ("deobfuscated-abc123.evalpeel.php", "deobfuscated-php-evalpeel") in names
    assert ("analysis-abc123.json", "phpdeobf-analysis") in names
    # attributes added for each category, permission check off
    added = [c.args[:2] for c in fake_file.add_attribute.call_args_list]
    assert added == [("phpdeobf_sink", "os_exec"), ("phpdeobf_sink", "code_exec")]
    for c in fake_file.add_attribute.call_args_list:
        assert c.kwargs["check_permissions"] is False
    assert app.fake_hooks.on_created_text_blob.call_count == 3


def test_static_preset_sends_all_off_and_stores_only_output(app, client):
    fake_file = _set_sample(app, b"<?php eval($x);")
    _set_sidecar_ok(app, output="<?php\n\necho 3;")
    fake_blob = MagicMock(); fake_blob.dhash = "cafebabe" * 8
    app.fake_textblob.get_or_create.return_value = (fake_blob, True)
    resp = client.post("/api/phpdeobf/abc123", json={"preset": "static"})
    body = resp.get_json()
    assert body["peeled_blob_id"] is None and body["analysis_blob_id"] is None
    assert body["analysis_summary"] is None
    assert all(v is False for v in app.fake_client.deobfuscate.call_args.kwargs["options"].values())
    fake_file.add_attribute.assert_not_called()


def test_bad_preset_body_is_400(app, client):
    _set_sample(app, b"<?php")
    assert client.post("/api/phpdeobf/abc123", json={"preset": "nope"}).status_code == 400
    assert client.post("/api/phpdeobf/abc123", json={"preset": "custom", "options": {"analyze": 1}}).status_code == 400


def test_warnings_pass_through_when_peel_captured_nothing(app, client):
    _set_sample(app, b"<?php eval($x);")
    _set_sidecar_full(app, with_peel=False)
    _blobs(app)
    body = client.post("/api/phpdeobf/abc123", json={}).get_json()
    assert body["peeled_blob_id"] is None
    assert body["warnings"][0]["code"] == "eval_peel_no_layers"
    assert body["analysis_summary"]["categories_present"] == ["os_exec", "code_exec"]


def test_health_proxy(app, client):
    from phpdeobf.client import HealthResult, UnavailableResult
    app.fake_client.health.return_value = HealthResult(evalhook_available=True, phpdeobf="abc123")
    resp = client.get("/api/phpdeobf/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "evalhook_available": True, "phpdeobf": "abc123"}
    app.fake_client.health.return_value = UnavailableResult(detail="down")
    assert client.get("/api/phpdeobf/health").status_code == 503
