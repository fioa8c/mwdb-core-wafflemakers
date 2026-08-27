"""REST resources for the wpsandbox plugin.

POST   /api/wpsandbox/<hash>        create a run (202) — requires adding_blobs
GET    /api/wpsandbox/<hash>        list runs for a sample
GET    /api/wpsandbox/run/<id>      poll one run
PATCH  /api/wpsandbox/run/<id>      worker-only status update
DELETE /api/wpsandbox/run/<id>      cancel a queued run
"""

from datetime import datetime, timezone

from flask import g, jsonify, request
from werkzeug.exceptions import (
    BadRequest,
    Conflict,
    Forbidden,
    NotFound,
    ServiceUnavailable,
)

import mwdb.model as _mwdb_model
from mwdb.core.capabilities import Capabilities
from mwdb.core.service import Resource
from mwdb.model import File
from mwdb.resources import requires_authorization

from . import attributes, config, logger
from .jobs import get_queue as _get_queue
from .model import WpSandboxRun, ensure_schema
from .validation import ValidationError, normalize_params

# Re-exported names so tests can monkeypatch them on this module.
get_queue = _get_queue

_schema_ready = False


def _db():
    return _mwdb_model.db


def _ensure_schema_once():
    global _schema_ready
    if not _schema_ready:
        _schema_ready = ensure_schema()
        if _schema_ready:
            try:
                attributes.ensure_attribute_definitions()
            except Exception as e:
                logger.warning(
                    "wpsandbox: could not ensure attribute definitions: %s", e
                )
                try:
                    _db().session.rollback()
                except Exception:
                    pass


def _now():
    return datetime.now(timezone.utc)


def _parse_iso(value, field):
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise BadRequest(f"Invalid ISO-8601 timestamp: {field}")


def _run_json(run, now=None) -> dict:
    """Run dict + the parent sample's sha256 (dhash), which the worker needs."""
    d = run.to_dict(now)
    table = _db().Model.metadata.tables["object"]
    row = (
        _db().session.execute(table.select().where(table.c.id == run.object_id)).first()
    )
    d["sample_sha256"] = row.dhash if row is not None else None
    return d


class WpSandboxRunListResource(Resource):
    @requires_authorization
    def get(self, identifier):
        """
        ---
        summary: List WP sandbox runs for a sample
        tags: [wpsandbox]
        security: [{bearerAuth: []}]
        parameters:
          - in: path
            name: identifier
            schema: {type: string}
            required: true
        responses:
          200: {description: "Runs, newest first"}
          404: {description: Sample not found or unauthorized}
        """
        _ensure_schema_once()
        sample = File.access(identifier)
        if sample is None:
            raise NotFound("Sample not found or you don't have access to it")
        runs = (
            _db()
            .session.query(WpSandboxRun)
            .filter(WpSandboxRun.object_id == sample.id)
            .order_by(WpSandboxRun.created_at.desc())
            .all()
        )
        now = _now()
        return jsonify({"runs": [_run_json(r, now) for r in runs]})

    @requires_authorization
    def post(self, identifier):
        """
        ---
        summary: Queue a WP sandbox run for a sample
        description: |
          Body: {mode: webroot|plugin, path?, method?, query?, body?, timeout?}.
          Requires the adding_blobs capability. Returns 202 with run_id,
          409 if an identical run is already queued/running.
        tags: [wpsandbox]
        security: [{bearerAuth: []}]
        parameters:
          - in: path
            name: identifier
            schema: {type: string}
            required: true
        responses:
          202: {description: Run queued}
          400: {description: Invalid parameters or sample too large}
          403: {description: Missing adding_blobs capability}
          404: {description: Sample not found or unauthorized}
          409: {description: Identical run already active}
        """
        _ensure_schema_once()
        if not g.auth_user.has_rights(Capabilities.adding_blobs):
            raise Forbidden("You don't have required capability (adding_blobs)")
        sample = File.access(identifier)
        if sample is None:
            raise NotFound("Sample not found or you don't have access to it")
        if (sample.file_size or 0) > config.max_sample_bytes():
            raise BadRequest(
                f"Sample size exceeds the maximum of {config.max_sample_bytes()} bytes"
            )
        try:
            mode, params = normalize_params(
                request.get_json(silent=True) or {},
                max_timeout=config.max_timeout(),
                sample_sha256=sample.sha256,
                sample_name=sample.file_name,
            )
        except ValidationError as e:
            raise BadRequest(str(e))

        session = _db().session
        active = (
            session.query(WpSandboxRun)
            .filter(
                WpSandboxRun.object_id == sample.id,
                WpSandboxRun.mode == mode,
                WpSandboxRun.status.in_(("queued", "running")),
            )
            .all()
        )
        for run in active:
            if run.params == params and run.effective_status()[0] in (
                "queued",
                "running",
            ):
                response = jsonify(
                    {"run_id": run.id, "message": "An identical run is already active"}
                )
                response.status_code = 409
                return response

        run = WpSandboxRun.new(
            object_id=sample.id, requested_by=g.auth_user.id, mode=mode, params=params
        )
        session.add(run)
        session.commit()
        try:
            get_queue().push(run.id)
        except Exception as e:
            run.status = "failed"
            run.error = f"could not enqueue: {e}"
            run.finished_at = _now()
            session.commit()
            logger.warning(
                "wpsandbox: could not enqueue run=%s sample=%s mode=%s: %s",
                run.id,
                identifier,
                mode,
                e,
            )
            raise ServiceUnavailable("Could not enqueue sandbox run")
        logger.info(
            "wpsandbox run queued run=%s sample=%s mode=%s", run.id, identifier, mode
        )
        response = jsonify({"run_id": run.id})
        response.status_code = 202
        return response


