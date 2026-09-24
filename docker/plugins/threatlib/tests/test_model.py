from datetime import timezone

import sqlalchemy as sa

from threatlib import model
from threatlib.model import CATEGORIES, Threat, ThreatSample, ensure_schema, iso, utcnow


def test_categories_are_the_four_repo_dirs():
    assert CATEGORIES == (
        "threats",
        "for-later-review",
        "webshells",
        "escalated_issues_samples",
    )


def test_ensure_schema_is_idempotent():
    assert ensure_schema() is True
    assert ensure_schema() is True


def test_threat_and_link_persist_and_cascade():
    from mwdb.model import db

    now = utcnow()
    t = Threat(name="FIO-1", category="threats", created_at=now, updated_at=now)
    db.session.add(t)
    db.session.flush()
    db.session.add(ThreatSample(threat_id=t.id, rel_path="a.php", object_id=None, added_at=now))
    db.session.add(ThreatSample(threat_id=t.id, rel_path="sub/b.php", object_id=None, added_at=now))
    db.session.commit()

    loaded = db.session.query(Threat).filter(Threat.name == "FIO-1").one()
    assert sorted(s.rel_path for s in loaded.samples) == ["a.php", "sub/b.php"]
    assert loaded.flat is False

    db.session.delete(loaded)
    db.session.commit()
    assert db.session.query(ThreatSample).count() == 0


def test_name_is_unique():
    from mwdb.model import db

    now = utcnow()
    db.session.add(Threat(name="dup", category="threats", created_at=now, updated_at=now))
    db.session.commit()
    db.session.add(Threat(name="dup", category="threats", created_at=now, updated_at=now))
    try:
        db.session.commit()
        assert False, "expected IntegrityError"
    except sa.exc.IntegrityError:
        db.session.rollback()


def test_iso_adds_utc_when_naive():
    dt = utcnow().replace(tzinfo=None)
    assert iso(dt).endswith("+00:00")
    assert iso(None) is None
    assert utcnow().tzinfo == timezone.utc


def test_ensure_schema_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(model.Threat.__table__, "create", boom)
    assert ensure_schema() is False
