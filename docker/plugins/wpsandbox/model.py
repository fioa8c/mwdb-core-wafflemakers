"""wpsandbox_run table. Created idempotently (MWDB has no plugin migrations)."""
import uuid
from datetime import datetime, timezone

from mwdb.model import db

from . import config, logger

STATUSES = ("queued", "running", "done", "failed", "timeout")
TERMINAL = ("done", "failed", "timeout")


def _iso(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class WpSandboxRun(db.Model):
    __tablename__ = "wpsandbox_run"

    id = db.Column(db.String(36), primary_key=True)
    object_id = db.Column(db.Integer, db.ForeignKey("object.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    requested_by = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"))
    mode = db.Column(db.String(16), nullable=False)
    params = db.Column(db.JSON, nullable=False, default=dict)
    status = db.Column(db.String(16), nullable=False, default="queued", index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)
    started_at = db.Column(db.DateTime(timezone=True))
    finished_at = db.Column(db.DateTime(timezone=True))
    error = db.Column(db.Text)
    # dhash (sha256) of the report TextBlob — the worker only knows dhashes over HTTP
    report_blob_id = db.Column(db.String(64))
    sandbox_id = db.Column(db.String(128))

    @classmethod
    def new(cls, object_id: int, requested_by: int | None, mode: str, params: dict):
        return cls(
            id=str(uuid.uuid4()),
            object_id=object_id,
            requested_by=requested_by,
            mode=mode,
            params=params,
            status="queued",
            created_at=datetime.now(timezone.utc),
        )

    def effective_status(self, now=None):
        now = now or datetime.now(timezone.utc)
        if self.status == "running" and self.started_at is not None:
            started = self.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if (now - started).total_seconds() > config.worker_lost_after():
                return "failed", "worker lost"
        return self.status, self.error

    def to_dict(self, now=None) -> dict:
        status, error = self.effective_status(now)
        return {
            "id": self.id,
            "object_id": self.object_id,
            "requested_by": self.requested_by,
            "mode": self.mode,
            "params": self.params,
            "status": status,
            "created_at": _iso(self.created_at),
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "error": error,
            "report_blob_id": self.report_blob_id,
            "sandbox_id": self.sandbox_id,
        }


def ensure_schema() -> bool:
    try:
        WpSandboxRun.__table__.create(bind=db.engine, checkfirst=True)
        return True
    except Exception as e:  # pragma: no cover - exercised via monkeypatch
        logger.warning("wpsandbox: could not create wpsandbox_run table yet: %s", e)
        return False
