import io
import sys
from unittest.mock import MagicMock

import pytest
from flask import Flask

from threatlib.tests.fakes import FakeStore


@pytest.fixture
def app(monkeypatch):
    from threatlib import service

    store = FakeStore()
    monkeypatch.setattr(service, "_load_file", store.load)

    # File.access(sha256) -> FakeFile or None; File.get_or_create -> store
    fake_file_cls = MagicMock()
    fake_file_cls.access.side_effect = lambda ident: store.by_sha.get(ident)

    def _get_or_create(file_name, file_stream, share_3rd_party, share_with=None, **kw):
        return store.get_or_create(file_name, file_stream)

    fake_file_cls.get_or_create.side_effect = _get_or_create
    monkeypatch.setattr("mwdb.model.File", fake_file_cls)
    fake_hooks = MagicMock()
    monkeypatch.setattr("mwdb.core.hooks.hooks", fake_hooks)

    if "threatlib.resource" in sys.modules:
        del sys.modules["threatlib.resource"]
    from threatlib import resource as resource_mod

    monkeypatch.setattr(resource_mod, "File", fake_file_cls)
    monkeypatch.setattr(resource_mod, "hooks", fake_hooks)

    flask_app = Flask(__name__)
    flask_app.add_url_rule(
        "/api/threatlib/threat/<name>/sample",
        view_func=resource_mod.ThreatSampleListResource.as_view("sample_list"),
    )
    flask_app.add_url_rule(
        "/api/threatlib/threat/<name>/sample/<sha256>",
        view_func=resource_mod.ThreatSampleResource.as_view("sample"),
    )
    flask_app.add_url_rule(
        "/api/threatlib/upload",
        view_func=resource_mod.ThreatUploadResource.as_view("upload"),
    )

    fake_user = MagicMock()
    fake_user.login = "alice"

    @flask_app.before_request
    def _auth():
        from flask import g

        g.auth_user = fake_user

    flask_app.store = store
    flask_app.fake_hooks = fake_hooks
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def test_link_existing_sample(client, app):
    from threatlib import service

    service.create_threat("FIO-1", "threats")
    f = app.store.add(b"<?php")
    r = client.post("/api/threatlib/threat/FIO-1/sample", json={"sha256": f.sha256, "rel_path": "a.php"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["sample_count"] == 1 and f.tags == {"threats"}
    # idempotent
    assert client.post("/api/threatlib/threat/FIO-1/sample", json={"sha256": f.sha256, "rel_path": "a.php"}).status_code == 200
    # conflict with another object on the same path
    g = app.store.add(b"other")
    assert client.post("/api/threatlib/threat/FIO-1/sample", json={"sha256": g.sha256, "rel_path": "a.php"}).status_code == 409
    # unknown object / threat / bad path
    assert client.post("/api/threatlib/threat/FIO-1/sample", json={"sha256": "0" * 64, "rel_path": "b.php"}).status_code == 404
    assert client.post("/api/threatlib/threat/nope/sample", json={"sha256": f.sha256, "rel_path": "b.php"}).status_code == 404
    assert client.post("/api/threatlib/threat/FIO-1/sample", json={"sha256": f.sha256, "rel_path": "../b.php"}).status_code == 400


def test_unlink_sample(client, app):
    from threatlib import service

    t = service.create_threat("FIO-1", "threats")
    f = app.store.add(b"<?php")
    service.link_sample(t, f, "sub/a.php")
    r = client.delete(f"/api/threatlib/threat/FIO-1/sample/{f.sha256}?rel_path=sub/a.php")
    assert r.status_code == 200 and r.get_json()["sample_count"] == 0
    assert f.tags == set()
    assert client.delete(f"/api/threatlib/threat/FIO-1/sample/{f.sha256}?rel_path=sub/a.php").status_code == 404
    assert client.delete(f"/api/threatlib/threat/FIO-1/sample/{f.sha256}").status_code == 400


def test_unlink_requires_matching_sha(client, app):
    from threatlib import service

    t = service.create_threat("FIO-1", "threats")
    f = app.store.add(b"<?php")
    service.link_sample(t, f, "a.php")
    assert client.delete(f"/api/threatlib/threat/FIO-1/sample/{'0' * 64}?rel_path=a.php").status_code == 404


def _multipart(threat, files, category=None, readme=None):
    data = {"threat": threat}
    if category:
        data["category"] = category
    if readme is not None:
        data["readme"] = readme
    data["files"] = [(io.BytesIO(content), name) for name, content in files]
    data["rel_paths"] = [name for name, _ in files]
    return data


def test_upload_creates_threat_and_links(client, app):
    data = _multipart(
        "NEW-1",
        [("a.php", b"<?php a();"), ("sub/b.php", b"<?php b();")],
        category="for-later-review",
        readme="# NEW-1",
    )
    r = client.post("/api/threatlib/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["threat"]["name"] == "NEW-1" and body["threat"]["category"] == "for-later-review"
    assert body["threat"]["readme"] == "# NEW-1" and body["threat"]["sample_count"] == 2
    assert [x["status"] for x in body["results"]] == ["new", "new"]
    assert [x["rel_path"] for x in body["results"]] == ["a.php", "sub/b.php"]
    assert app.fake_hooks.on_created_file.call_count == 2
    assert app.fake_hooks.on_created_object.call_count == 2
    for x in body["results"]:
        assert app.store.by_sha[x["sha256"]].tags == {"for-later-review"}


def test_upload_to_existing_threat_ignores_category_and_dedupes(client, app):
    from threatlib import service

    service.create_threat("FIO-1", "threats", readme="# keep")
    data = _multipart("FIO-1", [("a.php", b"same")], category="for-later-review", readme="# ignored")
    r = client.post("/api/threatlib/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 200
    assert r.get_json()["threat"]["category"] == "threats"
    assert r.get_json()["threat"]["readme"] == "# keep"

    data = _multipart("FIO-1", [("copy.php", b"same")])
    r = client.post("/api/threatlib/upload", data=data, content_type="multipart/form-data")
    assert r.get_json()["results"][0]["status"] == "existing"
    assert app.fake_hooks.on_reuploaded_file.call_count == 1
    assert r.get_json()["threat"]["sample_count"] == 2


def test_upload_rejects_path_conflict_and_empty_file(client, app):
    from threatlib import service

    t = service.create_threat("FIO-1", "threats")
    service.link_sample(t, app.store.add(b"first"), "a.php")
    data = _multipart("FIO-1", [("a.php", b"second"), ("empty.php", b"")])
    r = client.post("/api/threatlib/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 200
    results = r.get_json()["results"]
    assert results[0]["status"] == "rejected" and "already" in results[0]["reason"]
    assert results[1]["status"] == "rejected" and "empty" in results[1]["reason"]
    assert r.get_json()["threat"]["sample_count"] == 1


def test_upload_validation(client):
    # new threat without category
    data = _multipart("NEW-2", [("a.php", b"x")])
    assert client.post("/api/threatlib/upload", data=data, content_type="multipart/form-data").status_code == 400
    # mismatched rel_paths
    data = _multipart("NEW-2", [("a.php", b"x")], category="threats")
    data["rel_paths"] = []
    assert client.post("/api/threatlib/upload", data=data, content_type="multipart/form-data").status_code == 400
    # no files
    data = {"threat": "NEW-2", "category": "threats"}
    assert client.post("/api/threatlib/upload", data=data, content_type="multipart/form-data").status_code == 400
    # bad rel_path
    data = _multipart("NEW-2", [("../a.php", b"x")], category="threats")
    assert client.post("/api/threatlib/upload", data=data, content_type="multipart/form-data").status_code == 400
