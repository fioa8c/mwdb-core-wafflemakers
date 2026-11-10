#!/usr/bin/env python3
"""End-to-end smoke test for the Malleus second-opinion pipeline on the dev stack.

Uploads tests/fixtures/malleus/weak.php tagged source:researcher, then waits for
malleus-static (via Karton and karton-mwdb-reporter) to set the malleus_verdict attribute and
to add the llm-read and deobfuscated-php children.

With --context-test it also checks the `context` contract of the malleus plugin:
- an upload whose `context` attribute carries a disallowed key must be rejected with 400;
- a clean upload that gets such a `context` added afterwards (no Karton spawn then) must end
  up with the key stripped and the tag wm:context-stripped.

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


def connect():
    mwdb = MWDB(api_url=os.environ.get("MWDB_URL", "http://127.0.0.1:8082/api"))
    if os.environ.get("MWDB_API_KEY"):
        mwdb.api.set_api_key(os.environ["MWDB_API_KEY"])
    else:
        mwdb.login(os.environ["MWDB_ADMIN_LOGIN"], os.environ["MWDB_ADMIN_PASSWORD"])
    return mwdb


def wait_for_verdict(mwdb, sha256, timeout):
    deadline = time.time() + timeout
    while True:
        obj = mwdb.query_file(sha256)
        obj.flush()
        attrs = obj.attributes
        if attrs.get("malleus_verdict"):
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
    if args.context_test and not context_test(mwdb, content):
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
