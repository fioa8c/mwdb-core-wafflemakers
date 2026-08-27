"""Flask test-client tests for wpsandbox.resource with stubbed MWDB."""
import functools
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import fakeredis
import pytest
from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from wpsandbox.model import WpSandboxRun, ensure_schema
from wpsandbox.queue import JobQueue


@pytest.fixture
def app(monkeypatch):
    def noop(f):
        @functools.wraps(f)
        def w(*a, **k):
            return f(*a, **k)
        return w

    import mwdb.resources
    monkeypatch.setattr(mwdb.resources, "requires_authorization", noop)
    monkeypatch.setenv("MWDB_WPSANDBOX_MAX_TIMEOUT", "300")
    monkeypatch.setenv("MWDB_WPSANDBOX_MAX_SAMPLE_BYTES", "1000")
    monkeypatch.setenv("MWDB_WPSANDBOX_WORKER_LOGIN", "wpsandbox-worker")

    if "wpsandbox.resource" in sys.modules:
        del sys.modules["wpsandbox.resource"]
    from wpsandbox import resource as res

    ensure_schema()
    from mwdb.model import db
    db.session.query(WpSandboxRun).delete(); db.session.commit()
    db.session.execute(db.Model.metadata.tables["object"].delete())
    db.session.execute(db.Model.metadata.tables["object"].insert().values(id=11, dhash="ab" * 32))
    db.session.commit()

    sample = MagicMock()
    sample.id = 11; sample.sha256 = "ab" * 32; sample.file_name = "x.php"; sample.file_size = 100
    fake_access = MagicMock(return_value=sample)
    monkeypatch.setattr(res.File, "access", fake_access)

    fake_redis = fakeredis.FakeRedis()
    monkeypatch.setattr(res, "get_queue", lambda: JobQueue(fake_redis))

    user = MagicMock()
    user.id = 5; user.login = "alice"
    user.has_rights = MagicMock(return_value=True)

    flask_app = Flask(__name__)
    flask_app.add_url_rule("/api/wpsandbox/<identifier>",
                           view_func=res.WpSandboxRunListResource.as_view("runs"))
    flask_app.add_url_rule("/api/wpsandbox/run/<run_id>",
                           view_func=res.WpSandboxRunResource.as_view("run"))

    @flask_app.errorhandler(HTTPException)
    def _json_http_error(e):
        return jsonify({"message": e.description}), e.code

    @flask_app.before_request
    def _auth():
        from flask import g
        g.auth_user = user

    flask_app.sample = sample
    flask_app.user = user
    flask_app.redis = fake_redis
    flask_app.fake_access = fake_access
    flask_app.db = db
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


# --- POST ---------------------------------------------------------------

def test_post_creates_queued_run_and_pushes_job(client, app):
    r = client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot", "timeout": 10})
    assert r.status_code == 202, r.data
    run_id = r.get_json()["run_id"]
    assert app.redis.lrange("wpsandbox:jobs", 0, -1) == [run_id.encode()]
    row = app.db.session.get(WpSandboxRun, run_id)
    assert row.status == "queued" and row.object_id == 11 and row.requested_by == 5
    assert row.params["path"] == "wp-content/uploads/abababab.php"


def test_post_404_when_no_access(client, app):
    app.fake_access.return_value = None
    assert client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot"}).status_code == 404


def test_post_403_without_adding_blobs(client, app):
    app.user.has_rights.return_value = False
    assert client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot"}).status_code == 403


def test_post_400_on_bad_params(client):
    r = client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot", "timeout": 999})
    assert r.status_code == 400 and "timeout" in r.get_json()["message"]


def test_post_400_when_sample_too_large(client, app):
    app.sample.file_size = 1001
    r = client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot"})
    assert r.status_code == 400 and "size" in r.get_json()["message"].lower()


def test_post_409_on_duplicate_active_run(client, app):
    first = client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot"}).get_json()["run_id"]
    r = client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot", "method": "get"})
    assert r.status_code == 409 and r.get_json()["run_id"] == first
    # different params -> new run
    r = client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot", "query": "x=1"})
    assert r.status_code == 202
    assert len(app.redis.lrange("wpsandbox:jobs", 0, -1)) == 2


def test_post_after_terminal_run_is_allowed(client, app):
    run_id = client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot"}).get_json()["run_id"]
    row = app.db.session.get(WpSandboxRun, run_id); row.status = "done"; app.db.session.commit()
    assert client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot"}).status_code == 202


# --- GET list -----------------------------------------------------------

def test_get_lists_runs_newest_first(client, app):
    a = client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot"}).get_json()["run_id"]
    b = client.post("/api/wpsandbox/" + "ab" * 32, json={"mode": "webroot", "query": "q"}).get_json()["run_id"]
    r = client.get("/api/wpsandbox/" + "ab" * 32)
    assert r.status_code == 200
    ids = [x["id"] for x in r.get_json()["runs"]]
    assert ids == [b, a]
    assert all(x["sample_sha256"] == "ab" * 32 for x in r.get_json()["runs"])


def test_get_404_when_no_access(client, app):
    app.fake_access.return_value = None
    assert client.get("/api/wpsandbox/" + "ab" * 32).status_code == 404
