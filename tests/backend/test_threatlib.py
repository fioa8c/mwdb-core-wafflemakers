"""End-to-end test for the threatlib plugin over HTTP.

Requires the dev compose stack with the `threatlib` plugin enabled.

Run from tests/backend/:
    uv run pytest test_threatlib.py -v
"""

import io
import uuid

import pytest


@pytest.fixture
def name():
    return "e2e-" + uuid.uuid4().hex[:10]


def _skip_if_missing(resp):
    if resp.status_code == 404 and "threat" not in resp.text.lower():
        pytest.skip("threatlib plugin endpoints not registered")


def test_upload_creates_threat_links_and_mirrors(admin_session, name):
    url = admin_session.mwdb_url + "/threatlib/upload"
    content_a = f"<?php echo '{name}-a';".encode()
    content_b = f"<?php echo '{name}-b';".encode()
    resp = admin_session.session.post(
        url,
        data={
            "threat": name,
            "category": "for-later-review",
            "readme": f"# {name}\n",
            "rel_paths": ["a.php", "sub/b.php"],
        },
        files=[
            ("files", ("a.php", io.BytesIO(content_a))),
            ("files", ("b.php", io.BytesIO(content_b))),
        ],
    )
    _skip_if_missing(resp)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["threat"]["category"] == "for-later-review"
    assert [r["status"] for r in body["results"]] == ["new", "new"]
    sha_a = body["results"][0]["sha256"]

    tags = [t["tag"] for t in admin_session.get_tags(sha_a)]
    assert "for-later-review" in tags
    attrs = admin_session.get_attributes(sha_a)["attributes"]
    assert any(a["key"] == "jpop_threat_name" and a["value"] == name for a in attrs)

    threat = admin_session.request("GET", f"/threatlib/threat/{name}")
    assert threat["sample_count"] == 2
    assert sorted(s["rel_path"] for s in threat["samples"]) == ["a.php", "sub/b.php"]

    # promote to threats: tag swaps on the samples
    threat = admin_session.request("PUT", f"/threatlib/threat/{name}", json={"category": "threats"})
    assert threat["category"] == "threats"
    tags = [t["tag"] for t in admin_session.get_tags(sha_a)]
    assert "threats" in tags and "for-later-review" not in tags

    # link an existing sample by hash
    other = admin_session.add_sample(filename="c.php", content=f"<?php '{name}-c';".encode())
    threat = admin_session.request(
        "POST", f"/threatlib/threat/{name}/sample", json={"sha256": other["sha256"], "rel_path": "c.php"}
    )
    assert threat["sample_count"] == 3

    # unlink it
    resp = admin_session.session.delete(
        admin_session.mwdb_url + f"/threatlib/threat/{name}/sample/{other['sha256']}",
        params={"rel_path": "c.php"},
    )
    assert resp.status_code == 200 and resp.json()["sample_count"] == 2
    tags = [t["tag"] for t in admin_session.get_tags(other["sha256"])]
    assert "threats" not in tags

    # list finds it by prefix
    listing = admin_session.request("GET", "/threatlib/threat", params={"query": name[:8]})
    assert any(t["name"] == name for t in listing["threats"])

    # delete: samples remain, mirror stripped
    resp = admin_session.session.delete(admin_session.mwdb_url + f"/threatlib/threat/{name}")
    assert resp.status_code == 200
    assert admin_session.session.get(admin_session.mwdb_url + f"/threatlib/threat/{name}").status_code == 404
    assert admin_session.get_sample(sha_a)["sha256"] == sha_a
    tags = [t["tag"] for t in admin_session.get_tags(sha_a)]
    assert "threats" not in tags
    attrs = admin_session.get_attributes(sha_a)["attributes"]
    assert not any(a["key"] == "jpop_threat_name" and a["value"] == name for a in attrs)


def test_validation_over_http(admin_session, name):
    resp = admin_session.session.post(
        admin_session.mwdb_url + "/threatlib/threat", json={"name": "bad name", "category": "threats"}
    )
    _skip_if_missing(resp)
    assert resp.status_code == 400
    resp = admin_session.session.post(
        admin_session.mwdb_url + "/threatlib/threat", json={"name": name, "category": "nope"}
    )
    assert resp.status_code == 400
    assert admin_session.session.post(
        admin_session.mwdb_url + "/threatlib/threat", json={"name": name, "category": "webshells"}
    ).status_code == 200
    assert admin_session.session.post(
        admin_session.mwdb_url + "/threatlib/threat", json={"name": name, "category": "webshells"}
    ).status_code == 409
    admin_session.session.delete(admin_session.mwdb_url + f"/threatlib/threat/{name}")
