"""Flask-RESTful resources under /api/threatlib/."""

from __future__ import annotations

import posixpath

from flask import g, jsonify, request
from werkzeug.exceptions import BadRequest, Conflict, NotFound

from mwdb.core.capabilities import Capabilities
from mwdb.core.hooks import hooks
from mwdb.core.service import Resource
from mwdb.model import File, db
from mwdb.model.file import EmptyFileError
from mwdb.resources import (
    get_shares_for_upload,
    requires_authorization,
    requires_capabilities,
)

from . import service
from .model import Threat, ThreatSample, iso
from .validation import ValidationError, validate_category, validate_rel_path

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


def _threat_response(threat: Threat) -> dict:
    return threat_dict(threat, service.sample_count(threat), visible_samples(threat))


class ThreatSampleListResource(Resource):
    @requires_authorization
    @requires_capabilities(Capabilities.adding_files)
    def post(self, name):
        """
        ---
        summary: Link an existing sample to a threat
        security:
            - bearerAuth: []
        tags:
            - threatlib
        """
        threat = _get_or_404(name)
        body = _json_body()
        sha256 = body.get("sha256")
        if not isinstance(sha256, str):
            raise BadRequest("'sha256' is required")
        file_obj = File.access(sha256)
        if file_obj is None:
            raise NotFound("Sample not found or you don't have access to it")
        try:
            service.link_sample(threat, file_obj, body.get("rel_path"))
        except ValidationError as e:
            raise BadRequest(e.message)
        except service.PathConflict:
            raise Conflict("rel_path already used by another sample in this threat")
        return jsonify(_threat_response(threat))


class ThreatSampleResource(Resource):
    @requires_authorization
    @requires_capabilities(Capabilities.adding_files)
    def delete(self, name, sha256):
        """
        ---
        summary: Unlink one path of a sample from a threat
        security:
            - bearerAuth: []
        tags:
            - threatlib
        """
        threat = _get_or_404(name)
        rel_path = request.args.get("rel_path")
        if not rel_path:
            raise BadRequest("'rel_path' query parameter is required")
        try:
            rel_path = validate_rel_path(rel_path)
        except ValidationError as e:
            raise BadRequest(e.message)
        link = db.session.get(ThreatSample, (threat.id, rel_path))
        if link is None or link.object_id is None:
            raise NotFound("Link not found")
        file_obj = service._load_file(link.object_id)
        if file_obj is None or file_obj.dhash != sha256.lower():
            raise NotFound("Link not found")
        service.unlink_sample(threat, rel_path)
        return jsonify(_threat_response(threat))


class ThreatUploadResource(Resource):
    @requires_authorization
    @requires_capabilities(Capabilities.adding_files)
    def post(self):
        """
        ---
        summary: Upload one or more files into a threat, creating it if needed
        description: |
            multipart/form-data with fields: threat (name), category + readme
            (only used when the threat does not exist yet), files (repeatable),
            rel_paths (repeatable, parallel to files), upload_as (default "*").
        security:
            - bearerAuth: []
        tags:
            - threatlib
        """
        form = request.form
        name = form.get("threat")
        files = request.files.getlist("files")
        rel_paths = form.getlist("rel_paths")
        if not files:
            raise BadRequest("At least one file is required")
        if len(files) != len(rel_paths):
            raise BadRequest("'rel_paths' must have one entry per file")
        try:
            rel_paths = [validate_rel_path(p) for p in rel_paths]
        except ValidationError as e:
            raise BadRequest(e.message)

        threat = service.get_threat(name) if isinstance(name, str) else None
        if threat is None:
            category = form.get("category")
            if not category:
                raise BadRequest("'category' is required when creating a new threat")
            try:
                threat = service.create_threat(
                    name,
                    category,
                    readme=form.get("readme") or None,
                    created_by=g.auth_user.login,
                )
            except ValidationError as e:
                raise BadRequest(e.message)

        share_with = get_shares_for_upload(form.get("upload_as", "*"))
        results = []
        for storage, rel_path in zip(files, rel_paths):
            try:
                file_obj, is_new = File.get_or_create(
                    file_name=posixpath.basename(rel_path),
                    file_stream=storage.stream,
                    share_3rd_party=False,
                    share_with=share_with,
                )
            except EmptyFileError:
                results.append(
                    {
                        "rel_path": rel_path,
                        "sha256": None,
                        "status": "rejected",
                        "reason": "empty file",
                    }
                )
                continue
            db.session.commit()
            if is_new:
                hooks.on_created_file(file_obj)
                hooks.on_created_object(file_obj)
            else:
                hooks.on_reuploaded_file(file_obj)
                hooks.on_reuploaded_object(file_obj)
            file_obj.release_after_upload()
            try:
                service.link_sample(threat, file_obj, rel_path)
            except service.PathConflict:
                results.append(
                    {
                        "rel_path": rel_path,
                        "sha256": file_obj.dhash,
                        "status": "rejected",
                        "reason": "rel_path already used by another sample in this threat",
                    }
                )
                continue
            results.append(
                {
                    "rel_path": rel_path,
                    "sha256": file_obj.dhash,
                    "status": "new" if is_new else "existing",
                }
            )
        return jsonify({"threat": _threat_response(threat), "results": results})