class WpSandboxRunResource(Resource):
    def _load(self, run_id):
        _ensure_schema_once()
        run = _db().session.get(WpSandboxRun, run_id)
        if run is None:
            raise NotFound("Run not found")
        return run

    @requires_authorization
    def get(self, run_id):
        """
        ---
        summary: Get one WP sandbox run
        tags: [wpsandbox]
        security: [{bearerAuth: []}]
        parameters:
          - in: path
            name: run_id
            schema: {type: string}
            required: true
        responses:
          200: {description: Run}
          404: {description: Run not found or sample unauthorized}
        """
        run = self._load(run_id)
        d = _run_json(run)
        if d["sample_sha256"] is None or File.access(d["sample_sha256"]) is None:
            raise NotFound("Run not found")
        return jsonify(d)

    @requires_authorization
    def patch(self, run_id):
        """
        ---
        summary: Update run state (worker only)
        description: |
          Only the configured worker login may call this. Body may contain
          status, started_at, finished_at, error, report_blob_id, sandbox_id.
        tags: [wpsandbox]
        security: [{bearerAuth: []}]
        parameters:
          - in: path
            name: run_id
            schema: {type: string}
            required: true
        responses:
          200: {description: Updated run}
          400: {description: Invalid status}
          403: {description: Not the worker}
          404: {description: Run not found}
        """
        if g.auth_user.login != config.worker_login():
            raise Forbidden("Only the sandbox worker may update runs")
        run = self._load(run_id)
        body = request.get_json(silent=True) or {}
        if "status" in body:
            if body["status"] not in ("queued", "running", "done", "failed", "timeout"):
                raise BadRequest("Invalid status")
            run.status = body["status"]
        for key in ("started_at", "finished_at"):
            if key in body:
                setattr(run, key, _parse_iso(body[key], key))
        if "report_blob_id" in body:
            value = body["report_blob_id"]
            if value is not None and not (isinstance(value, str) and len(value) <= 64):
                raise BadRequest("Invalid report_blob_id")
            run.report_blob_id = value
        if "sandbox_id" in body:
            value = body["sandbox_id"]
            if value is not None and not (isinstance(value, str) and len(value) <= 128):
                raise BadRequest("Invalid sandbox_id")
            run.sandbox_id = value
        if "error" in body:
            value = body["error"]
            if value is not None and not isinstance(value, str):
                raise BadRequest("Invalid error")
            run.error = value[:4096] if isinstance(value, str) else value
        _db().session.commit()
        return jsonify(_run_json(run))

    @requires_authorization
    def delete(self, run_id):
        """
        ---
        summary: Cancel a queued run
        tags: [wpsandbox]
        security: [{bearerAuth: []}]
        parameters:
          - in: path
            name: run_id
            schema: {type: string}
            required: true
        responses:
          200: {description: Cancelled}
          403: {description: Not requester nor admin}
          404: {description: Run not found}
          409: {description: Run is no longer queued}
        """
        run = self._load(run_id)
        user = g.auth_user
        if run.requested_by != user.id and not user.has_rights(
            Capabilities.manage_users
        ):
            raise Forbidden("Only the requester or an admin may cancel a run")
        if run.status != "queued":
            raise Conflict("Only queued runs can be cancelled")
        removed = get_queue().remove(run.id)
        if removed == 0 and run.effective_status()[0] != "failed":
            raise Conflict("Run was already picked up by the worker")
        _db().session.delete(run)
        _db().session.commit()
        return jsonify({"cancelled": run_id})
