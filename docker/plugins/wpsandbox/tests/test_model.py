from datetime import datetime, timedelta, timezone

import pytest

from wpsandbox import model
from wpsandbox.model import WpSandboxRun, ensure_schema


@pytest.fixture(autouse=True)
def schema():
    assert ensure_schema() is True
    yield
    from mwdb.model import db
    db.session.query(WpSandboxRun).delete()
    db.session.commit()


def test_new_run_defaults():
    r = WpSandboxRun.new(object_id=1, requested_by=2, mode="webroot", params={"timeout": 5})
    assert len(r.id) == 36 and r.status == "queued"
    assert r.params == {"timeout": 5} and r.created_at.tzinfo is not None


def test_to_dict_shape():
    r = WpSandboxRun.new(object_id=1, requested_by=2, mode="plugin", params={"timeout": 5})
    d = r.to_dict()
    assert set(d) == {"id", "object_id", "requested_by", "mode", "params", "status",
                      "created_at", "started_at", "finished_at", "error",
                      "report_blob_id", "sandbox_id"}
    assert d["started_at"] is None and d["created_at"].endswith("+00:00")


def test_worker_lost_derived(monkeypatch):
    monkeypatch.setenv("MWDB_WPSANDBOX_MAX_TIMEOUT", "300")
    r = WpSandboxRun.new(object_id=1, requested_by=2, mode="webroot", params={})
    r.status = "running"
    r.started_at = datetime.now(timezone.utc) - timedelta(seconds=300 + 600 + 1)
    assert r.effective_status() == ("failed", "worker lost")
    assert r.to_dict()["status"] == "failed" and r.to_dict()["error"] == "worker lost"
    r.started_at = datetime.now(timezone.utc) - timedelta(seconds=100)
    assert r.effective_status() == ("running", None)


def test_ensure_schema_idempotent_and_persists():
    from mwdb.model import db
    assert ensure_schema() is True
    r = WpSandboxRun.new(object_id=1, requested_by=2, mode="webroot", params={})
    db.session.add(r)
    db.session.commit()
    assert db.session.get(WpSandboxRun, r.id).mode == "webroot"


def test_ensure_schema_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(WpSandboxRun.__table__, "create", boom)
    assert ensure_schema() is False
