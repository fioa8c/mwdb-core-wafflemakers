# WP Sandbox — Stage 1: SecEx Template + In-VM Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `wordpress` SecEx template (instrumented WordPress microVM) and the `harness` Python package that runs inside it, snapshots FS/DB state, triggers a sample, and emits `report.json` per the spec schema.

**Architecture:** `docker/wpsandbox-template/` holds (a) `wordpress.py`, an E2B `Template()` definition built with `uv run wordpress.py`, (b) `harness/`, a pure-Python package (stdlib only, so it runs inside the VM with no deps) with one module per concern — manifest diff, DB diff, log parsers, truncation, zip slug, report assembly, CLI — and (c) `files/` with the shell/config assets copied into the image. Everything in `harness/` is unit-tested on the host without a VM; the template build is verified manually against SecEx (Stage 3's live test asserts instrumentation works end-to-end).

**Tech Stack:** Python 3.12 (harness, stdlib only), `e2b` Python SDK + `uv` (template build), Debian `php:8.4-apache`, MariaDB, WP-CLI, evalhook (`snake66/php-eval-hook`, branch `php8x`), mitmproxy, dnsmasq, auditd, iptables.

**Spec:** `docs/superpowers/specs/2026-08-27-wpsandbox-plugin-design.md` (§4 Template and harness, §7 Report schema)

## Global Constraints

- Harness code uses **only the Python standard library** (it runs inside the VM; the image installs `python3` but no pip packages except mitmproxy's own venv).
- Report `schema_version` is `1`. Field names must match spec §7 exactly.
- Size caps (spec §4.2): file content / diff / eval layer / response body ≤ 262144 bytes; request body ≤ 65536 bytes. Every cut appends its JSON path to `truncated[]`.
- Paths in the report are relative to `/var/www/html`.
- Harness writes `/var/log/harness/report.json` incrementally after every phase, then prints the final JSON to stdout.
- Egress blocklist: CIDRs from `--blocklist` are `DROP`ped in the `OUTPUT` chain; TCP 25/465/587 always dropped.
- Nothing in this stage imports `mwdb`.

---

## File map

```
docker/wpsandbox-template/
├── pyproject.toml            # uv project: e2b, python-dotenv (build only) + pytest (dev)
├── .env.example              # E2B_API_KEY, E2B_API_URL, E2B_PROXY
├── README.md                 # how to build/rebuild the template, how to run harness tests
├── wordpress.py              # E2B Template definition + build entrypoint
├── files/
│   ├── setup-wordpress.sh    # installs WP + seeds content (run once at build)
│   ├── setup-instrumentation.sh  # evalhook, mitmproxy, dnsmasq, auditd, php.ini
│   ├── evalhook-prepend.php  # auto_prepend_file that logs eval layers
│   ├── mitm-addon.py         # mitmproxy addon writing flows.jsonl
│   ├── audit.rules           # auditd watch on /var/www/html
│   └── start-services.sh     # boots mariadb, apache, mitmproxy, dnsmasq, auditd at VM start
├── harness/
│   ├── __init__.py
│   ├── truncate.py           # cap() helper + Truncation registry
│   ├── manifest.py           # FS manifest + diff
│   ├── dbdiff.py             # WP-CLI JSON dump diffs
│   ├── logs.py               # eval.jsonl / flows.jsonl / dns.log / audit.log parsers
│   ├── plugin_zip.py         # slug derivation + safe extraction
│   ├── report.py             # Report dataclass + assemble/write
│   ├── shell.py              # thin subprocess wrappers (wp, mysqldump, curl, iptables)
│   └── cli.py                # `python -m harness run ...`
└── tests/
    ├── fixtures/             # small captured logs/dumps used by tests
    ├── test_truncate.py
    ├── test_manifest.py
    ├── test_dbdiff.py
    ├── test_logs.py
    ├── test_plugin_zip.py
    ├── test_report.py
    └── test_cli.py
```

---

### Task 1: Project scaffold + truncation helper

**Files:**
- Create: `docker/wpsandbox-template/pyproject.toml`
- Create: `docker/wpsandbox-template/.env.example`
- Create: `docker/wpsandbox-template/harness/__init__.py`
- Create: `docker/wpsandbox-template/harness/truncate.py`
- Test: `docker/wpsandbox-template/tests/test_truncate.py`

**Interfaces:**
- Produces: `Truncation` class with `.cap(value: str | bytes, limit: int, path: str) -> str` and `.paths: list[str]`; constants `CAP_CONTENT = 262144`, `CAP_REQUEST_BODY = 65536`.

- [ ] **Step 1: Create pyproject and env example**

`docker/wpsandbox-template/pyproject.toml`:
```toml
[project]
name = "wpsandbox-template"
version = "0.1.0"
description = "SecEx WordPress sandbox template + in-VM harness"
requires-python = ">=3.12"
dependencies = [
    "e2b>=1.0",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=7"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`docker/wpsandbox-template/.env.example`:
```bash
E2B_API_KEY=e2b_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
E2B_API_URL=https://sandbox-api-secex.a8c.com
# Optional, e.g. when building from a laptop through Autoproxxy
# E2B_PROXY=socks5://127.0.0.1:8080
```

`docker/wpsandbox-template/harness/__init__.py`:
```python
"""In-VM harness for the WP Sandbox. Stdlib only."""
```

- [ ] **Step 2: Write the failing test**

`docker/wpsandbox-template/tests/test_truncate.py`:
```python
from harness.truncate import CAP_CONTENT, CAP_REQUEST_BODY, Truncation


def test_short_value_untouched():
    t = Truncation()
    assert t.cap("abc", 10, "x.y") == "abc"
    assert t.paths == []


def test_long_value_cut_and_recorded():
    t = Truncation()
    out = t.cap("a" * 20, 10, "filesystem.created[0].content")
    assert out == "a" * 10
    assert t.paths == ["filesystem.created[0].content"]


def test_bytes_decoded_with_replacement():
    t = Truncation()
    assert t.cap(b"ok\xff", 10, "p") == "ok�"


def test_constants():
    assert CAP_CONTENT == 262144
    assert CAP_REQUEST_BODY == 65536
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd docker/wpsandbox-template && uv sync --extra dev && uv run pytest tests/test_truncate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.truncate'`

- [ ] **Step 4: Implement**

`docker/wpsandbox-template/harness/truncate.py`:
```python
"""Size caps for report fields. Every cut is recorded by JSON path."""

CAP_CONTENT = 256 * 1024
CAP_REQUEST_BODY = 64 * 1024


class Truncation:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def cap(self, value: str | bytes, limit: int, path: str) -> str:
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        if len(value) > limit:
            self.paths.append(path)
            return value[:limit]
        return value
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd docker/wpsandbox-template && uv run pytest tests/test_truncate.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add docker/wpsandbox-template
git commit -m "wpsandbox template: scaffold + truncation helper"
```

---

### Task 2: Filesystem manifest + diff

**Files:**
- Create: `docker/wpsandbox-template/harness/manifest.py`
- Test: `docker/wpsandbox-template/tests/test_manifest.py`

**Interfaces:**
- Produces: `build_manifest(root: Path) -> dict[str, Entry]` where `Entry = {"size": int, "mode": str, "sha256": str}` keyed by relative POSIX path; `diff_manifests(pre, post, root, trunc) -> dict` returning the spec's `filesystem` block minus `events` (`created`, `modified`, `deleted`).

- [ ] **Step 1: Write the failing test**

`docker/wpsandbox-template/tests/test_manifest.py`:
```python
import hashlib
from pathlib import Path

from harness.manifest import build_manifest, diff_manifests
from harness.truncate import Truncation


def _write(p: Path, data: bytes, mode=0o644):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    p.chmod(mode)


def test_build_manifest_relative_paths_and_hashes(tmp_path):
    _write(tmp_path / "wp-config.php", b"<?php // cfg")
    _write(tmp_path / "wp-content/uploads/a.txt", b"hello", 0o600)
    m = build_manifest(tmp_path)
    assert set(m) == {"wp-config.php", "wp-content/uploads/a.txt"}
    assert m["wp-content/uploads/a.txt"] == {
        "size": 5,
        "mode": "0600",
        "sha256": hashlib.sha256(b"hello").hexdigest(),
    }


def test_diff_created_modified_deleted(tmp_path):
    _write(tmp_path / "keep.php", b"same")
    _write(tmp_path / "mod.php", b"line1\nline2\n")
    _write(tmp_path / "gone.php", b"bye")
    pre = build_manifest(tmp_path)

    (tmp_path / "gone.php").unlink()
    _write(tmp_path / "mod.php", b"line1\nCHANGED\n")
    _write(tmp_path / "wp-content/uploads/.cache/s.php", b"<?php eval($_POST[1]);")
    post = build_manifest(tmp_path)

    t = Truncation()
    d = diff_manifests(pre, post, tmp_path, t)

    assert [c["path"] for c in d["created"]] == ["wp-content/uploads/.cache/s.php"]
    created = d["created"][0]
    assert created["content"] == "<?php eval($_POST[1]);"
    assert created["size"] == 22 and created["mode"] == "0644"
    assert created["sha256"] == hashlib.sha256(b"<?php eval($_POST[1]);").hexdigest()

    assert [m["path"] for m in d["modified"]] == ["mod.php"]
    mod = d["modified"][0]
    assert mod["sha256_before"] == pre["mod.php"]["sha256"]
    assert mod["sha256_after"] == post["mod.php"]["sha256"]
    assert "-line2" in mod["diff"] and "+CHANGED" in mod["diff"]

    assert d["deleted"] == [{"path": "gone.php"}]
    assert t.paths == []


def test_diff_large_created_file_content_is_null(tmp_path):
    pre = build_manifest(tmp_path)
    _write(tmp_path / "big.bin", b"x" * (256 * 1024 + 1))
    post = build_manifest(tmp_path)
    d = diff_manifests(pre, post, tmp_path, Truncation())
    assert d["created"][0]["content"] is None


def test_diff_binary_modified_file_has_no_diff(tmp_path):
    _write(tmp_path / "img.png", b"\x89PNG\x00\x01")
    pre = build_manifest(tmp_path)
    _write(tmp_path / "img.png", b"\x89PNG\x00\x02")
    post = build_manifest(tmp_path)
    d = diff_manifests(pre, post, tmp_path, Truncation())
    assert d["modified"][0]["diff"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd docker/wpsandbox-template && uv run pytest tests/test_manifest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.manifest'`

- [ ] **Step 3: Implement**

`docker/wpsandbox-template/harness/manifest.py`:
```python
"""Filesystem manifest (path -> size/mode/sha256) and pre/post diff."""
import difflib
import hashlib
import os
from pathlib import Path

from .truncate import CAP_CONTENT, Truncation

Entry = dict  # {"size": int, "mode": str, "sha256": str}


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(root: Path) -> dict[str, Entry]:
    out: dict[str, Entry] = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = Path(dirpath) / name
            if p.is_symlink() or not p.is_file():
                continue
            st = p.stat()
            out[p.relative_to(root).as_posix()] = {
                "size": st.st_size,
                "mode": f"{st.st_mode & 0o7777:04o}",
                "sha256": _sha256(p),
            }
    return out


def _is_text(data: bytes) -> bool:
    return b"\x00" not in data[:8192]


def _read_text(p: Path) -> str | None:
    data = p.read_bytes()
    if not _is_text(data):
        return None
    return data.decode("utf-8", errors="replace")


def diff_manifests(
    pre: dict[str, Entry], post: dict[str, Entry], root: Path, trunc: Truncation
) -> dict:
    created, modified, deleted = [], [], []

    for path in sorted(set(post) - set(pre)):
        entry = post[path]
        content = None
        if entry["size"] <= CAP_CONTENT:
            content = _read_text(root / path)
        created.append({"path": path, **entry, "content": content})

    for path in sorted(set(pre) & set(post)):
        if pre[path]["sha256"] == post[path]["sha256"]:
            continue
        before_text = None
        after_text = _read_text(root / path)
        diff = None
        # We only have the post-state on disk; the caller stores pre-content
        # for text files in `pre_texts` (see cli.py) to enable diffs.
        pre_text = pre[path].get("text")
        if pre_text is not None and after_text is not None:
            diff = "".join(
                difflib.unified_diff(
                    pre_text.splitlines(keepends=True),
                    after_text.splitlines(keepends=True),
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                )
            )
            diff = trunc.cap(diff, CAP_CONTENT, f"filesystem.modified[{len(modified)}].diff")
        modified.append(
            {
                "path": path,
                "sha256_before": pre[path]["sha256"],
                "sha256_after": post[path]["sha256"],
                "diff": diff,
            }
        )

    for path in sorted(set(pre) - set(post)):
        deleted.append({"path": path})

    return {"created": created, "modified": modified, "deleted": deleted}


def attach_pre_texts(root: Path, manifest: dict[str, Entry]) -> None:
    """Store text content of small text files in the pre manifest so
    diff_manifests can produce unified diffs for modified files."""
    for path, entry in manifest.items():
        if entry["size"] <= CAP_CONTENT:
            text = _read_text(root / path)
            if text is not None:
                entry["text"] = text
```

Then update the modified-file test: `pre` must carry texts. Change `test_diff_created_modified_deleted` and `test_diff_binary_modified_file_has_no_diff` to call `attach_pre_texts(tmp_path, pre)` right after `pre = build_manifest(tmp_path)`, and add the import `from harness.manifest import attach_pre_texts, build_manifest, diff_manifests`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd docker/wpsandbox-template && uv run pytest tests/test_manifest.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add docker/wpsandbox-template/harness/manifest.py docker/wpsandbox-template/tests/test_manifest.py
git commit -m "wpsandbox harness: filesystem manifest + diff"
```

---

### Task 3: WordPress DB state diff

**Files:**
- Create: `docker/wpsandbox-template/harness/dbdiff.py`
- Create: `docker/wpsandbox-template/tests/fixtures/db_pre.json`, `docker/wpsandbox-template/tests/fixtures/db_post.json`
- Test: `docker/wpsandbox-template/tests/test_dbdiff.py`

**Interfaces:**
- Consumes: JSON produced by `wp user list --format=json --fields=ID,user_login,user_email,roles`, `wp option list --format=json --fields=option_name,option_value`, `wp cron event list --format=json --fields=hook,next_run_gmt,schedule`, `wp post list --post_type=any --post_status=any --format=json --fields=ID,post_title,post_status,post_type,post_modified`, plus `mysqldump --skip-dump-date --skip-comments` output.
- Produces: `DbState` dict `{"users": [...], "options": [...], "cron": [...], "posts": [...], "dump": str}` and `diff_db(pre: DbState, post: DbState) -> dict` returning the spec's `database` block.

- [ ] **Step 1: Create fixtures**

`docker/wpsandbox-template/tests/fixtures/db_pre.json`:
```json
{
  "users": [{"ID": 1, "user_login": "admin", "user_email": "admin@example.test", "roles": "administrator"}],
  "options": [{"option_name": "siteurl", "option_value": "http://localhost"},
              {"option_name": "blogname", "option_value": "Sandbox"}],
  "cron": [{"hook": "wp_version_check", "next_run_gmt": "2026-08-27 00:00:00", "schedule": "twicedaily"}],
  "posts": [{"ID": 1, "post_title": "Hello world!", "post_status": "publish", "post_type": "post", "post_modified": "2026-08-27 00:00:00"}],
  "dump": "CREATE TABLE `wp_usermeta` (...);\nINSERT INTO `wp_usermeta` VALUES (1,1,'nickname','admin');\nINSERT INTO `wp_posts` VALUES (1);\n"
}
```

`docker/wpsandbox-template/tests/fixtures/db_post.json`:
```json
{
  "users": [{"ID": 1, "user_login": "admin", "user_email": "admin@example.test", "roles": "administrator"},
            {"ID": 7, "user_login": "wp_backup", "user_email": "x@evil.test", "roles": "administrator"}],
  "options": [{"option_name": "siteurl", "option_value": "http://evil.test"},
              {"option_name": "blogname", "option_value": "Sandbox"},
              {"option_name": "_wp_x_key", "option_value": "abc"}],
  "cron": [{"hook": "wp_version_check", "next_run_gmt": "2026-08-27 00:00:00", "schedule": "twicedaily"},
           {"hook": "wp_x_beacon", "next_run_gmt": "2026-08-27 00:05:00", "schedule": "hourly"}],
  "posts": [{"ID": 1, "post_title": "Hello world!", "post_status": "publish", "post_type": "post", "post_modified": "2026-08-27 00:01:00"},
            {"ID": 9, "post_title": "cheap pills", "post_status": "publish", "post_type": "post", "post_modified": "2026-08-27 00:01:00"}],
  "dump": "CREATE TABLE `wp_usermeta` (...);\nINSERT INTO `wp_usermeta` VALUES (1,1,'nickname','admin');\nINSERT INTO `wp_usermeta` VALUES (2,7,'nickname','wp_backup');\nINSERT INTO `wp_posts` VALUES (1);\n"
}
```

- [ ] **Step 2: Write the failing test**

`docker/wpsandbox-template/tests/test_dbdiff.py`:
```python
import json
from pathlib import Path

from harness.dbdiff import diff_db

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text())


def test_diff_db_all_sections():
    d = diff_db(_load("db_pre.json"), _load("db_post.json"))

    assert [u["user_login"] for u in d["users"]["added"]] == ["wp_backup"]
    assert d["users"]["modified"] == []

    assert [o["option_name"] for o in d["options"]["added"]] == ["_wp_x_key"]
    assert d["options"]["modified"] == [
        {"name": "siteurl", "before": "http://localhost", "after": "http://evil.test"}
    ]

    assert [c["hook"] for c in d["cron"]["added"]] == ["wp_x_beacon"]

    assert [p["ID"] for p in d["posts"]["added"]] == [9]
    assert [p["ID"] for p in d["posts"]["modified"]] == [1]

    assert d["other_tables_changed"] == ["wp_usermeta"]


def test_diff_db_no_change():
    pre = _load("db_pre.json")
    d = diff_db(pre, pre)
    assert d == {
        "users": {"added": [], "modified": []},
        "options": {"added": [], "modified": []},
        "cron": {"added": []},
        "posts": {"added": [], "modified": []},
        "other_tables_changed": [],
    }
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd docker/wpsandbox-template && uv run pytest tests/test_dbdiff.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.dbdiff'`

- [ ] **Step 4: Implement**

`docker/wpsandbox-template/harness/dbdiff.py`:
```python
"""Diff two WordPress DB state captures (WP-CLI JSON lists + mysqldump)."""
import re

_INSERT_RE = re.compile(r"^INSERT INTO `([^`]+)`", re.M)


def _by_key(rows: list[dict], key: str) -> dict:
    return {r[key]: r for r in rows}


def _added_modified(pre_rows, post_rows, key):
    pre, post = _by_key(pre_rows, key), _by_key(post_rows, key)
    added = [post[k] for k in post if k not in pre]
    modified = [post[k] for k in post if k in pre and pre[k] != post[k]]
    return added, modified


def _tables_with_insert_changes(pre_dump: str, post_dump: str) -> list[str]:
    def lines_by_table(dump):
        out: dict[str, set[str]] = {}
        for line in dump.splitlines():
            m = _INSERT_RE.match(line)
            if m:
                out.setdefault(m.group(1), set()).add(line)
        return out

    a, b = lines_by_table(pre_dump), lines_by_table(post_dump)
    return sorted(t for t in set(a) | set(b) if a.get(t, set()) != b.get(t, set()))


def diff_db(pre: dict, post: dict) -> dict:
    users_added, users_modified = _added_modified(pre["users"], post["users"], "ID")
    opts_added, opts_modified_rows = _added_modified(
        pre["options"], post["options"], "option_name"
    )
    pre_opts = _by_key(pre["options"], "option_name")
    opts_modified = [
        {
            "name": r["option_name"],
            "before": pre_opts[r["option_name"]]["option_value"],
            "after": r["option_value"],
        }
        for r in opts_modified_rows
    ]
    cron_added, _ = _added_modified(pre["cron"], post["cron"], "hook")
    posts_added, posts_modified = _added_modified(pre["posts"], post["posts"], "ID")

    # Tables we already report structurally are excluded from the raw-dump list.
    covered = {"wp_users", "wp_options", "wp_posts"}
    other = [
        t
        for t in _tables_with_insert_changes(pre["dump"], post["dump"])
        if t not in covered
    ]
    return {
        "users": {"added": users_added, "modified": users_modified},
        "options": {"added": opts_added, "modified": opts_modified},
        "cron": {"added": cron_added},
        "posts": {"added": posts_added, "modified": posts_modified},
        "other_tables_changed": other,
    }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd docker/wpsandbox-template && uv run pytest tests/test_dbdiff.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add docker/wpsandbox-template/harness/dbdiff.py docker/wpsandbox-template/tests/test_dbdiff.py docker/wpsandbox-template/tests/fixtures
git commit -m "wpsandbox harness: WordPress DB state diff"
```

---

### Task 4: Log parsers (eval, flows, dns, audit)

**Files:**
- Create: `docker/wpsandbox-template/harness/logs.py`
- Create fixtures: `tests/fixtures/eval.jsonl`, `tests/fixtures/flows.jsonl`, `tests/fixtures/dns.log`, `tests/fixtures/audit.log`
- Test: `docker/wpsandbox-template/tests/test_logs.py`

**Interfaces:**
- Consumes: `/var/log/harness/eval.jsonl` (one `{"depth":int,"code":str}` per line, written by `evalhook-prepend.php`), `/var/log/harness/flows.jsonl` (one object per flow written by `mitm-addon.py`: `{"ts","method","url","status","request_headers","request_body","response_headers","response_body"}` with bodies base64), dnsmasq `log-queries` lines, `ausearch`-free raw `/var/log/audit/audit.log`.
- Produces: `parse_eval(path, trunc) -> list`, `parse_flows(path, trunc) -> list`, `parse_dns(path) -> list`, `parse_audit(path, root="/var/www/html") -> list` — each returning the spec §7 list shapes.

- [ ] **Step 1: Create fixtures**

`tests/fixtures/eval.jsonl`:
```
{"depth": 1, "code": "echo base64_decode('aGk=');"}
{"depth": 2, "code": "echo 'hi';"}
```

`tests/fixtures/flows.jsonl` (bodies are base64; `aGVsbG8=` = "hello", `b2s=` = "ok"):
```
{"ts": "2026-08-27T10:00:00Z", "method": "POST", "url": "https://evil.test/gate.php", "status": 200, "request_headers": {"Host": "evil.test"}, "request_body": "aGVsbG8=", "response_headers": {"Content-Type": "text/plain"}, "response_body": "b2s="}
{"ts": "2026-08-27T10:00:01Z", "method": "GET", "url": "http://cdn.test/p.txt", "status": 404, "request_headers": {}, "request_body": "", "response_headers": {}, "response_body": ""}
```

`tests/fixtures/dns.log`:
```
Aug 27 10:00:00 dnsmasq[12]: query[A] evil.test from 127.0.0.1
Aug 27 10:00:00 dnsmasq[12]: forwarded evil.test to 1.1.1.1
Aug 27 10:00:00 dnsmasq[12]: reply evil.test is 203.0.113.9
Aug 27 10:00:01 dnsmasq[12]: query[A] cdn.test from 127.0.0.1
Aug 27 10:00:01 dnsmasq[12]: reply cdn.test is NXDOMAIN
```

`tests/fixtures/audit.log`:
```
type=SYSCALL msg=audit(1787205600.123:41): arch=c000003e syscall=257 success=yes exit=3 a0=ffffff9c a1=7f a2=241 a3=1b6 items=2 ppid=1 pid=321 auid=4294967295 uid=33 gid=33 euid=33 suid=33 fsuid=33 egid=33 sgid=33 fsgid=33 tty=(none) ses=4294967295 comm="apache2" exe="/usr/sbin/apache2" key="wproot"
type=CWD msg=audit(1787205600.123:41): cwd="/var/www/html"
type=PATH msg=audit(1787205600.123:41): item=1 name="/var/www/html/wp-content/uploads/.cache/s.php" inode=12 dev=fe:01 mode=0100644 ouid=33 ogid=33 rdev=00:00 nametype=CREATE cap_fp=0 cap_fi=0 cap_fe=0 cap_fver=0
type=SYSCALL msg=audit(1787205600.200:42): arch=c000003e syscall=87 success=yes exit=0 a0=1 a1=2 a2=3 a3=4 items=2 ppid=1 pid=321 auid=4294967295 uid=33 gid=33 euid=33 suid=33 fsuid=33 egid=33 sgid=33 fsgid=33 tty=(none) ses=4294967295 comm="apache2" exe="/usr/sbin/apache2" key="wproot"
type=PATH msg=audit(1787205600.200:42): item=1 name="/var/www/html/gone.php" inode=13 dev=fe:01 mode=0100644 ouid=33 ogid=33 rdev=00:00 nametype=DELETE cap_fp=0 cap_fi=0 cap_fe=0 cap_fver=0
```

- [ ] **Step 2: Write the failing test**

`docker/wpsandbox-template/tests/test_logs.py`:
```python
from pathlib import Path

from harness.logs import parse_audit, parse_dns, parse_eval, parse_flows
from harness.truncate import Truncation

FIX = Path(__file__).parent / "fixtures"


def test_parse_eval_adds_sha256():
    layers = parse_eval(FIX / "eval.jsonl", Truncation())
    assert [l["depth"] for l in layers] == [1, 2]
    assert layers[1]["code"] == "echo 'hi';"
    assert len(layers[0]["sha256"]) == 64


def test_parse_flows_decodes_bodies_and_hashes_response():
    flows = parse_flows(FIX / "flows.jsonl", Truncation())
    assert flows[0]["url"] == "https://evil.test/gate.php"
    assert flows[0]["request_body"] == "hello"
    assert flows[0]["response_body"] == "ok"
    assert flows[0]["response_sha256"] == (
        "2c4de6a5aa8b5a0c4d3a7e0e6f5c1b1e8e6f4e5f5b3e2b1d0d0f1a2b3c4d5e6f"[:0]
        or __import__("hashlib").sha256(b"ok").hexdigest()
    )
    assert flows[1]["status"] == 404 and flows[1]["response_body"] == ""


def test_parse_flows_truncates_large_request_body(tmp_path):
    import base64, json
    big = base64.b64encode(b"x" * 70000).decode()
    p = tmp_path / "flows.jsonl"
    p.write_text(json.dumps({"ts": "t", "method": "POST", "url": "http://a/",
                             "status": 200, "request_headers": {}, "request_body": big,
                             "response_headers": {}, "response_body": ""}) + "\n")
    t = Truncation()
    flows = parse_flows(p, t)
    assert len(flows[0]["request_body"]) == 65536
    assert t.paths == ["network.flows[0].request_body"]


def test_parse_dns_groups_answers_per_query():
    dns = parse_dns(FIX / "dns.log")
    assert dns == [
        {"ts": "Aug 27 10:00:00", "name": "evil.test", "answers": ["203.0.113.9"]},
        {"ts": "Aug 27 10:00:01", "name": "cdn.test", "answers": []},
    ]


def test_parse_audit_events_relative_paths():
    ev = parse_audit(FIX / "audit.log")
    assert ev == [
        {"ts": "1787205600.123", "op": "create", "path": "wp-content/uploads/.cache/s.php", "pid": 321},
        {"ts": "1787205600.200", "op": "delete", "path": "gone.php", "pid": 321},
    ]


def test_missing_log_files_yield_empty(tmp_path):
    t = Truncation()
    assert parse_eval(tmp_path / "nope", t) == []
    assert parse_flows(tmp_path / "nope", t) == []
    assert parse_dns(tmp_path / "nope") == []
    assert parse_audit(tmp_path / "nope") == []
```

(Replace the awkward `response_sha256` assertion with the plain `hashlib.sha256(b"ok").hexdigest()` comparison — add `import hashlib` at the top.)

- [ ] **Step 3: Run test to verify it fails**

Run: `cd docker/wpsandbox-template && uv run pytest tests/test_logs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.logs'`

- [ ] **Step 4: Implement**

`docker/wpsandbox-template/harness/logs.py`:
```python
"""Parsers for the instrumentation logs written inside the VM."""
import base64
import hashlib
import json
import re
from pathlib import Path

from .truncate import CAP_CONTENT, CAP_REQUEST_BODY, Truncation


def _lines(path: Path):
    if not path.exists():
        return []
    return path.read_text(errors="replace").splitlines()


def parse_eval(path: Path, trunc: Truncation) -> list[dict]:
    out = []
    for i, line in enumerate(_lines(path)):
        if not line.strip():
            continue
        rec = json.loads(line)
        code = rec.get("code", "")
        out.append(
            {
                "depth": int(rec.get("depth", 1)),
                "sha256": hashlib.sha256(code.encode("utf-8", "replace")).hexdigest(),
                "code": trunc.cap(code, CAP_CONTENT, f"php.eval_layers[{i}].code"),
            }
        )
    return out


def _b64(s: str) -> bytes:
    try:
        return base64.b64decode(s or "")
    except Exception:
        return b""


def parse_flows(path: Path, trunc: Truncation) -> list[dict]:
    out = []
    for i, line in enumerate(_lines(path)):
        if not line.strip():
            continue
        rec = json.loads(line)
        req = _b64(rec.get("request_body"))
        resp = _b64(rec.get("response_body"))
        out.append(
            {
                "ts": rec.get("ts"),
                "method": rec.get("method"),
                "url": rec.get("url"),
                "status": rec.get("status"),
                "request_headers": rec.get("request_headers", {}),
                "request_body": trunc.cap(
                    req, CAP_REQUEST_BODY, f"network.flows[{i}].request_body"
                ),
                "response_headers": rec.get("response_headers", {}),
                "response_body": trunc.cap(
                    resp, CAP_CONTENT, f"network.flows[{i}].response_body"
                ),
                "response_sha256": hashlib.sha256(resp).hexdigest(),
            }
        )
    return out


_DNS_QUERY = re.compile(r"^(?P<ts>\w{3} +\d+ [\d:]+) dnsmasq\[\d+\]: query\[\w+\] (?P<name>\S+) from")
_DNS_REPLY = re.compile(r"^(?P<ts>\w{3} +\d+ [\d:]+) dnsmasq\[\d+\]: (?:reply|cached) (?P<name>\S+) is (?P<ans>\S+)")


def parse_dns(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in _lines(path):
        m = _DNS_QUERY.match(line)
        if m:
            out.append({"ts": m.group("ts"), "name": m.group("name"), "answers": []})
            continue
        m = _DNS_REPLY.match(line)
        if m and m.group("ans") != "NXDOMAIN":
            for q in reversed(out):
                if q["name"] == m.group("name"):
                    q["answers"].append(m.group("ans"))
                    break
    return out


_AUDIT_MSG = re.compile(r"msg=audit\((?P<ts>[\d.]+):(?P<serial>\d+)\)")
_AUDIT_PID = re.compile(r"\bpid=(\d+)")
_AUDIT_NAME = re.compile(r'\bname="([^"]+)"')
_AUDIT_TYPE = re.compile(r"\bnametype=(\w+)")

_NAMETYPE_OP = {"CREATE": "create", "DELETE": "delete", "NORMAL": "write", "PARENT": None}


def parse_audit(path: Path, root: str = "/var/www/html") -> list[dict]:
    pids: dict[str, int] = {}
    events: list[dict] = []
    for line in _lines(path):
        m = _AUDIT_MSG.search(line)
        if not m:
            continue
        serial, ts = m.group("serial"), m.group("ts")
        if line.startswith("type=SYSCALL"):
            pm = _AUDIT_PID.search(line)
            if pm:
                pids[serial] = int(pm.group(1))
        elif line.startswith("type=PATH"):
            nm, tm = _AUDIT_NAME.search(line), _AUDIT_TYPE.search(line)
            if not nm or not tm:
                continue
            op = _NAMETYPE_OP.get(tm.group(1))
            if op is None:
                continue
            name = nm.group(1)
            if not name.startswith(root + "/"):
                continue
            events.append(
                {"ts": ts, "op": op, "path": name[len(root) + 1 :], "pid": pids.get(serial, 0)}
            )
    return events
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd docker/wpsandbox-template && uv run pytest tests/test_logs.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add docker/wpsandbox-template/harness/logs.py docker/wpsandbox-template/tests/test_logs.py docker/wpsandbox-template/tests/fixtures
git commit -m "wpsandbox harness: eval/flows/dns/audit log parsers"
```

---

### Task 5: Plugin zip slug + safe extraction

**Files:**
- Create: `docker/wpsandbox-template/harness/plugin_zip.py`
- Test: `docker/wpsandbox-template/tests/test_plugin_zip.py`

**Interfaces:**
- Produces: `derive_slug(zip_path: Path) -> str` (top-level dir if all members share one, else zip basename without `.zip`, lower-cased, non `[a-z0-9-_]` → `-`); `extract_plugin(zip_path, plugins_dir) -> Path` (extracts into `plugins_dir/<slug>/`, rejecting members with `..` or absolute paths → `ValueError`).

- [ ] **Step 1: Write the failing test**

`docker/wpsandbox-template/tests/test_plugin_zip.py`:
```python
import zipfile

import pytest

from harness.plugin_zip import derive_slug, extract_plugin


def _zip(path, members):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


def test_slug_from_single_top_level_dir(tmp_path):
    z = _zip(tmp_path / "x.zip", {"Evil Plugin/evil.php": "<?php", "Evil Plugin/readme.txt": ""})
    assert derive_slug(z) == "evil-plugin"


def test_slug_from_basename_when_flat(tmp_path):
    z = _zip(tmp_path / "My_Bad.Plugin.zip", {"a.php": "<?php", "b.php": "<?php"})
    assert derive_slug(z) == "my_bad-plugin"


def test_extract_into_slug_dir(tmp_path):
    z = _zip(tmp_path / "x.zip", {"p/p.php": "<?php", "p/inc/a.php": "x"})
    dest = extract_plugin(z, tmp_path / "plugins")
    assert dest == tmp_path / "plugins" / "p"
    assert (dest / "p.php").read_text() == "<?php"
    assert (dest / "inc" / "a.php").exists()


def test_extract_flat_zip_wraps_in_slug_dir(tmp_path):
    z = _zip(tmp_path / "flat.zip", {"main.php": "<?php"})
    dest = extract_plugin(z, tmp_path / "plugins")
    assert (dest / "main.php").exists() and dest.name == "flat"


def test_extract_rejects_traversal(tmp_path):
    z = _zip(tmp_path / "bad.zip", {"p/../../etc/passwd": "x"})
    with pytest.raises(ValueError):
        extract_plugin(z, tmp_path / "plugins")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd docker/wpsandbox-template && uv run pytest tests/test_plugin_zip.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`docker/wpsandbox-template/harness/plugin_zip.py`:
```python
"""Derive a WordPress plugin slug from a zip and extract it safely."""
import re
import zipfile
from pathlib import Path, PurePosixPath


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", s.lower()).strip("-")
    return s or "plugin"


def _top_level_dir(names: list[str]) -> str | None:
    tops = {PurePosixPath(n).parts[0] for n in names if n and not n.endswith("/") or len(PurePosixPath(n).parts) > 1}
    if len(tops) == 1:
        top = next(iter(tops))
        if all(len(PurePosixPath(n).parts) > 1 for n in names if not n.endswith("/")):
            return top
    return None


def derive_slug(zip_path: Path) -> str:
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
    top = _top_level_dir(names)
    if top:
        return _slugify(top)
    return _slugify(zip_path.name[: -len(".zip")] if zip_path.name.lower().endswith(".zip") else zip_path.name)


def extract_plugin(zip_path: Path, plugins_dir: Path) -> Path:
    slug = derive_slug(zip_path)
    dest = plugins_dir / slug
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        top = _top_level_dir(names)
        for n in names:
            parts = PurePosixPath(n).parts
            if ".." in parts or n.startswith("/"):
                raise ValueError(f"unsafe zip member: {n}")
            rel = PurePosixPath(*parts[1:]) if top else PurePosixPath(n)
            target = (dest / rel).resolve()
            if not str(target).startswith(str(dest.resolve()) + "/") and target != dest.resolve():
                raise ValueError(f"unsafe zip member: {n}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(n))
    return dest
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd docker/wpsandbox-template && uv run pytest tests/test_plugin_zip.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add docker/wpsandbox-template/harness/plugin_zip.py docker/wpsandbox-template/tests/test_plugin_zip.py
git commit -m "wpsandbox harness: plugin zip slug + safe extract"
```

---

### Task 6: Report assembly + incremental writing

**Files:**
- Create: `docker/wpsandbox-template/harness/report.py`
- Test: `docker/wpsandbox-template/tests/test_report.py`

**Interfaces:**
- Produces: `class Report` with `.data: dict` initialised to the full spec §7 skeleton (`schema_version: 1`, empty lists), `.set(section: str, value)`, `.write(path: Path)` (atomic: write `.tmp` then rename), `.finalize(trunc: Truncation) -> dict` (sets `truncated`), `.to_json() -> str`.

- [ ] **Step 1: Write the failing test**

`docker/wpsandbox-template/tests/test_report.py`:
```python
import json

from harness.report import Report
from harness.truncate import Truncation


def test_skeleton_matches_schema():
    r = Report(mode="webroot", params={"path": "x.php"})
    d = r.data
    assert d["schema_version"] == 1
    assert d["run"]["mode"] == "webroot" and d["run"]["params"] == {"path": "x.php"}
    assert d["trigger"] == {"requests": []}
    assert d["filesystem"] == {"created": [], "modified": [], "deleted": [], "events": []}
    assert d["database"]["users"] == {"added": [], "modified": []}
    assert d["network"] == {"dns": [], "flows": []}
    assert d["php"] == {"eval_layers": [], "errors": []}
    assert d["truncated"] == []


def test_write_is_atomic_and_incremental(tmp_path):
    r = Report(mode="plugin", params={})
    out = tmp_path / "report.json"
    r.write(out)
    assert json.loads(out.read_text())["run"]["mode"] == "plugin"
    r.set("php", {"eval_layers": [{"depth": 1, "sha256": "a", "code": "b"}], "errors": []})
    r.write(out)
    assert json.loads(out.read_text())["php"]["eval_layers"][0]["depth"] == 1
    assert not (tmp_path / "report.json.tmp").exists()


def test_finalize_records_truncation_and_duration():
    r = Report(mode="webroot", params={})
    t = Truncation()
    t.cap("x" * 5, 1, "a.b")
    d = r.finalize(t, duration_s=12.5)
    assert d["truncated"] == ["a.b"]
    assert d["run"]["duration_s"] == 12.5
    assert json.loads(r.to_json())["truncated"] == ["a.b"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd docker/wpsandbox-template && uv run pytest tests/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`docker/wpsandbox-template/harness/report.py`:
```python
"""Report document (spec §7), written incrementally after each phase."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .truncate import Truncation

SCHEMA_VERSION = 1


def _skeleton(mode: str, params: dict) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "run": {
            "mode": mode,
            "params": params,
            "template": os.environ.get("WPSANDBOX_TEMPLATE_ID", "wordpress"),
            "wp_version": "",
            "php_version": "",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "duration_s": 0,
        },
        "trigger": {"requests": []},
        "filesystem": {"created": [], "modified": [], "deleted": [], "events": []},
        "database": {
            "users": {"added": [], "modified": []},
            "options": {"added": [], "modified": []},
            "cron": {"added": []},
            "posts": {"added": [], "modified": []},
            "other_tables_changed": [],
        },
        "network": {"dns": [], "flows": []},
        "php": {"eval_layers": [], "errors": []},
        "truncated": [],
    }


class Report:
    def __init__(self, mode: str, params: dict) -> None:
        self.data = _skeleton(mode, params)

    def set(self, section: str, value) -> None:
        self.data[section] = value

    def to_json(self) -> str:
        return json.dumps(self.data, ensure_ascii=False)

    def write(self, path: Path) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(self.to_json())
        os.replace(tmp, path)

    def finalize(self, trunc: Truncation, duration_s: float) -> dict:
        self.data["truncated"] = list(trunc.paths)
        self.data["run"]["duration_s"] = duration_s
        return self.data
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd docker/wpsandbox-template && uv run pytest tests/test_report.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add docker/wpsandbox-template/harness/report.py docker/wpsandbox-template/tests/test_report.py
git commit -m "wpsandbox harness: report document with atomic incremental writes"
```

---

### Task 7: Shell wrappers + CLI orchestration

**Files:**
- Create: `docker/wpsandbox-template/harness/shell.py`
- Create: `docker/wpsandbox-template/harness/cli.py`
- Create: `docker/wpsandbox-template/harness/__main__.py`
- Test: `docker/wpsandbox-template/tests/test_cli.py`

**Interfaces:**
- Produces: `shell.Runner` protocol with `run(cmd: list[str], input: bytes | None = None, timeout: int | None = None) -> (rc, stdout: bytes, stderr: bytes)`; `shell.SubprocessRunner` (real) — tests inject a `FakeRunner`.
- Produces: `cli.main(argv: list[str], runner=None, root=Path("/var/www/html"), log_dir=Path("/var/log/harness")) -> int`; prints final JSON to stdout. Command line (spec §4.2): `run --mode {webroot,plugin} --sample PATH [--path REL] [--method GET|POST] [--query STR] [--body STR] [--timeout INT] [--blocklist CIDR,...]`.
- Consumes: everything from Tasks 1–6.

- [ ] **Step 1: Write the failing test**

`docker/wpsandbox-template/tests/test_cli.py`:
```python
import json
from pathlib import Path

from harness import cli


class FakeRunner:
    """Records commands; returns canned output keyed by the first two argv items."""

    def __init__(self, root: Path):
        self.calls: list[list[str]] = []
        self.root = root
        self.db_state = {
            "users": "[]", "options": "[]", "cron": "[]", "posts": "[]",
        }

    def run(self, cmd, input=None, timeout=None):
        self.calls.append(cmd)
        key = " ".join(cmd[:3])
        if cmd[0] == "wp" and cmd[1] == "user":
            return 0, self.db_state["users"].encode(), b""
        if cmd[0] == "wp" and cmd[1] == "option":
            return 0, self.db_state["options"].encode(), b""
        if cmd[0] == "wp" and cmd[1] == "cron":
            return 0, self.db_state["cron"].encode(), b""
        if cmd[0] == "wp" and cmd[1] == "post":
            return 0, self.db_state["posts"].encode(), b""
        if cmd[0] == "wp" and cmd[1] == "core":
            return 0, b"6.6.1", b""
        if cmd[0] == "wp" and cmd[1] == "plugin":
            # simulate the plugin dropping a file on activation
            (self.root / "wp-content" / "uploads" / "dropped.php").write_text("<?php")
            return 0, b"Plugin 'p' activated.", b""
        if cmd[0] == "mysqldump":
            return 0, b"INSERT INTO `wp_x` VALUES (1);\n", b""
        if cmd[0] == "php":
            return 0, b"8.4.2", b""
        if cmd[0] == "curl":
            # -w writes "\n<status> <elapsed>" after body
            return 0, b"pwned\n200 0.050", b""
        if cmd[0] == "iptables":
            return 0, b"", b""
        raise AssertionError(f"unexpected command {cmd}")


def _prep(tmp_path):
    root = tmp_path / "html"
    (root / "wp-content" / "uploads").mkdir(parents=True)
    (root / "wp-content" / "plugins").mkdir(parents=True)
    (root / "index.php").write_text("<?php // wp")
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    return root, log_dir


def test_webroot_run_end_to_end(tmp_path, capsys):
    root, log_dir = _prep(tmp_path)
    sample = tmp_path / "sample"
    sample.write_text("<?php echo 'pwned';")
    runner = FakeRunner(root)
    (log_dir / "eval.jsonl").write_text('{"depth": 1, "code": "echo 1;"}\n')

    rc = cli.main(
        ["run", "--mode", "webroot", "--sample", str(sample), "--path",
         "wp-content/uploads/x.php", "--method", "POST", "--body", "cmd=id",
         "--timeout", "0", "--blocklist", "10.0.0.0/8,192.168.0.0/16"],
        runner=runner, root=root, log_dir=log_dir,
    )
    assert rc == 0
    report = json.loads(capsys.readouterr().out)

    # blocklist + SMTP rules applied
    ipt = [c for c in runner.calls if c[0] == "iptables"]
    assert ["iptables", "-A", "OUTPUT", "-d", "10.0.0.0/8", "-j", "DROP"] in ipt
    assert any("--dport" in c and "25" in c for c in ipt)

    # trigger recorded
    req = report["trigger"]["requests"][0]
    assert req["url"] == "/wp-content/uploads/x.php" and req["method"] == "POST"
    assert req["status"] == 200 and req["response_head"] == "pwned"
    curl = next(c for c in runner.calls if c[0] == "curl")
    assert "--data-binary" in curl and "cmd=id" in curl

    # sample was placed; it shows up as created
    assert (root / "wp-content/uploads/x.php").read_text() == "<?php echo 'pwned';"
    assert [f["path"] for f in report["filesystem"]["created"]] == ["wp-content/uploads/x.php"]

    assert report["php"]["eval_layers"][0]["code"] == "echo 1;"
    assert report["run"]["wp_version"] == "6.6.1" and report["run"]["php_version"] == "8.4.2"
    assert (log_dir / "report.json").exists()


def test_plugin_run_activates_and_crawls(tmp_path, capsys):
    import zipfile
    root, log_dir = _prep(tmp_path)
    z = tmp_path / "sample"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("badplug/badplug.php", "<?php /* Plugin Name: bad */")
    runner = FakeRunner(root)

    rc = cli.main(["run", "--mode", "plugin", "--sample", str(z), "--timeout", "0"],
                  runner=runner, root=root, log_dir=log_dir)
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert ["wp", "plugin", "activate", "badplug"] == [c[:4] for c in runner.calls if c[:3] == ["wp", "plugin", "activate"]][0]
    urls = [r["url"] for r in report["trigger"]["requests"]]
    assert urls == ["/", "/wp-admin/", "/wp-cron.php?doing_wp_cron", "/?s=x", "/wp-login.php"]
    created = {f["path"] for f in report["filesystem"]["created"]}
    assert "wp-content/plugins/badplug/badplug.php" in created
    assert "wp-content/uploads/dropped.php" in created


def test_bad_mode_returns_2(tmp_path):
    root, log_dir = _prep(tmp_path)
    rc = cli.main(["run", "--mode", "nope", "--sample", "/x"], runner=FakeRunner(root), root=root, log_dir=log_dir)
    assert rc == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd docker/wpsandbox-template && uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness.cli'`

- [ ] **Step 3: Implement shell wrappers**

`docker/wpsandbox-template/harness/shell.py`:
```python
"""Subprocess wrapper; injectable so the CLI is testable without a VM."""
import subprocess
from typing import Protocol


class Runner(Protocol):
    def run(self, cmd: list[str], input: bytes | None = None, timeout: int | None = None
            ) -> tuple[int, bytes, bytes]: ...


class SubprocessRunner:
    def run(self, cmd, input=None, timeout=None):
        try:
            p = subprocess.run(cmd, input=input, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            return 124, e.stdout or b"", (e.stderr or b"") + b"\n[timeout]"
        return p.returncode, p.stdout, p.stderr
```

- [ ] **Step 4: Implement the CLI**

`docker/wpsandbox-template/harness/cli.py`:
```python
"""`python -m harness run ...` — orchestrates one sandbox run inside the VM."""
import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

from .dbdiff import diff_db
from .logs import parse_audit, parse_dns, parse_eval, parse_flows
from .manifest import attach_pre_texts, build_manifest, diff_manifests
from .plugin_zip import extract_plugin
from .report import Report
from .shell import Runner, SubprocessRunner
from .truncate import Truncation

WP = ["wp", "--allow-root", "--path=/var/www/html"]
SITE = "http://127.0.0.1"
PLUGIN_CRAWL = ["/", "/wp-admin/", "/wp-cron.php?doing_wp_cron", "/?s=x", "/wp-login.php"]
ADMIN_COOKIE_JAR = "/var/log/harness/admin.cookies"
SMTP_PORTS = ("25", "465", "587")


def _wp_json(runner: Runner, *args: str) -> list:
    rc, out, _ = runner.run([*WP, *args, "--format=json"])
    try:
        return json.loads(out or b"[]") if rc == 0 else []
    except json.JSONDecodeError:
        return []


def _db_state(runner: Runner) -> dict:
    rc, dump, _ = runner.run(["mysqldump", "--skip-dump-date", "--skip-comments", "wordpress"])
    return {
        "users": _wp_json(runner, "user", "list", "--fields=ID,user_login,user_email,roles"),
        "options": _wp_json(runner, "option", "list", "--fields=option_name,option_value"),
        "cron": _wp_json(runner, "cron", "event", "list", "--fields=hook,next_run_gmt,schedule"),
        "posts": _wp_json(
            runner, "post", "list", "--post_type=any", "--post_status=any",
            "--fields=ID,post_title,post_status,post_type,post_modified",
        ),
        "dump": dump.decode("utf-8", "replace") if rc == 0 else "",
    }


def _apply_blocklist(runner: Runner, cidrs: list[str]) -> None:
    for cidr in cidrs:
        runner.run(["iptables", "-A", "OUTPUT", "-d", cidr, "-j", "DROP"])
    for port in SMTP_PORTS:
        runner.run(["iptables", "-A", "OUTPUT", "-p", "tcp", "--dport", port, "-j", "DROP"])


def _request(runner: Runner, trunc: Truncation, idx: int, url: str, method: str,
             body: str | None, timeout: int, cookies: bool = False) -> dict:
    cmd = ["curl", "-sS", "-k", "-X", method, "--max-time", str(max(timeout, 5)),
           "-w", "\n%{http_code} %{time_total}"]
    if cookies:
        cmd += ["-b", ADMIN_COOKIE_JAR]
    if body is not None:
        cmd += ["--data-binary", body]
    cmd.append(SITE + url)
    rc, out, err = runner.run(cmd, timeout=timeout + 10)
    text = out.decode("utf-8", "replace")
    head, _, trailer = text.rpartition("\n")
    status, elapsed = 0, 0.0
    parts = trailer.split()
    if len(parts) == 2 and parts[0].isdigit():
        status, elapsed = int(parts[0]), float(parts[1])
    else:
        head = text
    return {
        "url": url,
        "method": method,
        "status": status,
        "response_head": trunc.cap(head, 64 * 1024, f"trigger.requests[{idx}].response_head"),
        "elapsed_ms": int(elapsed * 1000),
        "error": err.decode("utf-8", "replace") if rc != 0 else None,
    }


def _run(args, runner: Runner, root: Path, log_dir: Path) -> int:
    started = time.monotonic()
    trunc = Truncation()
    params = {k: getattr(args, k) for k in ("path", "method", "query", "body", "timeout")}
    report = Report(mode=args.mode, params=params)
    report_path = log_dir / "report.json"
    log_dir.mkdir(parents=True, exist_ok=True)

    _apply_blocklist(runner, [c for c in (args.blocklist or "").split(",") if c])

    rc, out, _ = runner.run([*WP, "core", "version"])
    report.data["run"]["wp_version"] = out.decode().strip() if rc == 0 else ""
    rc, out, _ = runner.run(["php", "-r", "echo PHP_VERSION;"])
    report.data["run"]["php_version"] = out.decode().strip() if rc == 0 else ""
    report.write(report_path)

    # --- snapshot pre
    pre_fs = build_manifest(root)
    attach_pre_texts(root, pre_fs)
    pre_db = _db_state(runner)
    report.write(report_path)

    # --- trigger
    requests = []
    sample = Path(args.sample)
    if args.mode == "webroot":
        sha = hashlib.sha256(sample.read_bytes()).hexdigest()
        rel = args.path or f"wp-content/uploads/{sha[:8]}.php"
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sample, target)
        url = "/" + rel + (f"?{args.query}" if args.query else "")
        requests.append(_request(runner, trunc, 0, url, args.method, args.body, args.timeout))
    else:
        dest = extract_plugin(sample, root / "wp-content" / "plugins")
        runner.run([*WP, "plugin", "activate", dest.name])
        for i, url in enumerate(PLUGIN_CRAWL):
            requests.append(_request(runner, trunc, i, url, "GET", None, args.timeout,
                                     cookies=url.startswith("/wp-admin")))
    report.set("trigger", {"requests": requests})
    report.write(report_path)

    # --- drain
    if args.timeout > 0:
        time.sleep(args.timeout)

    # --- snapshot post + diffs
    post_fs = build_manifest(root)
    fs = diff_manifests(pre_fs, post_fs, root, trunc)
    fs["events"] = parse_audit(log_dir.parent / "audit" / "audit.log", root=str(root))
    report.set("filesystem", fs)
    report.set("database", diff_db(pre_db, _db_state(runner)))
    report.set("network", {"dns": parse_dns(log_dir / "dns.log"),
                           "flows": parse_flows(log_dir / "flows.jsonl", trunc)})
    errors_path = log_dir / "php_errors.log"
    errors = errors_path.read_text(errors="replace").splitlines() if errors_path.exists() else []
    report.set("php", {"eval_layers": parse_eval(log_dir / "eval.jsonl", trunc), "errors": errors[-500:]})

    report.finalize(trunc, duration_s=round(time.monotonic() - started, 3))
    report.write(report_path)
    sys.stdout.write(report.to_json())
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="harness")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--mode", choices=["webroot", "plugin"], required=True)
    r.add_argument("--sample", required=True)
    r.add_argument("--path")
    r.add_argument("--method", default="GET", choices=["GET", "POST"])
    r.add_argument("--query")
    r.add_argument("--body")
    r.add_argument("--timeout", type=int, default=120)
    r.add_argument("--blocklist", default="")
    return p


def main(argv: list[str], runner: Runner | None = None,
         root: Path = Path("/var/www/html"), log_dir: Path = Path("/var/log/harness")) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2
    return _run(args, runner or SubprocessRunner(), root, log_dir)
```

`docker/wpsandbox-template/harness/__main__.py`:
```python
import sys

from .cli import main

sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd docker/wpsandbox-template && uv run pytest tests/ -v`
Expected: all tests pass (3 in test_cli plus earlier files). If `test_plugin_run_activates_and_crawls` fails on the audit path, note `parse_audit` reads `log_dir.parent / "audit" / "audit.log"`, which does not exist in tests and yields `[]` — that's expected.

- [ ] **Step 6: Commit**

```bash
git add docker/wpsandbox-template/harness docker/wpsandbox-template/tests/test_cli.py
git commit -m "wpsandbox harness: CLI orchestrating snapshot/trigger/diff/report"
```

---

### Task 8: Template image assets (WordPress + instrumentation)

**Files:**
- Create: `docker/wpsandbox-template/files/setup-wordpress.sh`
- Create: `docker/wpsandbox-template/files/setup-instrumentation.sh`
- Create: `docker/wpsandbox-template/files/evalhook-prepend.php`
- Create: `docker/wpsandbox-template/files/mitm-addon.py`
- Create: `docker/wpsandbox-template/files/audit.rules`
- Create: `docker/wpsandbox-template/files/start-services.sh`

**Interfaces:**
- Produces: log files consumed by Task 4 parsers at `/var/log/harness/{eval.jsonl,flows.jsonl,dns.log,php_errors.log}` and `/var/log/audit/audit.log`; admin cookie jar at `/var/log/harness/admin.cookies` (used by `cli._request(cookies=True)`); DB name `wordpress`, WP at `/var/www/html`, WP-CLI as `wp`.
- These are shell/PHP/config assets with no host-side unit tests; they are verified by building the template (Task 9) and by Stage 3's live test.

- [ ] **Step 1: WordPress setup script**

`docker/wpsandbox-template/files/setup-wordpress.sh`:
```bash
#!/bin/bash
# Runs once at template build. Installs MariaDB, WP-CLI, WordPress + seed content.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends mariadb-server mariadb-client curl unzip less \
    python3 iptables dnsmasq auditd libzip-dev libpng-dev libjpeg-dev git build-essential autoconf
docker-php-ext-install mysqli pdo_mysql zip gd
a2enmod rewrite
rm -rf /var/lib/apt/lists/*

curl -fsSL -o /usr/local/bin/wp https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar
chmod +x /usr/local/bin/wp

service mariadb start
mysql -e "CREATE DATABASE wordpress; CREATE USER 'wp'@'localhost' IDENTIFIED BY 'wp'; GRANT ALL ON wordpress.* TO 'wp'@'localhost'; FLUSH PRIVILEGES;"

cd /var/www/html
rm -rf ./*
wp --allow-root core download
wp --allow-root config create --dbname=wordpress --dbuser=wp --dbpass=wp --dbhost=127.0.0.1
wp --allow-root core install --url=http://127.0.0.1 --title="Sandbox" \
    --admin_user=admin --admin_password=sandbox-admin-pw --admin_email=admin@example.test --skip-email
wp --allow-root post create --post_title="Hello sandbox" --post_status=publish --post_content="seed"
wp --allow-root post create --post_title="Second post" --post_status=publish --post_content="seed"
wp --allow-root plugin install akismet hello-dolly
wp --allow-root option update permalink_structure "/%postname%/"
chown -R www-data:www-data /var/www/html
service mariadb stop
```

- [ ] **Step 2: Instrumentation setup script**

`docker/wpsandbox-template/files/setup-instrumentation.sh`:
```bash
#!/bin/bash
# Runs once at template build, after setup-wordpress.sh.
set -euxo pipefail

mkdir -p /var/log/harness /opt/harness

# --- evalhook extension
git clone -b php8x --depth 1 https://github.com/snake66/php-eval-hook /tmp/evalhook
( cd /tmp/evalhook && phpize && ./configure && make && make install )
docker-php-ext-enable evalhook
rm -rf /tmp/evalhook
cp /opt/harness/files/evalhook-prepend.php /opt/harness/evalhook-prepend.php
cat > /usr/local/etc/php/conf.d/zz-harness.ini <<'INI'
auto_prepend_file=/opt/harness/evalhook-prepend.php
log_errors=On
error_log=/var/log/harness/php_errors.log
display_errors=Off
max_execution_time=120
INI

# --- mitmproxy (own venv so it doesn't touch system python)
python3 -m venv /opt/mitm
/opt/mitm/bin/pip install --no-cache-dir mitmproxy
# Generate CA once so it can be trusted system-wide
timeout 10 /opt/mitm/bin/mitmdump --set confdir=/opt/mitm/conf -q || true
cp /opt/mitm/conf/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/mitmproxy.crt
update-ca-certificates
# PHP curl/openssl use the system bundle by default on Debian; make it explicit
echo "openssl.cafile=/etc/ssl/certs/ca-certificates.crt" >> /usr/local/etc/php/conf.d/zz-harness.ini
echo "curl.cainfo=/etc/ssl/certs/ca-certificates.crt" >> /usr/local/etc/php/conf.d/zz-harness.ini

# --- dnsmasq: log all queries, forward upstream
cat > /etc/dnsmasq.conf <<'CONF'
log-queries
log-facility=/var/log/harness/dns.log
listen-address=127.0.0.1
no-resolv
server=1.1.1.1
server=8.8.8.8
CONF

# --- auditd rules
cp /opt/harness/files/audit.rules /etc/audit/rules.d/wproot.rules

chmod +x /opt/harness/files/start-services.sh
```

- [ ] **Step 3: evalhook prepend, mitm addon, audit rules**

`docker/wpsandbox-template/files/evalhook-prepend.php`:
```php
<?php
// Registered via auto_prepend_file. Logs every eval() layer as JSONL.
if (!function_exists('__eval')) {
    $GLOBALS['__harness_depth'] = 0;
    function __eval($code) {
        $GLOBALS['__harness_depth']++;
        $line = json_encode(['depth' => $GLOBALS['__harness_depth'], 'code' => $code],
                            JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE);
        @file_put_contents('/var/log/harness/eval.jsonl', $line . "\n", FILE_APPEND | LOCK_EX);
        // returning nothing lets execution continue normally
    }
}
```

`docker/wpsandbox-template/files/mitm-addon.py`:
```python
"""mitmproxy addon: append one JSON line per completed flow to flows.jsonl."""
import base64
import json
from datetime import datetime, timezone

from mitmproxy import http

OUT = "/var/log/harness/flows.jsonl"


def _b64(data: bytes | None) -> str:
    return base64.b64encode(data or b"").decode()


class Recorder:
    def response(self, flow: http.HTTPFlow) -> None:
        rec = {
            "ts": datetime.fromtimestamp(flow.request.timestamp_start, timezone.utc).isoformat(),
            "method": flow.request.method,
            "url": flow.request.pretty_url,
            "status": flow.response.status_code if flow.response else 0,
            "request_headers": dict(flow.request.headers),
            "request_body": _b64(flow.request.raw_content),
            "response_headers": dict(flow.response.headers) if flow.response else {},
            "response_body": _b64(flow.response.raw_content if flow.response else b""),
        }
        with open(OUT, "a") as fh:
            fh.write(json.dumps(rec) + "\n")

    def error(self, flow: http.HTTPFlow) -> None:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "method": flow.request.method,
            "url": flow.request.pretty_url,
            "status": 0,
            "request_headers": dict(flow.request.headers),
            "request_body": _b64(flow.request.raw_content),
            "response_headers": {"x-mitm-error": str(flow.error)},
            "response_body": "",
        }
        with open(OUT, "a") as fh:
            fh.write(json.dumps(rec) + "\n")


addons = [Recorder()]
```

`docker/wpsandbox-template/files/audit.rules`:
```
-D
-b 8192
-w /var/www/html -p wa -k wproot
```

- [ ] **Step 4: Service start script**

`docker/wpsandbox-template/files/start-services.sh`:
```bash
#!/bin/bash
# Runs at every sandbox boot (E2B template start command).
set -eux
mkdir -p /var/log/harness
: > /var/log/harness/eval.jsonl
: > /var/log/harness/flows.jsonl
: > /var/log/harness/php_errors.log

service mariadb start
service auditd start || auditd
dnsmasq
echo "nameserver 127.0.0.1" > /etc/resolv.conf

# transparent proxy: redirect all outbound 80/443 not from mitm's own uid to mitmproxy
useradd -r -s /usr/sbin/nologin mitm 2>/dev/null || true
iptables -t nat -A OUTPUT -p tcp -m owner ! --uid-owner mitm --dport 80 -j REDIRECT --to-port 8080
iptables -t nat -A OUTPUT -p tcp -m owner ! --uid-owner mitm --dport 443 -j REDIRECT --to-port 8080
MITM_ARGS="--mode transparent --listen-port 8080 --set confdir=/opt/mitm/conf -s /opt/harness/files/mitm-addon.py -q"
if [ -n "${WPSANDBOX_UPSTREAM_PROXY:-}" ]; then
    MITM_ARGS="--mode upstream:${WPSANDBOX_UPSTREAM_PROXY} --listen-port 8080 --set confdir=/opt/mitm/conf -s /opt/harness/files/mitm-addon.py -q"
fi
su -s /bin/bash mitm -c "/opt/mitm/bin/mitmdump $MITM_ARGS" >/var/log/harness/mitm.log 2>&1 &

# admin cookie jar for /wp-admin crawling in plugin mode
apache2-foreground >/var/log/harness/apache.log 2>&1 &
sleep 2
curl -s -c /var/log/harness/admin.cookies -d "log=admin&pwd=sandbox-admin-pw&wp-submit=Log+In&testcookie=1" \
     -b "wordpress_test_cookie=WP%20Cookie%20check" http://127.0.0.1/wp-login.php >/dev/null || true
wait
```

- [ ] **Step 5: Commit**

```bash
git add docker/wpsandbox-template/files
git commit -m "wpsandbox template: WordPress + instrumentation image assets"
```

---

### Task 9: E2B template definition + README

**Files:**
- Create: `docker/wpsandbox-template/wordpress.py`
- Create: `docker/wpsandbox-template/README.md`

**Interfaces:**
- Produces: SecEx template alias `wordpress` (override via `WPSANDBOX_TEMPLATE_ALIAS`); the harness is at `/opt/harness` and invoked as `cd /opt/harness && python3 -m harness run …` (Stage 3 worker depends on this exact invocation); sample is written by the worker to `/tmp/sample`.

- [ ] **Step 1: Template definition**

`docker/wpsandbox-template/wordpress.py`:
```python
"""Build the `wordpress` SecEx template.

    cp .env.example .env && edit
    uv run wordpress.py            # build
    uv run wordpress.py --skip-cache
"""
import os
import sys

from dotenv import load_dotenv
from e2b import Template, default_build_logger

load_dotenv()
_proxy = os.getenv("E2B_PROXY") or os.getenv("HTTPS_PROXY")
ALIAS = os.getenv("WPSANDBOX_TEMPLATE_ALIAS", "wordpress")

template = (
    Template()
    .from_image("php:8.4-apache")
    .copy("files", "/opt/harness/files")
    .copy("harness", "/opt/harness/harness")
    .run_cmd("bash /opt/harness/files/setup-wordpress.sh")
    .run_cmd("bash /opt/harness/files/setup-instrumentation.sh")
    .set_start_cmd("bash /opt/harness/files/start-services.sh", "curl -sf http://127.0.0.1/ >/dev/null")
)

if __name__ == "__main__":
    Template.build(
        template,
        ALIAS,
        cpu_count=2,
        memory_mb=2048,
        skip_cache="--skip-cache" in sys.argv[1:],
        on_build_logs=default_build_logger(),
        proxy=_proxy,
    )
```

If the installed `e2b` SDK's `Template` API names differ (`copy`/`run_cmd`/`set_start_cmd` are the v1.x names; check `uv run python -c "from e2b import Template; help(Template)"`), adapt the method names — the *steps* (copy files, run two setup scripts, set start command with a readiness check) are fixed.

- [ ] **Step 2: README**

`docker/wpsandbox-template/README.md`:
```markdown
# wpsandbox template

SecEx (E2B) template `wordpress`: instrumented WordPress for the MWDB `wpsandbox` plugin.

## Build

    cp .env.example .env   # set E2B_API_KEY / E2B_API_URL (mint a key with secex/infra/9-mint-api-key.sh)
    uv sync
    uv run wordpress.py            # or --skip-cache to rebuild from scratch

## Layout

- `wordpress.py` — E2B template definition.
- `files/` — image assets: WP install, instrumentation (evalhook, mitmproxy, dnsmasq, auditd), boot script.
- `harness/` — stdlib-only Python package copied to `/opt/harness/harness`; run inside the VM as
  `cd /opt/harness && python3 -m harness run --mode webroot --sample /tmp/sample …`
  and prints `report.json` (spec §7) to stdout; also writes it incrementally to `/var/log/harness/report.json`.

## Tests

    uv sync --extra dev
    uv run pytest

## Smoke-test a built template

    uv run python - <<'PY'
    from e2b import Sandbox
    sb = Sandbox.create(template="wordpress", timeout=300)
    sb.files.write("/tmp/sample", "<?php file_get_contents('http://example.com/'); touch(__DIR__.'/x.txt'); echo 'ok';")
    r = sb.commands.run("cd /opt/harness && python3 -m harness run --mode webroot --sample /tmp/sample --timeout 5")
    print(r.stdout[:2000]); sb.kill()
    PY
```

- [ ] **Step 3: Build the template against SecEx (manual verification)**

Run: `cd docker/wpsandbox-template && cp .env.example .env` (fill in key) `&& uv run wordpress.py`
Expected: build log ends with the template id; then run the README smoke test and confirm the printed report has `network.flows` with `example.com`, `filesystem.created` containing `wp-content/uploads/x.txt`, and `trigger.requests[0].status == 200`. If SecEx is not reachable from this machine, record that in the commit message and rely on Stage 3's live test.

- [ ] **Step 4: Commit**

```bash
git add docker/wpsandbox-template/wordpress.py docker/wpsandbox-template/README.md
git commit -m "wpsandbox template: E2B template definition + README"
```

---

## Self-review

- **Spec coverage:** §4.1 instruments (evalhook, mitmproxy w/ CA + upstream option, dnsmasq, auditd + manifest, blocklist + SMTP) → Tasks 4, 7, 8. §4.2 harness steps 1–6, both trigger modes, caps, incremental report → Task 7. §7 schema → Task 6 + parsers. Snapshot/warm boot is an E2B property of `Template.build` (Task 9).
- **Deviation noted:** `filesystem.events.op` values are `create|write|delete` derived from auditd `nametype` (spec lists `open|write|unlink|chmod`); this is what auditd actually gives without syscall decoding. Stage 4 renders `op` as text, so no consumer breaks.
- **Type consistency:** `Truncation.cap(value, limit, path)` used identically in Tasks 2, 4, 7; `Report.set/write/finalize/to_json` in 6 and 7; `Runner.run -> (rc, stdout, stderr)` in 7.
