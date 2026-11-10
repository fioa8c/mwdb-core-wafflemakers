import json
from types import SimpleNamespace

import pytest
from werkzeug.exceptions import BadRequest

from malleus import upload_guard

disallowed = upload_guard.disallowed_context_keys


def _options(*attributes, field="attributes"):
    return json.dumps({field: [{"key": k, "value": v} for k, v in attributes]})


def test_clean_context_has_no_disallowed_keys():
    assert disallowed(_options(("context", {"scanner": "v2", "path": "a.php"}))) == []


def test_disallowed_keys_are_reported_sorted():
    opts = _options(("context", {"scanner": "v2", "site_id": 1, "host": "h"}))
    assert disallowed(opts) == ["host", "site_id"]


def test_legacy_metakeys_are_checked():
    opts = _options(("context", {"site_id": 1}), field="metakeys")
    assert disallowed(opts) == ["site_id"]


def test_other_attributes_are_ignored():
    assert disallowed(_options(("malleus_verdict", {"site_id": 1}))) == []


@pytest.mark.parametrize("value", ["site_id", ["site_id"], 7, None])
def test_non_object_context_is_left_to_the_hook(value):
    assert disallowed(_options(("context", value))) == []


@pytest.mark.parametrize(
    "options",
    [
        None,
        "",
        "{not json",
        "[]",
        '"text"',
        '{"attributes": "context"}',
        '{"attributes": [1, "x", null]}',
        '{"tags": [{"tag": "source:researcher"}]}',
    ],
)
def test_absent_or_malformed_options_are_tolerated(options):
    assert disallowed(options) == []


class FakeRequest:
    def __init__(
        self, method="POST", endpoint="api.fileresource", form=None, body=None
    ):
        self.method = method
        self.endpoint = endpoint
        self.form = form or {}
        self.is_json = body is not None
        self._body = body

    def get_data(self, as_text=False):
        return self._body


@pytest.fixture
def run(monkeypatch):
    def _run(request, user=SimpleNamespace(login="jetpack-scan")):
        monkeypatch.setattr(upload_guard, "request", request)
        monkeypatch.setattr(upload_guard, "g", SimpleNamespace(auth_user=user))
        return upload_guard.guard_upload()

    return _run


BAD = _options(("context", {"scanner": "v2", "site_id": 1}))
GOOD = _options(("context", {"scanner": "v2"}))


def test_guard_rejects_file_upload_with_disallowed_keys(run):
    with pytest.raises(BadRequest) as exc:
        run(FakeRequest(form={"options": BAD}))
    assert exc.value.description == "context attribute has disallowed keys: site_id"


def test_guard_passes_clean_upload(run):
    assert run(FakeRequest(form={"options": GOOD})) is None


def test_guard_passes_upload_without_options(run):
    assert run(FakeRequest(form={})) is None


@pytest.mark.parametrize("endpoint", ["api.configresource", "api.textblobresource"])
def test_guard_checks_json_body_uploads(run, endpoint):
    with pytest.raises(BadRequest):
        run(FakeRequest(endpoint=endpoint, body=BAD))


@pytest.mark.parametrize("field", ["json", "metakeys"])
def test_guard_checks_legacy_form_fields(run, field):
    payload = BAD if field == "json" else _options(("context", {"x": 1}), field=field)
    with pytest.raises(BadRequest):
        run(FakeRequest(endpoint="api.fileitemresource", form={field: payload}))


@pytest.mark.parametrize(
    "request_",
    [
        FakeRequest(method="GET", form={"options": BAD}),
        FakeRequest(endpoint="api.attributelistresource", body=BAD),
        FakeRequest(endpoint=None, form={"options": BAD}),
    ],
)
def test_guard_ignores_other_requests(run, request_):
    assert run(request_) is None


def test_guard_leaves_unauthenticated_requests_to_the_resource(run):
    assert run(FakeRequest(form={"options": BAD}), user=None) is None


def test_register_attaches_guard_to_api_blueprint(monkeypatch):
    import sys
    from unittest.mock import MagicMock

    api = MagicMock()
    monkeypatch.setattr(sys.modules["mwdb.core.app"], "api", api)
    upload_guard.register()
    api.blueprint.before_request.assert_called_once_with(upload_guard.guard_upload)
