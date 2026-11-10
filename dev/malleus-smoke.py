#!/usr/bin/env python3
"""End-to-end smoke test for the Malleus second-opinion pipeline on the dev stack.

Uploads tests/fixtures/malleus/weak.php tagged source:researcher, then waits for
malleus-static (via Karton and karton-mwdb-reporter) to set the malleus_verdict attribute and
to add the llm-read and deobfuscated-php children.

With --expect-sandbox it also waits for the Kadath detonation: a sandbox-report child named
*-kadath-summary.json, a sandbox_level attribute, and a sandbox stage with status "ok" in
malleus_stages (the full-depth source:researcher upload always detonates PHP, so this needs
malleus-detonate running and sandbox_mode = auto or always).

With --reanalyze it then calls obj.reanalyze() (the uploading account needs karton_reanalyze),
waits for a malleus_analysis value different from the first and checks that the plugin's
reanalysis cleanup left exactly one verdict:* tag, exactly one value of malleus_analysis,
malleus_verdict, malleus_confidence and malleus_depth, and wm:needs-review exactly when the new
verdict needs review.

With --context-test it also checks the `context` contract of the malleus plugin:
- an upload whose `context` attribute carries a disallowed key must be rejected with 400;
- a clean upload that gets such a `context` added afterwards (no Karton spawn then) must end
  up with the key stripped and the tag wm:context-stripped.

--reanalyze only works if the Karton reporter logs in as an account named in
MWDB_MALLEUS_REPORTER_LOGINS (default `karton`); the plugin ignores reporters under other names,
so with the dev overlay's reporter (logging in as admin) old and new values pile up.

Env: MWDB_URL (default http://127.0.0.1:8082/api, where compose.with-malleus-dev.yml serves the
SPA/API), and MWDB_API_KEY or MWDB_ADMIN_LOGIN/MWDB_ADMIN_PASSWORD.

Running as a non-admin account: create an API key for it (Settings -> Users -> the user ->
Manage API keys) and run with MWDB_API_KEY=<key> instead of the admin login. The account needs
adding_files and adding_tags, read access to the malleus_* attributes (reading_all_attributes,
or membership of a group in MWDB_MALLEUS_READ_GROUPS) and, for --context-test, set access to
`context` (a group in MWDB_MALLEUS_READ_GROUPS, or adding_all_attributes). Do not use a scanner
login (jetpack-scan, ...): its source:researcher tag is stripped by design. The children
are uploaded by the reporter account; if this account cannot see them, pass --skip-children.
(Not yet run with a non-admin key.)
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path

from mwdblib import MWDB
from mwdblib.exc import MWDBError

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "malleus" / "weak.php"
CONTEXT = {"scanner": "smoke", "site_id": 123}
# Verdicts the reporter tags wm:needs-review (malleus/service/payloads.py REVIEW_VERDICTS).
REVIEW_VERDICTS = ("likely-malicious", "likely-benign", "undetermined")
SINGLE_KEYS = ("malleus_analysis", "malleus_verdict", "malleus_confidence", "malleus_depth")


def connect():
    mwdb = MWDB(api_url=os.environ.get("MWDB_URL", "http://127.0.0.1:8082/api"))
    if os.environ.get("MWDB_API_KEY"):
        mwdb.api.set_api_key(os.environ["MWDB_API_KEY"])
    else:
        mwdb.login(os.environ["MWDB_ADMIN_LOGIN"], os.environ["MWDB_ADMIN_PASSWORD"])
    return mwdb


def wait_for_verdict(mwdb, sha256, timeout, old_analyses=None):
    """Wait for a malleus_verdict. With old_analyses (a reanalysis; must not be empty), also wait until
    a malleus_analysis value that is not in it is there: the previous run's verdict is not the one we want."""
    assert old_analyses is None or old_analyses, "a reanalysis needs the first run's malleus_analysis"
    deadline = time.time() + timeout
    while True:
        obj = mwdb.query_file(sha256)
        obj.flush()
        attrs = obj.attributes
        fresh = [a for a in attrs.get("malleus_analysis", []) if a not in old_analyses]
        if attrs.get("malleus_verdict") and (old_analyses is None or fresh):
            return obj, attrs
        if time.time() >= deadline:
            return obj, None
        print("waiting for malleus_verdict ...", flush=True)
        time.sleep(10)


CHILD_TYPES = ("llm-read", "deobfuscated-php")


def wait_for_children(mwdb, sha256, timeout):
    """The reporter adds the layer and LLM children after the sample's attributes. A verdict
    without them means the child uploads failed (e.g. the reporter account lacks karton_assign)."""
    deadline = time.time() + timeout
    while True:
        obj = mwdb.query_file(sha256)
        children = list(obj.children)
        if all(any(c.type == t for c in children) for t in CHILD_TYPES) or time.time() >= deadline:
            return children
        print("waiting for children %s ..." % ", ".join(CHILD_TYPES), flush=True)
        time.sleep(10)


