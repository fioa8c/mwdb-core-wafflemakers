#!/usr/bin/env python3
"""End-to-end smoke test for the Malleus second-opinion pipeline on the dev stack.

Uploads tests/fixtures/malleus/weak.php tagged source:researcher, then waits for
malleus-static (via Karton and karton-mwdb-reporter) to set the malleus_verdict attribute.

Env: MWDB_URL (default http://127.0.0.1/api), MWDB_API_KEY or MWDB_ADMIN_LOGIN/MWDB_ADMIN_PASSWORD.
"""
import argparse
import os
import sys
import time
from pathlib import Path

from mwdblib import MWDB

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "malleus" / "weak.php"
CONTEXT = {"scanner": "smoke", "site_id": 123}


def connect():
    mwdb = MWDB(api_url=os.environ.get("MWDB_URL", "http://127.0.0.1/api"))
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


def check_context(obj):
    stored = obj.attributes.get("context") or []
    ctx = stored[0] if stored else None
    ok = isinstance(ctx, dict) and "site_id" not in ctx and ctx.get("scanner") == "smoke"
    ok = ok and "wm:context-stripped" in [t.tag for t in obj.tags]
    print("context stored: %r, tags: %s -> %s" % (ctx, [t.tag for t in obj.tags], "ok" if ok else "FAILED"))
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--timeout", type=int, default=600, help="seconds to wait for the verdict")
    ap.add_argument("--context-test", action="store_true",
                    help="also upload a variant carrying a context attribute with a disallowed key")
    args = ap.parse_args()

    mwdb = connect()
    content = FIXTURE.read_bytes()
    uploads = [("weak.php", content, {})]
    if args.context_test:
        marker = b"// malleus-smoke context test\n"
        variant = content.replace(b"<?php\n", b"<?php\n" + marker, 1)
        uploads.append(("weak-context.php", variant, {"context": CONTEXT}))

    failed = False
    for name, data, attributes in uploads:
        obj = mwdb.upload_file(name, data, tags=["source:researcher"], attributes=attributes or None)
        print("uploaded %s sha256=%s" % (name, obj.sha256))
        obj, attrs = wait_for_verdict(mwdb, obj.sha256, args.timeout)
        if attrs is None:
            print("TIMEOUT: no malleus_verdict for %s after %ss" % (obj.sha256, args.timeout))
            failed = True
            continue
        print("malleus_verdict: %s" % attrs["malleus_verdict"][0])
        print("tags: %s" % [t.tag for t in obj.tags])
        for child in obj.children:
            print("child: %s %s" % (child.type, child.name))
        if attributes and not check_context(obj):
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
