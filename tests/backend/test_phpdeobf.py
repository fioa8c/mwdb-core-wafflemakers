"""End-to-end test for the phpdeobf plugin.

Requires the dev compose stack with the `phpdeobf` plugin enabled. Skipped
when the plugin endpoint isn't reachable (mirrors how other tests in this
directory degrade).

Run from tests/backend/:
    uv run pytest test_phpdeobf.py -v
"""
from pathlib import Path

import pytest


FIXTURE = Path(__file__).parent / "fixtures" / "phpdeobf_sample.php"


@pytest.mark.skipif(
    not FIXTURE.exists(),
    reason=(
        f"fixture {FIXTURE} missing — "
        f"run `cp ~/WORK/PHPDeobfuscator/samples/e835f.php {FIXTURE}`"
    ),
)
def test_phpdeobf_creates_child_blob_and_dedupes(admin_session):
    """Upload an obfuscated PHP sample, call the plugin endpoint twice,
    assert the first creates a child TextBlob and the second dedupes."""
    sample = admin_session.add_sample(
        filename="phpdeobf_sample.php",
        content=FIXTURE.read_bytes(),
    )
    sample_id = sample["id"]

    # First call — creates a new child blob (or is idempotent if already exists).
    resp = admin_session.session.post(
        admin_session.mwdb_url + f"/phpdeobf/{sample_id}",
    )
    if resp.status_code == 503:
        pytest.skip("phpdeobf sidecar unavailable")
    if resp.status_code == 404 and "not found" not in resp.text.lower():
        pytest.skip("phpdeobf plugin endpoint not registered")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok", body
    # `created` may be True (first ever run for this sample) or False (a
    # prior run left a blob with the same content already attached). Both
    # are valid; the second call's strict dedupe assertion below covers
    # the load-bearing behavior.
    assert "blob_id" in body, body
    blob_id = body["blob_id"]

    # The blob is a child of the sample, with our blob_type.
    sample_full = admin_session.get_sample(sample_id)
    child_ids = [c["id"] for c in sample_full.get("children", [])]
    assert blob_id in child_ids, (
        f"blob {blob_id} not in sample children: {sample_full.get('children')}"
    )
    blob = admin_session.get_blob(blob_id)
    assert blob["blob_type"] == "deobfuscated-php"

    # Second call — must always dedupe (created=False, same blob_id).
    resp = admin_session.session.post(
        admin_session.mwdb_url + f"/phpdeobf/{sample_id}",
    )
    assert resp.status_code == 200
    body2 = resp.json()
    assert body2["status"] == "ok"
    assert body2["created"] is False, (
        f"Expected created=False on second call, got: {body2}"
    )
    assert body2["blob_id"] == blob_id
