"""Flask-RESTful resource for POST /api/phpdeobf/<sample_id>."""

import json
import os

from flask import g, jsonify, request
from werkzeug.exceptions import (
    BadRequest,
    NotFound,
    RequestEntityTooLarge,
    ServiceUnavailable,
)

import mwdb.model as _mwdb_model
from mwdb.core.hooks import hooks
from mwdb.core.service import Resource
from mwdb.model import File
from mwdb.resources import requires_authorization

from . import client as _client_module
from . import logger, presets
from .attributes import ATTRIBUTE_KEY
from .client import ErrorResult, OkResult, UnavailableResult

# Re-export under a name the tests can monkeypatch.
client = _client_module

# Maximum sample size — same cap the sidecar enforces. Defense-in-depth.
MAX_SAMPLE_SIZE = 5 * 1024 * 1024  # 5 MB
SIDECAR_TIMEOUT = 90.0
BLOB_TYPE = "deobfuscated-php"
PEELED_BLOB_TYPE = "deobfuscated-php-evalpeel"
ANALYSIS_BLOB_TYPE = "phpdeobf-analysis"


def _sidecar_url() -> str:
    return os.environ.get("MWDB_PHPDEOBF_URL", "http://phpdeobf:8080")


class PhpDeobfResource(Resource):
    @requires_authorization
    def post(self, identifier):
        """
        ---
        summary: Deobfuscate a PHP sample and create child TextBlobs
        description: |
            Sends the sample's bytes to the phpdeobf sidecar; on success
            creates (or reuses) up to three TextBlob children of this
            sample:

            * `deobfuscated-php` — the deobfuscated output (always).
            * `deobfuscated-php-evalpeel` — the eval()-peeled output, only
              when the sidecar captured one or more layers.
            * `phpdeobf-analysis` — the JSON analysis report, only when
              the sidecar ran an analysis pass.

            Sink categories found by the analysis are recorded on the
            sample as `phpdeobf_sink` attributes.

            The request body is optional JSON controlling which sidecar
            passes run:

                {"preset": "full" | "static" | "custom", "options": {...}}

            `full` (the default) runs every pass, sniffing whether
            `eval_peel` should run from the raw sample bytes. `static`
            disables every pass. `custom` takes an explicit `options` map
            of booleans keyed by option name (unlisted options default to
            `False`).

            Sidecar errors are passed through at HTTP 200; a bad preset
            or options body is HTTP 400; transport failures map to
            HTTP 503.
        security:
            - bearerAuth: []
        tags:
            - phpdeobf
        parameters:
            - in: path
              name: identifier
              schema:
                type: string
              required: true
              description: SHA256/MD5/SHA1/SHA512 of the sample
        requestBody:
            required: false
            content:
                application/json:
                    schema:
                        type: object
                        properties:
                            preset:
                                type: string
                                enum: [full, static, custom]
                            options:
                                type: object
        responses:
            200:
                description: |
                    ok with blob_id/peeled_blob_id/analysis_blob_id (any of
                    the latter two may be null) plus peeled_layers (the
                    number of eval() layers captured, or null when no
                    peeled blob was stored), analysis_summary, warnings,
                    engine and the options sent to the sidecar, OR sidecar
                    error pass-through
            400:
                description: Bad preset or options body
            404:
                description: Sample not found or unauthorized
            413:
                description: Sample exceeds 5 MB cap
            503:
                description: Sidecar unavailable
        """
        sample = File.access(identifier)
        if sample is None:
            raise NotFound("Sample not found or you don't have access to it")

        sample_size = getattr(sample, "file_size", None)
        if sample_size is not None and sample_size > MAX_SAMPLE_SIZE:
            raise RequestEntityTooLarge(
                f"Sample exceeds maximum size of {MAX_SAMPLE_SIZE} bytes"
            )

        sample_bytes = sample.read()
        try:
            options = presets.resolve(request.get_json(silent=True), sample_bytes)
        except ValueError as e:
            raise BadRequest(str(e))
        # Decode with replacement: PHP source is *usually* UTF-8 / ASCII;
        # malformed bytes survive the round-trip as U+FFFD which is acceptable
        # for the v1 scope. Spec §10 documents the upgrade path.
        source = sample_bytes.decode("utf-8", errors="replace")

        result = client.deobfuscate(
            source, base_url=_sidecar_url(), timeout=SIDECAR_TIMEOUT, options=options
        )

        if isinstance(result, UnavailableResult):
            logger.warning("phpdeobf sidecar unavailable: %s", result.detail)
            raise ServiceUnavailable("PHP deobfuscator backend unavailable")

        if isinstance(result, ErrorResult):
            logger.info(
                "phpdeobf eval sample=%s status=error code=%s",
                identifier,
                result.code,
            )
            return jsonify(
                {
                    "status": "error",
                    "code": result.code,
                    "message": result.message,
                }
            )

        assert isinstance(result, OkResult)

        share_3rd_party = bool(getattr(sample, "share_3rd_party", False))

        # Share the blob with all workspace groups the requesting user belongs
        # to — mirrors how the regular upload resources build share_with. Without
        # this the blob is created but has no ObjectPermission rows, so nobody
        # can access it (add_parent inherits permissions, but only commits them
        # as part of the outer transaction which needs to have been started with
        # at least one give_access first).
        share_with = [grp for grp in g.auth_user.groups if grp.workspace]

        def store(content, name, blob_type):
            # Use a stable blob name so the dedupe path actually triggers —
            # same content + same blob_name + same parent → same TextBlob row.
            return _mwdb_model.TextBlob.get_or_create(
                content,
                name,
                blob_type,
                share_3rd_party=share_3rd_party,
                parent=sample,
                share_with=share_with,
            )

        stored = []
        blob, created = store(
            result.output, f"deobfuscated-{identifier}.php", BLOB_TYPE
        )
        stored.append((blob, created))

        peeled_blob = peeled_created = None
        if result.peeled and result.peeled.get("output"):
            peeled_blob, peeled_created = store(
                result.peeled["output"],
                f"deobfuscated-{identifier}.evalpeel.php",
                PEELED_BLOB_TYPE,
            )
            stored.append((peeled_blob, peeled_created))

        analysis_blob = analysis_created = None
        summary = None
        if result.analysis:
            analysis_blob, analysis_created = store(
                json.dumps(result.analysis, indent=1, sort_keys=True),
                f"analysis-{identifier}.json",
                ANALYSIS_BLOB_TYPE,
            )
            stored.append((analysis_blob, analysis_created))
            summary = result.analysis.get("summary") or None
            for category in (summary or {}).get("categories_present", []):
                sample.add_attribute(
                    ATTRIBUTE_KEY, str(category), commit=False, check_permissions=False
                )

        # Flush the session so all pending writes (ObjectPermission rows from
        # share_with + the parent relation from add_parent, plus any
        # attributes) are persisted before we return. The regular
        # ObjectUploader.upload() path does this at resources/object.py:134;
        # plugins must do it themselves.
        # Access via _mwdb_model so test monkeypatches on mwdb.model.db take effect.
        _mwdb_model.db.session.commit()

        # Fire the standard blob hooks so listeners (Karton routing, indexers,
        # etc.) see each new/reused blob — same pattern as TextBlobUploader in
        # mwdb/resources/blob.py.
        for b, is_new in stored:
            if is_new:
                hooks.on_created_text_blob(b)
            else:
                hooks.on_reuploaded_text_blob(b)

        logger.info(
            "phpdeobf eval sample=%s elapsed_ms=%d status=ok blobs=%d",
            identifier,
            result.elapsed_ms,
            len(stored),
        )

        return jsonify(
            {
                "status": "ok",
                "blob_id": blob.dhash,
                "created": bool(created),
                "peeled_blob_id": peeled_blob.dhash if peeled_blob else None,
                "peeled_created": bool(peeled_created) if peeled_blob else None,
                "peeled_layers": (
                    int(result.peeled["layers"]) if peeled_blob else None
                ),
                "analysis_blob_id": analysis_blob.dhash if analysis_blob else None,
                "analysis_created": bool(analysis_created) if analysis_blob else None,
                "analysis_summary": summary,
                "warnings": result.warnings,
                "engine": result.engine,
                "options": options,
                "elapsed_ms": result.elapsed_ms,
            }
        )


class PhpDeobfHealthResource(Resource):
    @requires_authorization
    def get(self):
        """
        ---
        summary: Sidecar health (engine version, eval-hook availability)
        security:
            - bearerAuth: []
        tags:
            - phpdeobf
        """
        h = client.health(base_url=_sidecar_url())
        if isinstance(h, UnavailableResult):
            raise ServiceUnavailable("PHP deobfuscator backend unavailable")
        return jsonify(
            {
                "status": "ok",
                "evalhook_available": h.evalhook_available,
                "phpdeobf": h.phpdeobf,
            }
        )
