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

    if "threatlib.resource" in sys.modules:
        del sys.modules["threatlib.resource"]
    from threatlib import resource as resource_mod

    monkeypatch.setattr(resource_mod, "_load_files", store.load_visible)

    flask_app = Flask(__name__)
    flask_app.add_url_rule(
        "/api/threatlib/threat",
        view_func=resource_mod.ThreatListResource.as_view("threat_list"),
    )
    flask_app.add_url_rule(
        "/api/threatlib/threat/<name>",
        view_func=resource_mod.ThreatResource.as_view("threat"),
    )

    fake_user = MagicMock()
    fake_user.login = "alice"

    @flask_app.before_request
    def _auth():
        from flask import g

        g.auth_user = fake_user

    flask_app.store = store
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def test_create_and_get(client):
    r = client.post(
        "/api/threatlib/threat",
        json={"name": "FIO-1", "category": "threats", "readme": "# FIO-1"},
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["name"] == "FIO-1" and body["created_by"] == "alice"
    assert body["sample_count"] == 0 and body["samples"] == []

    r = client.get("/api/threatlib/threat/FIO-1")
    assert r.status_code == 200 and r.get_json()["readme"] == "# FIO-1"
    assert client.get("/api/threatlib/threat/nope").status_code == 404


def test_create_validation_and_conflict(client):
    assert client.post("/api/threatlib/threat", json={"name": "a b", "category": "threats"}).status_code == 400
    assert client.post("/api/threatlib/threat", json={"name": "x", "category": "zzz"}).status_code == 400
    assert client.post("/api/threatlib/threat", json={"name": "x", "category": "threats"}).status_code == 200
    assert client.post("/api/threatlib/threat", json={"name": "x", "category": "threats"}).status_code == 409


def test_list_with_query_category_and_paging(client, app):
    from threatlib import service

    a = service.create_threat("alpha", "threats")
    service.create_threat("alpine", "for-later-review")
    service.create_threat("beta", "threats")
    service.link_sample(a, app.store.add(b"1"), "1.php")

    r = client.get("/api/threatlib/threat")
    body = r.get_json()
    assert body["total"] == 3 and body["page"] == 1
    assert [t["name"] for t in body["threats"]] == ["alpha", "alpine", "beta"]
    assert body["threats"][0]["sample_count"] == 1
    assert "samples" not in body["threats"][0]

    assert client.get("/api/threatlib/threat?query=alp").get_json()["total"] == 2
    assert client.get("/api/threatlib/threat?category=threats").get_json()["total"] == 2
    r = client.get("/api/threatlib/threat?page=2&per_page=2").get_json()
    assert [t["name"] for t in r["threats"]] == ["beta"]
    assert client.get("/api/threatlib/threat?category=zzz").status_code == 400


def test_get_includes_samples(client, app):
    from threatlib import service

    t = service.create_threat("FIO-1", "threats")
    f = app.store.add(b"<?php", "orig.php")
    service.link_sample(t, f, "sub/a.php")
    service.link_sample(t, None, "empty.php")
    body = client.get("/api/threatlib/threat/FIO-1").get_json()
    assert body["sample_count"] == 2
    assert body["samples"] == [
        {"sha256": None, "file_name": None, "rel_path": "empty.php", "added_at": body["samples"][0]["added_at"]},
        {"sha256": f.sha256, "file_name": "orig.php", "rel_path": "sub/a.php", "added_at": body["samples"][1]["added_at"]},
    ]


def test_put_readme_and_category(client, app):
    from threatlib import service

    t = service.create_threat("FIO-1", "for-later-review")
    f = app.store.add(b"x")
    service.link_sample(t, f, "a.php")

    r = client.put("/api/threatlib/threat/FIO-1", json={"readme": "# edited"})
    assert r.status_code == 200 and r.get_json()["readme"] == "# edited"
    r = client.put("/api/threatlib/threat/FIO-1", json={"category": "threats"})
    assert r.status_code == 200 and r.get_json()["category"] == "threats"
    assert f.tags == {"threats"}
    assert client.put("/api/threatlib/threat/FIO-1", json={"category": "zzz"}).status_code == 400
    assert client.put("/api/threatlib/threat/FIO-1", json={}).status_code == 400
    assert client.put("/api/threatlib/threat/nope", json={"readme": "x"}).status_code == 404


def test_delete(client, app):
    from threatlib import service

    t = service.create_threat("FIO-1", "threats")
    f = app.store.add(b"x")
    service.link_sample(t, f, "a.php")
    assert client.delete("/api/threatlib/threat/FIO-1").status_code == 200
    assert service.get_threat("FIO-1") is None and f.tags == set()
    assert client.delete("/api/threatlib/threat/FIO-1").status_code == 404


def test_get_omits_inaccessible_samples_but_counts_them(client, app):
    from threatlib import service

    t = service.create_threat("FIO-1", "threats")
    visible = app.store.add(b"visible", "v.php")
    hidden = app.store.add(b"hidden", "h.php")
    service.link_sample(t, visible, "v.php")
    service.link_sample(t, hidden, "h.php")
    hidden.accessible = False
    body = client.get("/api/threatlib/threat/FIO-1").get_json()
    assert body["sample_count"] == 2
    assert [s["sha256"] for s in body["samples"]] == [visible.sha256]


def test_put_category_on_flat_threat_is_400(client, app):
    from threatlib import service

    t = service.create_threat("c99", "webshells", flat=True)
    service.link_sample(t, app.store.add(b"x"), "c99.php")
    r = client.put("/api/threatlib/threat/c99", json={"category": "threats"})
    assert r.status_code == 400
    assert service.get_threat("c99").category == "webshells"


def test_create_rejects_dot_git_name(client):
    assert client.post("/api/threatlib/threat", json={"name": ".GIT", "category": "threats"}).status_code == 400
