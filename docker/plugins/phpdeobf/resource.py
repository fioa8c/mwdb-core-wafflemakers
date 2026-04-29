"""Flask-RESTful resource for POST /api/phpdeobf/<sample_id>."""
import os

from flask import g, jsonify
from werkzeug.exceptions import NotFound, RequestEntityTooLarge, ServiceUnavailable

import mwdb.model as _mwdb_model
from mwdb.core.service import Resource
from mwdb.model import File, db
from mwdb.resources import requires_authorization

from . import logger
from . import client as _client_module

# Re-export under a name the tests can monkeypatch.
client = _client_module

# Maximum sample size — same cap the sidecar enforces. Defense-in-depth.
MAX_SAMPLE_SIZE = 5 * 1024 * 1024  # 5 MB
SIDECAR_TIMEOUT = 30.0
BLOB_TYPE = "deobfuscated-php"


def _sidecar_url() -> str:
    return os.environ.get("MWDB_PHPDEOBF_URL", "http://phpdeobf:8080")


class PhpDeobfResource(Resource):
    @requires_authorization
    def post(self, identifier):
        """
        ---
        summary: Deobfuscate a PHP sample and create a child TextBlob
        description: |
            Sends the sample's bytes to the phpdeobf sidecar; on success
            creates (or reuses) a TextBlob child of this sample with
            blob_type="deobfuscated-php". Sidecar errors are passed
            through at HTTP 200; transport failures map to HTTP 503.
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
        responses:
            200:
                description: ok with blob_id, OR sidecar error pass-through
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
        # Decode with replacement: PHP source is *usually* UTF-8 / ASCII;
        # malformed bytes survive the round-trip as U+FFFD which is acceptable
        # for the v1 scope. Spec §10 documents the upgrade path.
        source = sample_bytes.decode("utf-8", errors="replace")

        result = client.deobfuscate(
            source,
            base_url=_sidecar_url(),
            timeout=SIDECAR_TIMEOUT,
        )

        from .client import OkResult, ErrorResult, UnavailableResult

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

        # Use a stable blob name so the dedupe path actually triggers — same
        # content + same blob_name + same parent → same TextBlob row.
        blob_name = f"deobfuscated-{identifier}.php"
        share_3rd_party = bool(getattr(sample, "share_3rd_party", False))

        # Share the blob with all workspace groups the requesting user belongs
        # to — mirrors how the regular upload resources build share_with. Without
        # this the blob is created but has no ObjectPermission rows, so nobody
        # can access it (add_parent inherits permissions, but only commits them
        # as part of the outer transaction which needs to have been started with
        # at least one give_access first).
        share_with = [grp for grp in g.auth_user.groups if grp.workspace]

        blob, is_new = _mwdb_model.TextBlob.get_or_create(
            result.output,
            blob_name,
            BLOB_TYPE,
            share_3rd_party=share_3rd_party,
            parent=sample,
            share_with=share_with,
        )

        # Flush the session so all pending writes (ObjectPermission rows from
        # share_with + the parent relation from add_parent) are persisted before
        # we return.  The regular ObjectUploader.upload() path does this at
        # resources/object.py:134; plugins must do it themselves.
        db.session.commit()

        logger.info(
            "phpdeobf eval sample=%s elapsed_ms=%d status=ok created=%s",
            identifier,
            result.elapsed_ms,
            is_new,
        )

        return jsonify(
            {
                "status": "ok",
                "blob_id": blob.dhash,
                "created": bool(is_new),
                "elapsed_ms": result.elapsed_ms,
            }
        )
