"""Flask-RESTful resources under /api/threatlib/."""

from __future__ import annotations

from flask import g, jsonify, request
from werkzeug.exceptions import BadRequest, Conflict, NotFound

from mwdb.core.capabilities import Capabilities
from mwdb.core.service import Resource
from mwdb.model import db
from mwdb.resources import requires_authorization, requires_capabilities

from . import service
from .model import Threat, iso
from .validation import ValidationError, validate_category

MAX_PER_PAGE = 200


def sample_dict(link, file_obj) -> dict:
    return {
        "sha256": None if file_obj is None else file_obj.dhash,
        "file_name": None if file_obj is None else file_obj.file_name,
        "rel_path": link.rel_path,
        "added_at": iso(link.added_at),
    }


def visible_samples(threat: Threat):
    """Links the current user may see: empty-file links always, object links
    only when the user has explicit access to the object."""
    result = []
    for link in sorted(threat.samples, key=lambda link: link.rel_path):
        if link.object_id is None:
            result.append((link, None))
            continue
        file_obj = service._load_file(link.object_id)
        if file_obj is None or not file_obj.has_explicit_access(g.auth_user):
            continue
        result.append((link, file_obj))
    return result


def threat_dict(threat: Threat, sample_count: int, samples=None) -> dict:
    data = {
        "name": threat.name,
        "category": threat.category,
        "readme": threat.readme,
        "flat": bool(threat.flat),
        "created_by": threat.created_by,
        "created_at": iso(threat.created_at),
        "updated_at": iso(threat.updated_at),
        "sample_count": sample_count,
    }
    if samples is not None:
        data["samples"] = [sample_dict(link, f) for link, f in samples]
    return data


def _json_body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise BadRequest("JSON object body required")
    return body


def _get_or_404(name: str) -> Threat:
    threat = service.get_threat(name)
    if threat is None:
        raise NotFound("Threat not found")
    return threat


class ThreatListResource(Resource):
    @requires_authorization
    def get(self):
        """
        ---
        summary: List threats
        description: Name-prefix search with optional category filter and paging.
        security:
            - bearerAuth: []
        tags:
            - threatlib
        """
        query = request.args.get("query") or None
        category = request.args.get("category") or None
        if category is not None:
            try:
                validate_category(category)
            except ValidationError as e:
                raise BadRequest(e.message)
        try:
            page = max(1, int(request.args.get("page", 1)))
            per_page = min(MAX_PER_PAGE, max(1, int(request.args.get("per_page", 50))))
        except ValueError:
            raise BadRequest("page and per_page must be integers")
        items, total = service.list_threats(
            query=query, category=category, page=page, per_page=per_page
        )
        return jsonify(
            {
                "threats": [threat_dict(t, n) for t, n in items],
                "total": total,
                "page": page,
                "per_page": per_page,
            }
        )

    @requires_authorization
    @requires_capabilities(Capabilities.adding_files)
    def post(self):
        """
        ---
        summary: Create a threat
        security:
            - bearerAuth: []
        tags:
            - threatlib
        """
        body = _json_body()
        try:
            threat = service.create_threat(
                body.get("name"),
                body.get("category"),
                readme=body.get("readme"),
                created_by=g.auth_user.login,
            )
        except ValidationError as e:
            raise BadRequest(e.message)
        except service.NameConflict:
            raise Conflict("A threat with this name already exists")
        return jsonify(threat_dict(threat, 0, samples=[]))


class ThreatResource(Resource):
    @requires_authorization
    def get(self, name):
        """
        ---
        summary: Get a threat with its samples
        security:
            - bearerAuth: []
        tags:
            - threatlib
        """
        threat = _get_or_404(name)
        return jsonify(
            threat_dict(threat, service.sample_count(threat), visible_samples(threat))
        )

    @requires_authorization
    @requires_capabilities(Capabilities.adding_files)
    def put(self, name):
        """
        ---
        summary: Update readme and/or category
        security:
            - bearerAuth: []
        tags:
            - threatlib
        """
        threat = _get_or_404(name)
        body = _json_body()
        if "readme" not in body and "category" not in body:
            raise BadRequest("Provide 'readme' and/or 'category'")
        try:
            if "category" in body:
                service.set_category(threat, body["category"], commit=False)
            if "readme" in body:
                readme = body["readme"]
                if readme is not None and not isinstance(readme, str):
                    raise BadRequest("'readme' must be a string or null")
                service.set_readme(threat, readme, commit=False)
        except ValidationError as e:
            db.session.rollback()
            raise BadRequest(e.message)
        db.session.commit()
        return jsonify(
            threat_dict(threat, service.sample_count(threat), visible_samples(threat))
        )

    @requires_authorization
    @requires_capabilities(Capabilities.removing_objects)
    def delete(self, name):
        """
        ---
        summary: Delete a threat (samples are unlinked, never deleted)
        security:
            - bearerAuth: []
        tags:
            - threatlib
        """
        threat = _get_or_404(name)
        service.delete_threat(threat)
        return jsonify({"deleted": name})
