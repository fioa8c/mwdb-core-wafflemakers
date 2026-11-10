"""threatlib_threat / threatlib_threat_sample tables.

Created idempotently at plugin entrypoint (MWDB has no plugin migrations).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from mwdb.model import db

logger = logging.getLogger("mwdb.plugin.threatlib")

CATEGORIES = ("threats", "for-later-review", "webshells", "escalated_issues_samples")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class Threat(db.Model):
    __tablename__ = "threatlib_threat"

    id = db.Column(db.Integer, primary_key=True)
    # Repo folder name (or file stem for flat webshells).
    name = db.Column(db.String(255), unique=True, nullable=False, index=True)
    category = db.Column(db.String(32), nullable=False, index=True)
    readme = db.Column(db.Text, nullable=True)
    # True only for single-file threats living directly under webshells/.
    flat = db.Column(db.Boolean, nullable=False, default=False)
    created_by = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False)

    samples = db.relationship(
        "ThreatSample",
        back_populates="threat",
        cascade="all, delete-orphan",
        lazy="select",
    )


class ThreatSample(db.Model):
    __tablename__ = "threatlib_threat_sample"

    threat_id = db.Column(
        db.Integer,
        db.ForeignKey("threatlib_threat.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Path inside the threat folder, POSIX separators, e.g. "0154/wp-admin/menu.php".
    rel_path = db.Column(db.String(1024), primary_key=True)
    # NULL means "an empty file" (MWDB cannot store zero-byte objects).
    object_id = db.Column(
        db.Integer,
        db.ForeignKey("object.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    added_at = db.Column(db.DateTime(timezone=True), nullable=False)

    threat = db.relationship("Threat", back_populates="samples")


def ensure_schema() -> bool:
    try:
        Threat.__table__.create(bind=db.engine, checkfirst=True)
        ThreatSample.__table__.create(bind=db.engine, checkfirst=True)
        return True
    except Exception as e:  # pragma: no cover - exercised via monkeypatch
        logger.warning("threatlib: could not create tables yet: %s", e)
        return False