def stage_status(attrs, name):
    """Status of one stage in the latest malleus_stages value (one dict: deobf, gate, llm, sandbox -> result)."""
    stages = (attrs.get("malleus_stages") or [None])[-1]
    for stage in stages if isinstance(stages, list) else [stages]:
        if isinstance(stage, dict) and isinstance(stage.get(name), dict):
            return stage[name].get("status")
    return None


def wait_for_sandbox(mwdb, sha256, timeout):
    """The sandbox attributes and the kadath summary blob arrive with the verdict of a detonated run.
    Returns a list of failed checks (empty when all hold)."""
    deadline = time.time() + timeout
    while True:
        obj = mwdb.query_file(sha256)
        obj.flush()
        attrs = obj.attributes
        summaries = [c for c in obj.children
                     if c.type == "sandbox-report" and c.name.endswith("-kadath-summary.json")]
        ready = summaries and attrs.get("sandbox_level") and stage_status(attrs, "sandbox")
        if ready or time.time() >= deadline:
            break
        print("waiting for sandbox-report child, sandbox_level and sandbox stage ...", flush=True)
        time.sleep(10)
    failures = []
    if not summaries:
        failures.append("no sandbox-report child named *-kadath-summary.json")
    if not attrs.get("sandbox_level"):
        failures.append("no sandbox_level attribute")
    status = stage_status(attrs, "sandbox")
    if status != "ok":
        failures.append("malleus_stages sandbox status is %r, expected 'ok'" % (status,))
    if not failures:
        print("sandbox: level=%s coverage=%s, report=%s" % (
            attrs["sandbox_level"][-1], (attrs.get("sandbox_coverage") or ["?"])[-1], summaries[0].name))
    return failures


def reanalysis_failures(obj):
    """Checks on a reanalysed object: the plugin must have replaced, not appended to, the old run."""
    attrs = obj.attributes
    failures = []
    for key in SINGLE_KEYS:
        if len(attrs.get(key, [])) != 1:
            failures.append("%s has %d values, expected 1: %r" % (key, len(attrs.get(key, [])), attrs.get(key)))
    verdict_tags = [t for t in obj.tags if t.startswith("verdict:")]
    if len(verdict_tags) != 1:
        failures.append("%d verdict:* tags, expected 1: %s" % (len(verdict_tags), verdict_tags))
    verdicts = attrs.get("malleus_verdict", [])
    if len(verdicts) == 1:
        want_review = verdicts[0] in REVIEW_VERDICTS
        if ("wm:needs-review" in obj.tags) != want_review:
            failures.append("wm:needs-review is %s for verdict %s" % (
                "present" if not want_review else "missing", verdicts[0]))
        if verdict_tags and verdict_tags != ["verdict:" + verdicts[0]]:
            failures.append("tag %s does not match malleus_verdict %s" % (verdict_tags, verdicts[0]))
    return failures


def reanalyze(mwdb, obj, first_attrs, args):
    """Reanalyse `obj`, wait for the new run and return a list of failed checks."""
    # The verdict can be visible before malleus_analysis (separate reporter posts): without the first
    # run's value there is nothing to tell the new run from the old one.
    deadline = time.time() + args.timeout
    old = list(first_attrs.get("malleus_analysis", []))
    while not old:
        if time.time() >= deadline:
            return ["no malleus_analysis on the first run after %ss, cannot tell a reanalysis from it" % args.timeout]
        print("waiting for malleus_analysis of the first run ...", flush=True)
        time.sleep(10)
        fresh_obj = mwdb.query_file(obj.sha256)
        fresh_obj.flush()
        old = list(fresh_obj.attributes.get("malleus_analysis", []))
    try:
        analysis = obj.reanalyze()
    except Exception as e:
        return ["reanalyze: %s" % e]
    print("reanalysis requested: karton analysis %s" % analysis.id)
    obj, attrs = wait_for_verdict(mwdb, obj.sha256, args.timeout, old_analyses=old)
    if attrs is None:
        return ["TIMEOUT: no new malleus_analysis and malleus_verdict for %s after %ss" % (obj.sha256, args.timeout)]
    failures = []
    if args.expect_sandbox:
        failures += wait_for_sandbox(mwdb, obj.sha256, 120)
    # The reporter posts tags, attributes and comments in separate calls: let them settle.
    settle = time.time() + 60
    while True:
        obj = mwdb.query_file(obj.sha256)
        obj.flush()
        found = reanalysis_failures(obj)
        if not found or time.time() >= settle:
            break
        time.sleep(5)
    failures += found
    attrs = obj.attributes
    if attrs.get("malleus_analysis") and attrs["malleus_analysis"][-1] in old:
        failures.append("malleus_analysis did not change")
    if not failures:
        print("malleus_verdict: %s, tags: %s" % (attrs["malleus_verdict"][-1], list(obj.tags)))
        print("reanalysis: one verdict, one value per key")
    return failures


