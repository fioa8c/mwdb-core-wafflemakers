"""Flask-RESTful resource for POST /api/yarax/regex."""
from flask import jsonify, request
from werkzeug.exceptions import BadRequest, NotFound, RequestEntityTooLarge

from mwdb.core.service import Resource
from mwdb.model import File
from mwdb.resources import requires_authorization

from . import logger, runner


# Maximum regex length accepted at the endpoint. Real regexes are sub-200
# chars; this is a defensive cap, not a real-world boundary.
MAX_REGEX_LENGTH = 4096

# Maximum sample size we attempt to scan. Web-threat samples are KB-MB; the
# cap is defense against a researcher pointing the panel at a very large
# blob (e.g. a tar.gz). Above this we return a soft sample_too_large.
MAX_SAMPLE_SIZE = 50 * 1024 * 1024  # 50 MB


class YaraXRegexResource(Resource):
    @requires_authorization
    def post(self):
        """
        ---
        summary: Evaluate a regex against a sample using yara-x
        description: |
            Compiles the user's regex into a synthetic single-pattern YARA
            rule, scans the referenced sample, returns matches and any
            compile diagnostics.

            On compile failure the response is HTTP 200 with
            `status: "compile_error"` — the request itself is well-formed,
            only the user's regex is invalid.
        security:
            - bearerAuth: []
        tags:
            - yarax
        requestBody:
            required: true
            content:
                application/json:
                    schema:
                        type: object
                        required: [sample_id, regex]
                        properties:
                            sample_id:
                                type: string
                                description: SHA256/MD5/SHA1/SHA512 of the sample
                            regex:
                                type: string
                                description: User regex pattern (yara-x flavor)
        responses:
            200:
                description: Either matches or compile_error / sample_too_large
            400:
                description: Body malformed or regex empty
            404:
                description: Sample not found or unauthorized
            413:
                description: Regex exceeds length cap
        """
        # Pre-parse guard: reject obviously-too-big bodies before Flask buffers
        # them into memory. The 4 KB regex cap means a legitimate request body
        # is well under 5 KB; allow 16 KB as a defensive ceiling.
        if request.content_length is not None and request.content_length > 16 * 1024:
            raise RequestEntityTooLarge("Request body too large")

        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise BadRequest("Request body must be a JSON object")

        sample_id = body.get("sample_id")
        regex = body.get("regex")

        if not isinstance(sample_id, str) or not sample_id:
            raise BadRequest("sample_id is required and must be a string")
        if not isinstance(regex, str):
            raise BadRequest("regex is required and must be a string")
        if regex == "":
            raise BadRequest("regex must not be empty")
        if len(regex) > MAX_REGEX_LENGTH:
            raise RequestEntityTooLarge(
                f"regex exceeds maximum length of {MAX_REGEX_LENGTH} characters"
            )

        sample = File.access(sample_id)
        if sample is None:
            raise NotFound("Sample not found or you don't have access to it")

        sample_size = getattr(sample, "file_size", None)
        if sample_size is not None and sample_size > MAX_SAMPLE_SIZE:
            return jsonify(
                {
                    "status": "sample_too_large",
                    "diagnostics": [
                        {
                            "severity": "error",
                            "code": "sample_too_large",
                            "message": (
                                f"sample is {sample_size} bytes; the live "
                                f"panel only scans samples up to "
                                f"{MAX_SAMPLE_SIZE} bytes"
                            ),
                        }
                    ],
                }
            )

        sample_bytes = sample.read()
        result = runner.run(regex=regex, sample_bytes=sample_bytes)

        if result.get("status") == "yarax_unavailable":
            # yara-x failed to import in this worker — surface as 503 so the
            # frontend can render an admin-facing banner rather than a regex
            # error. See spec §5.4 / §7.3.
            response = jsonify(result)
            response.status_code = 503
            return response

        logger.info(
            "yarax_regex eval sample=%s regex_len=%d elapsed_ms=%s status=%s",
            sample_id,
            len(regex),
            result.get("elapsed_ms", 0),
            result.get("status", "unknown"),
        )
        return jsonify(result)