def check_context(obj):
    """Every stored context value must be clean, one must be ours, and the strip must be tagged."""
    stored = obj.attributes.get("context") or []
    ok = bool(stored) and all(isinstance(ctx, dict) and "site_id" not in ctx for ctx in stored)
    ok = ok and any(ctx.get("scanner") == "smoke" for ctx in stored)
    ok = ok and "wm:context-stripped" in obj.tags
    print("context stored: %r, tags: %s -> %s" % (stored, list(obj.tags), "ok" if ok else "FAILED"))
    return ok


def context_test(mwdb, content):
    """Disallowed context keys: rejected at upload, stripped when added afterwards."""
    ok = True
    marker = b"// malleus-smoke context test\n"
    variant = content.replace(b"<?php\n", b"<?php\n" + marker, 1)
    try:
        obj = mwdb.upload_file("weak-context.php", variant, tags=["source:researcher"],
                               attributes={"context": CONTEXT})
        print("FAILED: upload with a disallowed context key was accepted: sha256=%s" % obj.sha256)
        ok = False
    except MWDBError as e:
        status = getattr(getattr(e.http_error, "response", None), "status_code", None)
        print("upload with a disallowed context key -> %s %s" % (status, e))
        if status != 400:
            print("FAILED: expected HTTP 400")
            ok = False

    clean = variant.replace(marker, b"// malleus-smoke context test (added later)\n", 1)
    obj = mwdb.upload_file("weak-context-later.php", clean, tags=["source:researcher"])
    print("uploaded weak-context-later.php sha256=%s, adding context afterwards" % obj.sha256)
    obj.add_attribute("context", CONTEXT)
    obj = mwdb.query_file(obj.sha256)
    return check_context(obj) and ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--timeout", type=int, default=600, help="seconds to wait for the verdict")
    ap.add_argument("--context-test", action="store_true",
                    help="also check that a disallowed context key is rejected at upload and stripped later")
    ap.add_argument("--skip-children", action="store_true",
                    help="do not require the llm-read and deobfuscated-php children (e.g. an account that "
                         "cannot see the reporter's objects, or a Malleus run without the LLM)")
    ap.add_argument("--expect-sandbox", action="store_true",
                    help="also require the Kadath detonation: a *-kadath-summary.json sandbox-report child, a "
                         "sandbox_level attribute and sandbox status ok in malleus_stages")
    ap.add_argument("--reanalyze", action="store_true",
                    help="after the first verdict, reanalyse the sample and check that only the new run's "
                         "verdict, tags and attribute values remain")
    ap.add_argument("--salt", default="",
                    help="append a harmless PHP assignment with this text so the upload and its deobfuscated "
                         "layers hash differently from earlier runs")
    args = ap.parse_args()

    mwdb = connect()
    content = FIXTURE.read_bytes()
    if args.salt:
        # A statement, not a comment: the deobfuscator drops comments, so a comment salt would
        # give a new sample but the same layers, which then already exist and are only re-parented.
        salt = re.sub(r"[^A-Za-z0-9._-]", "_", args.salt)
        content += b"\n$malleus_smoke_salt = '" + salt.encode() + b"';\n"
    failed = False
    obj = mwdb.upload_file("weak.php", content, tags=["source:researcher"])
    print("uploaded weak.php sha256=%s" % obj.sha256)
    obj, attrs = wait_for_verdict(mwdb, obj.sha256, args.timeout)
    if attrs is None:
        print("TIMEOUT: no malleus_verdict for %s after %ss" % (obj.sha256, args.timeout))
        failed = True
    else:
        # Attributes come back ordered by id and a reanalysis appends, so the last one is current.
        print("malleus_verdict: %s" % attrs["malleus_verdict"][-1])
        print("tags: %s" % list(obj.tags))
        children = wait_for_children(mwdb, obj.sha256, 0 if args.skip_children else 120)
        for child in children:
            print("child: %s %s" % (child.type, child.name))
        missing = [t for t in CHILD_TYPES if not any(c.type == t for c in children)]
        if missing and not args.skip_children:
            print("FAILED: no %s child (check the reporter log for 403)" % ", ".join(missing))
            failed = True
        if args.expect_sandbox:
            for failure in wait_for_sandbox(mwdb, obj.sha256, 120):
                print("FAILED: %s" % failure, file=sys.stderr)
                failed = True
        if args.reanalyze and not failed:
            for failure in reanalyze(mwdb, obj, attrs, args):
                print("FAILED: %s" % failure, file=sys.stderr)
                failed = True
    if args.context_test and not context_test(mwdb, content):
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
