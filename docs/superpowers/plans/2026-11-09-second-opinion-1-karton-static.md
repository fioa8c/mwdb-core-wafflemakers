# Second-Opinion Pipeline, Part 1: Karton and `malleus-static` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A file uploaded to MWDB with a `source:` tag comes back with deobfuscated layers, a model read and a `verdict:*` tag on its sample page, produced by Malleus running as a Karton service.

**Architecture:** MWDB (`--with karton`) spawns a task per new File; `malleus-static`, a Karton consumer in the Malleus repo running as a host process on the worker droplet, applies the source policy, runs `pipeline.run_static`, and emits reporter tasks (an `analyzed` sample task carrying tags/attributes/comments, one `blob` task per layer and for the model read). A `malleus` MWDB plugin creates the attribute definitions, grants the reporter account permission to set them, and enforces the `context` attribute and `source:` tag contract in post-commit hooks. Detonation is part 2; in this part `sandbox_mode` is `never`, so every analysis ends statically.

**Tech Stack:** Python 3.9+ (Malleus) / 3.10+ (MWDB plugin), karton-core 5.9.x, mwdb-core fork, Docker Compose, systemd.

**Spec:** `docs/superpowers/specs/2026-11-09-second-opinion-pipeline-design.md` (sections 4, 4.1–4.3, 5, 5.1, 8). Depends on the Malleus plan `docs/superpowers/plans/2026-11-09-stage-records-and-llm-provider.md` in the Malleus repo (`pipeline.Settings`, `run_static`, `finish`, `sandbox_decision`).

## Global Constraints

- Two repositories: Malleus (`/Users/fioa8c/WORK/Malleus-Monstrorum`, tasks 1–5) and this fork (tasks 6–8). Each task commits in its own repo.
- Malleus core stays dependency-free; the Karton layer is the optional extra `service = ["karton-core>=5.9,<6"]` and lives under `malleus/service/`.
- Attribute keys match `^[A-Za-z0-9_-]{1,32}$`: `malleus_verdict`, `malleus_confidence`, `malleus_reasons`, `malleus_stages`, `malleus_depth`, `malleus_gate`, `malleus_gate_disagreement`, `malleus_rules_commit`, `malleus_unlocked`, `malleus_model`, `malleus_prompt_sha256`, `malleus_llm`, `context`, and on blobs `layer_variant`, `layer_unlocked`. The list is defined once per repo (`malleus/service/payloads.py` and `docker/plugins/malleus/attributes.py`) and both files carry the comment `# keep in sync with <other path>`.
- Tags: `source:<caller>`, `verdict:<v>`, `yara:<rule>`, `wm:needs-review`, `wm:context-stripped`, `wm:source-stripped`. Verdict values: `known-bad`, `malicious`, `likely-malicious`, `likely-benign`, `undetermined`, `no-hit`, `not-applicable`. Untagged objects get no verdict and no attributes.
- Scanner sources: `("jetpack-scan", "wpcloud-scan", "elderward")`. Allowed `context` keys: `("scanner", "scanner_version", "rules", "path", "engine")`.
- Reporter payload shapes are those in spec section 4.3; `attributes` is `{key: [value, ...]}`.
- Services never execute sample content; `cx` runs only through `deobf.build_argv`'s hardened `docker run`.

## Review Focus

- A payload whose `sample` resource is larger than `pipeline.MAX_STATIC_BYTES`: verdict `undetermined` with the `too-large` stage reasons, no crash. Test in Task 4.
- An object re-uploaded by a scanner that already has `malleus_verdict`: MWDB spawns no task, so nothing runs; a reanalyze does run and must be treated as full depth. Test in Task 1.
- A `source:` tag with an unknown value (`source:foo`): treated as researcher depth, never as skipped. Test in Task 1.
- A `context` attribute that is not a JSON object (a bare string): left untouched, nothing stripped, no exception in the hook. Test in Task 6.
- The rules clone advancing between two tasks: the second task's `malleus_rules_commit` differs and the `.yarc` is recompiled once. Test in Task 4.

---

### Task 1: Source policy (Malleus)

**Files:**
- Create: `malleus/service/__init__.py`, `malleus/service/policy.py`
- Test: `tests/service/__init__.py`, `tests/service/test_policy.py`

**Interfaces:**
- Produces:
  ```python
  SCANNER_SOURCES = ("jetpack-scan", "wpcloud-scan", "elderward")
  def sources(tags: List[str]) -> List[str]                   # values after "source:"
  def depth_for(tags: List[str], attributes: Dict[str, list]) -> str   # "none" | "auto" | "full"
  def continue_after_gate(depth: str, g: Gate, caller_rules: List[str]) -> Tuple[bool, str]
  def gate_disagreement(hub_hits: Set[str], caller_rules: List[str]) -> Optional[dict]
  ```

- [ ] **Step 1: Write the failing tests**

```python
def test_depth_none_without_source_tag(): assert policy.depth_for(["foo"], {}) == "none"
def test_depth_auto_for_scanner_sources(): assert policy.depth_for(["source:jetpack-scan"], {}) == "auto"
def test_depth_full_for_unknown_source(): assert policy.depth_for(["source:foo"], {}) == "full"
def test_depth_full_when_verdict_already_present():  # reanalysis
    assert policy.depth_for(["source:jetpack-scan"], {"malleus_verdict": ["no-hit"]}) == "full"
def test_continue_none_is_not_requested(): assert policy.continue_after_gate("none", weak_gate, []) == (False, "not-requested")
def test_continue_known_bad_stops(): assert policy.continue_after_gate("auto", strong_gate, []) == (False, "known-bad")
def test_continue_auto_no_hits_no_caller_rules_is_no_hit(): assert policy.continue_after_gate("auto", empty_gate, []) == (False, "no-hit")
def test_continue_auto_caller_rules_override_empty_gate(): assert policy.continue_after_gate("auto", empty_gate, ["r"]) == (True, "caller reported r")
def test_continue_full_always_runs(): assert policy.continue_after_gate("full", empty_gate, []) == (True, "full depth")
def test_disagreement_none_when_equal(): assert policy.gate_disagreement({"r"}, ["r"]) is None
def test_disagreement_lists_both_sides(): assert policy.gate_disagreement(set(), ["r"]) == {"hub": [], "caller": ["r"]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/service/test_policy.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'malleus.service'`

- [ ] **Step 3: Implement `policy.py`**

`gate_disagreement` returns `None` when `caller_rules` is empty (nothing to compare).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/service/test_policy.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add malleus/service tests/service
git commit -m "Service: source policy decides analysis depth"
```

### Task 2: File-kind sniffing (Malleus)

**Files:**
- Create: `malleus/service/sniff.py`
- Test: `tests/service/test_sniff.py`

**Interfaces:**
- Produces: `def file_kind(name: str, head: bytes) -> Optional[str]` returning `"php" | "js" | "html" | "htaccess" | None`; `EXTENSIONS = {"php": sandbox.PHP_EXTENSIONS, "js": (".js",), "html": (".html", ".htm")}`; `.htaccess` by name; `<?php` or `<?=` in the first 256 bytes → `"php"`; `<html`/`<script` (case-insensitive) → `"html"`; otherwise `None`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("name,head,kind", [
    ("x.php", b"", "php"), ("x.PHTML", b"", "php"), ("x.txt", b"junk <?php eval(1);", "php"),
    ("x.js", b"", "js"), ("index.htm", b"", "html"), ("x.txt", b"<HTML>", "html"),
    (".htaccess", b"RewriteEngine on", "htaccess"), ("x.png", b"\x89PNG", None), ("x.txt", b"hello", None)])
def test_file_kind(name, head, kind): assert sniff.file_kind(name, head) == kind
```

- [ ] **Step 2: Run to verify failure** → `ModuleNotFoundError`
- [ ] **Step 3: Implement** (pure function, no I/O)
- [ ] **Step 4: Run to verify pass**
- [ ] **Step 5: Commit** `git commit -m "Service: file-kind sniffing for PHP, JS, HTML and .htaccess"`

### Task 3: Reporter payload builders (Malleus)

**Files:**
- Create: `malleus/service/payloads.py`
- Test: `tests/service/test_payloads.py`

**Interfaces:**
- Consumes: `pipeline.StaticResult`, `verdict.Verdict`, `deobf.Layer`, `karton.core.Task`, `karton.core.ResourceBase`.
- Produces:
  ```python
  ATTRIBUTE_KEYS: Tuple[str, ...]        # the Global Constraints list; keep in sync with docker/plugins/malleus/attributes.py
  REVIEW_VERDICTS = ("likely-malicious", "likely-benign", "undetermined")
  def sample_task(sample: ResourceBase, tags: List[str], attributes: Dict[str, list], comments: List[str]) -> Task
      # Task({"type": "sample", "stage": "analyzed"}, payload={"sample","tags","attributes","comments"})
  def blob_task(kind: str, name: str, content: str, parent_sha256: str,
                tags: List[str], attributes: Dict[str, list]) -> Task
      # Task({"type": "blob", "kind": kind}, payload={"name","content","parent","tags","attributes"})
  def layer_blob_tasks(res: StaticResult) -> List[Task]
      # one per res.deobf.layers: kind "deobfuscated-php", name f"{sha256[:12]}-{variant}.php",
      # content read with errors="replace", attributes {"layer_variant": [variant], "layer_unlocked": sorted(res.deobf.layer_hits[variant] - set(res.hits))}
  def llm_blob_task(res: StaticResult) -> Optional[Task]
      # kind "llm-read", name f"{sha256[:12]}-llm.json", content json.dumps({"request": res.llm_messages, "result": res.llm.as_dict()}, indent=2, sort_keys=True)
  def verdict_payload(res: StaticResult, v: Verdict, *, depth: str, rules_commit: str,
                      disagreement: Optional[dict]) -> Tuple[List[str], Dict[str, list], List[str]]
      # tags: f"verdict:{v.verdict}", f"yara:{r}" for r in res.hits, "wm:needs-review" when v.verdict in REVIEW_VERDICTS
      # attributes: malleus_verdict, malleus_confidence (str), malleus_reasons (list of str, one value each),
      #   malleus_stages ([res.stages] as one JSON value), malleus_depth, malleus_gate (strong/weak/none),
      #   malleus_rules_commit, malleus_unlocked (one value per rule), malleus_gate_disagreement ([disagreement]) when not None,
      #   malleus_model / malleus_prompt_sha256 / malleus_llm ([res.llm.parsed]) when res.llm is ok
      # comments: ["Malleus verdict: <verdict> (<confidence>)\n- reason\n- reason"]
  def terminal_payload(verdict: str, depth: str, reason: str, hits: List[str] = ()) -> Tuple[List[str], Dict[str, list], List[str]]
      # for not-applicable / no-hit / known-bad shortcuts: tags [f"verdict:{verdict}"] + [f"yara:{r}" for r in hits],
      # attributes {"malleus_verdict": [verdict], "malleus_depth": [depth], "malleus_reasons": [reason], "malleus_gate": [...]}, comments [reason]
  ```

- [ ] **Step 1: Write the failing tests**

```python
def test_sample_task_headers_and_payload():
    t = payloads.sample_task(res_obj, ["a"], {"k": ["v"]}, ["c"])
    assert t.headers == {"type": "sample", "stage": "analyzed"} and t.payload["attributes"] == {"k": ["v"]}
def test_blob_task_carries_parent_hash(): assert t.payload["parent"] == "ab" * 32 and t.headers["kind"] == "deobfuscated-php"
def test_layer_blob_tasks_one_per_layer_with_unlocked(tmp_path): ...  # two layers, second unlocks "strong_r"
def test_verdict_payload_tags_and_review_flag():
    tags, attrs, comments = payloads.verdict_payload(res, Verdict("undetermined", 0.4, ["llm: amber"], res.stages), depth="auto", rules_commit="abc", disagreement=None)
    assert "verdict:undetermined" in tags and "wm:needs-review" in tags and attrs["malleus_rules_commit"] == ["abc"]
    assert comments[0].startswith("Malleus verdict: undetermined")
def test_verdict_payload_includes_disagreement(): assert attrs["malleus_gate_disagreement"] == [{"hub": [], "caller": ["r"]}]
def test_attribute_keys_cover_everything_emitted():  # every key used by verdict_payload/terminal_payload/layer attrs is in ATTRIBUTE_KEYS
```

- [ ] **Step 2: Run to verify failure** → `ModuleNotFoundError`
- [ ] **Step 3: Implement `payloads.py`**
- [ ] **Step 4: Run to verify pass**
- [ ] **Step 5: Commit** `git commit -m "Service: reporter payload builders"`

### Task 4: `malleus-static` Karton consumer (Malleus)

**Files:**
- Create: `malleus/service/static.py`, `malleus/service/__main__.py`
- Modify: `pyproject.toml` (`[project.optional-dependencies] service = ["karton-core>=5.9,<6"]`, `[project.scripts] malleus-static = "malleus.service.static:main"`)
- Test: `tests/service/test_static.py` (uses `karton.core.test.KartonTestCase`; skipped with `pytest.importorskip("karton.core")`)

**Interfaces:**
- Consumes: Task 1–3 functions; `pipeline.Settings`, `run_static`, `sandbox_decision`, `finish`; `rules.compile_rules`, `rules.scan`, `gate.classify`.
- Produces:
  ```python
  class MalleusStatic(Karton):
      identity = "karton.malleus-static"
      filters = [{"type": "sample", "stage": "recognized"}, {"type": "sample", "stage": "unrecognized"}]
      def __init__(self, config=None, identity=None, backend=None, *,
                   settings: Optional[pipeline.Settings] = None, work_root: Optional[Path] = None,
                   scanner=rules.scan, compiler=rules.compile_rules, rules_commit_fn: Optional[Callable[[], str]] = None)
      def process(self, task: Task) -> None
  def settings_from_config(config: Config) -> pipeline.Settings
      # [malleus] rules_dir, phpdeobf_dir, kadath_dir, use_x (default 1), sandbox_mode (default "never"),
      # llm_provider, llm_url, llm_model, llm_api_key, llm_schema_mode (defaults as in malleus.config)
  def git_head(path: Path, runner=procs.run_command) -> str   # "git -C <path> rev-parse HEAD" or "unknown"
  def main() -> None                                         # MalleusStatic.main()
  ```
- Depth `none` (no `source:` tag) emits **nothing**: the service logs and returns, so threat-library syncs and bulk imports leave no trace. Every other outcome writes a sample task.
- `process` order: depth → kind → write sample to `work_root/<sha256>/sample.<ext>` → recompile rules if `git_head` changed → `scanner` → `gate.classify` → `continue_after_gate` → `run_static` → emit. Emitted tasks, in order: the `analyzed` sample task **first** (so the Karton analysis is attached to the object and the reporter account can see it before children arrive), then layer blobs, then the llm blob. When `sandbox_decision` is `(True, _)` and `settings.sandbox_mode != "never"`, the sample task is not emitted; instead emit the blobs and then `task.derive_task({"type": "sample", "stage": "malleus-detonate"})` with `add_payload("malleus_static", res.as_dict())` and `add_payload("malleus_depth", depth)`; the sample task is then part 2's job. Part 2 note: `sandbox_decision` checks `settings.kadath_dir` locally, so on the worker both services see the same checkout; if they are ever split across hosts the check moves to the detonate service. The per-sample work dir is removed in a `finally`.

- [ ] **Step 1: Write the failing tests**

```python
class StaticCase(KartonTestCase):
    karton_class = MalleusStatic
    kwargs = {"settings": FAKE_SETTINGS, "work_root": TMP, "scanner": fake_scanner, "compiler": fake_compiler, "rules_commit_fn": lambda: "abc"}

    def _run(self, name, content, tags, attributes=None):
        return self.run_task(Task({"type": "sample", "stage": "recognized"},
                                  payload={"sample": Resource(name, content), "tags": tags, "attributes": attributes or {}}))

    def test_untagged_sample_emits_nothing(self):
        out = self._run("a.php", b"<?php echo 1;", [])
        assert out == [] and self.scanner_calls == 0
    def test_binary_sample_is_not_applicable(self): ...  # "a.png", tags ["source:researcher"] -> verdict:not-applicable
    def test_scanner_strong_hit_is_known_bad(self): ...  # fake_scanner -> {"strong_r"}; tags include "yara:strong_r"; attributes["malleus_depth"] == ["auto"]
    def test_scanner_weak_hit_settled_by_deobf_emits_layer_blob_then_sample(self):
        # FAKE_SETTINGS.sweeper writes a layer and returns settled; expect [sample(analyzed), blob(deobfuscated-php)] with verdict:malicious
        assert [t.headers["type"] for t in out] == ["sample", "blob"] and out[1].payload["parent"] == sha
    def test_scanner_no_hub_hit_with_caller_rules_continues_and_records_disagreement(self):
        # attributes {"context": [{"rules": ["weak_r"]}]}, fake_scanner -> set()
        assert out[-1].payload["attributes"]["malleus_gate_disagreement"] == [{"hub": [], "caller": ["weak_r"]}]
    def test_researcher_no_hits_runs_full_depth(self): ...  # tags ["source:researcher"], scanner -> set(); llm blob + sample emitted, malleus_depth == ["full"]
    def test_too_large_sample_is_undetermined(self): ...  # content of MAX_STATIC_BYTES + 1 bytes starting with b"<?php"
    def test_rules_recompiled_when_head_changes(self):  # rules_commit_fn returns "a" then "b"; compiler called twice across two runs
    def test_detonate_task_emitted_when_sandbox_wanted(self):  # settings.sandbox_mode = "auto", asker returns green, and settings.kadath_dir / "bin" / "kadath" exists under tmp (as make_ctx in tests/test_cli.py does) or sandbox_decision is False; expect no sample task and the last task headers {"type":"sample","stage":"malleus-detonate"} with payload "malleus_static"
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev,service]' && .venv/bin/python -m pytest tests/service/test_static.py -q` (use `.venv/bin/python` for every later test run in this repo)
Expected: FAIL with `ImportError: cannot import name 'MalleusStatic'`

- [ ] **Step 3: Implement `static.py` and `__main__.py`**

`settings_from_config` builds `Settings` with `poster=llm.poster_for(...)` and the module defaults for `runner`/`sweeper`/`asker`/`offerer`. The sample's extension comes from `sniff` (`php`→`.php`, `js`→`.js`, `html`→`.html`, `htaccess`→`.htaccess`).

- [ ] **Step 4: Run the whole suite** → `python3 -m pytest tests -q` PASS
- [ ] **Step 5: Commit** `git commit -m "Service: malleus-static Karton consumer"`

### Task 5: Worker host install files (Malleus)

**Files:**
- Create: `deploy/worker/README.md`, `deploy/worker/karton.ini.example`, `deploy/worker/malleus-static.service`, `deploy/worker/rules-sync.service`, `deploy/worker/rules-sync.timer`, `deploy/worker/rules-sync.sh`

**Interfaces:**
- Produces: the systemd unit runs `/opt/malleus/venv/bin/malleus-static --config-file /etc/karton/karton.ini` as user `malleus` (member of `docker`), `Restart=on-failure`, `EnvironmentFile=/etc/malleus/env` (holds `MALLEUS_LLM_API_KEY` when used); `rules-sync.sh` does `git -C /opt/rules/jetpack-scan-yara-rules pull --ff-only` (clone on first run from `RULES_REPO`), timer `OnUnitActiveSec=15min`.
- `karton.ini.example` sections: `[redis] host=<vpc-ip> port=6379 password=...`, `[s3] address=http://<vpc-ip>:9000 access_key secret_key bucket=karton`, `[malleus] rules_dir=/opt/rules/jetpack-scan-yara-rules/signatures/YARA phpdeobf_dir=/opt/PHPDeobfuscator kadath_dir=/opt/KadathSandbox use_x=1 sandbox_mode=never llm_provider=ollama llm_url=http://127.0.0.1:11434 llm_model=hf.co/ree2raz/CyberSecQwen-4B-GGUF:Q4_K_M`.

- [ ] **Step 1: Write the files** (README lists host packages: `python3-venv`, `php-cli`, `composer`, `docker`, `git`, `yr` from yara-x releases, `ollama`; the PHPDeobfuscator checkout with `composer install` and `docker build -t phpdeobf .`; the Kadath checkout; `pip install '/opt/malleus[service]'`).
- [ ] **Step 2: Verify** `systemd-analyze verify deploy/worker/*.service deploy/worker/*.timer` on a Linux host, or `bash -n deploy/worker/rules-sync.sh` locally. Expected: no output.
- [ ] **Step 3: Commit** `git commit -m "Deploy: worker host units for malleus-static and rules-sync"`

### Task 6: `malleus` MWDB plugin (this fork)

**Files:**
- Create: `docker/plugins/malleus/{__init__.py,attributes.py,context_policy.py,hooks.py,pyproject.toml,README.md}`, `docker/plugins/malleus/tests/{__init__.py,conftest.py,test_context_policy.py,test_hooks.py,test_attributes.py}`
- Modify: `compose/compose.with-plugins.yml` (`MWDB_PLUGINS: "yarax_regex,phpdeobf,malleus"`), `CLAUDE.md` plugin list

**Interfaces:**
- Consumes: `mwdb.core.hooks.HookHandler`, `Object.add_attribute` / `remove_attribute` / `add_tag` / `remove_tag` (`mwdb/model/object.py:656-870`), `AttributeDefinition`, `AttributePermission`, `Group`, `flask.g.auth_user`.
- Produces:
  ```python
  # attributes.py
  ATTRIBUTE_KEYS, DESCRIPTIONS                               # keep in sync with Malleus malleus/service/payloads.py
  def ensure_attribute_definitions() -> None                 # idempotent, as wpsandbox
  def ensure_group_permissions(set_groups: List[str], read_groups: List[str]) -> None
      # set_groups (the reporter account): AttributePermission(key, group.id, can_read=True, can_set=True) for every key
      # read_groups (scanner accounts): can_read=True, can_set=False for every key except `context`, which gets can_set=True so they can submit it
      # idempotent; a group that does not exist is logged and skipped
  # context_policy.py
  ALLOWED_CONTEXT_KEYS = ("scanner", "scanner_version", "rules", "path", "engine")
  SCANNER_SOURCES = ("jetpack-scan", "wpcloud-scan", "elderward")
  def filter_context(value: Any) -> Tuple[Any, List[str]]    # non-dict -> (value, []); dict -> (kept, sorted stripped keys)
  def source_tag_allowed(tag: str, login: str) -> bool       # not a source: tag -> True; login not in SCANNER_SOURCES -> True; else tag == f"source:{login}"
  # hooks.py
  class MalleusHookHandler(HookHandler):
      def on_created_attribute(self, object, attribute) -> None  # key == "context": strip, re-add, tag wm:context-stripped, commit
      def on_created_tag(self, object, tag) -> None              # not allowed: remove tag, add wm:source-stripped, commit
  # __init__.py entrypoint: ensure definitions; ensure_group_permissions(split(os.environ.get("MWDB_MALLEUS_SET_GROUPS", "karton")), split(os.environ.get("MWDB_MALLEUS_READ_GROUPS", "jetpack-scan,wpcloud-scan,elderward"))); register_hook_handler(MalleusHookHandler)
  ```

- [ ] **Step 1: Write the failing tests** (conftest copied from `docker/plugins/phpdeobf/tests/conftest.py`, stubbing `mwdb.*` with MagicMock)

```python
def test_filter_context_strips_unknown_keys():
    assert context_policy.filter_context({"scanner": "v2", "site_id": 1}) == ({"scanner": "v2"}, ["site_id"])
def test_filter_context_leaves_non_dict(): assert context_policy.filter_context("x") == ("x", [])
def test_source_tag_rules(): scanner login may only set its own; researcher login may set any; non-source tags always allowed
def test_hook_strips_context_and_tags(monkeypatch):  # MagicMock object; assert remove_attribute/add_attribute/add_tag("wm:context-stripped") calls
def test_hook_ignores_clean_context(): no calls on the object
def test_hook_removes_foreign_source_tag(monkeypatch):  # g.auth_user.login = "jetpack-scan", tag "source:wpcloud-scan"
def test_ensure_group_permissions_skips_missing_group(): logs, no session.add
def test_read_group_can_set_only_context(): rows for a read group have can_set True only for key "context"
```

- [ ] **Step 2: Run to verify failure** → `cd docker/plugins/malleus && python3 -m pytest -q` → `ModuleNotFoundError`
- [ ] **Step 3: Implement the plugin** (dual layout like `phpdeobf`; no npm package in this part, so no `package.json`)
- [ ] **Step 4: Run to verify pass**; also `ruff check docker/plugins/malleus`
- [ ] **Step 5: Commit** `git commit -m "Plugin malleus: attribute definitions, reporter permissions, context and source hooks"`

### Task 7: Karton in the production compose and deployment runbook (this fork)

**Files:**
- Modify: `docker-compose-prod.yml`, `deploy/DEPLOYMENT.md`, `.env.example` (or the file `gen_vars.sh` produces for prod)
- Create: `deploy/karton.ini.example`

**Interfaces:**
- Produces, in `docker-compose-prod.yml`: `MWDB_ENABLE_KARTON=1` and `MWDB_PLUGINS=yarax_regex,phpdeobf,malleus` on `mwdb`; `./karton.ini:/etc/karton/karton.ini:ro` mounted on `mwdb`, `karton-system-router`, `karton-system-gc`, `karton-classifier`, `karton-mwdb-reporter` (same images and commands as `compose/compose.with-karton.yml`); `silo` from `compose/compose.silo.yml`; `redis` with `command: redis-server --requirepass ${REDIS_PASSWORD}` and `ports: ["${VPC_BIND_IP}:6379:6379"]`; `silo` with `ports: ["${VPC_BIND_IP}:9000:9000"]`; reporter credentials in `karton.ini` `[mwdb] api_url = http://mwdb.:8080/api/` and `api_key = <key of the karton user>` (env form `KARTON_MWDB_API_KEY`), no admin password.
- `deploy/DEPLOYMENT.md` gains a section "Karton and the worker droplet": create the `karton` user and API key, grant its group the capabilities `access_all_objects`, `adding_tags`, `adding_comments`, `adding_parents`, `adding_files`, `adding_blobs` (attribute rights come from the plugin's `AttributePermission` rows); create one account per scanner (`jetpack-scan`, `wpcloud-scan`, `elderward`) with `adding_tags` only, and give the researchers' group `reading_all_attributes`; set `MWDB_MALLEUS_SET_GROUPS=karton` and `MWDB_MALLEUS_READ_GROUPS=jetpack-scan,wpcloud-scan,elderward`; leave `MWDB_SANDBOX_URL` unset on prod so the synchronous PHP normalize hook does not run per upload (it would add a `normalized-php` child beside the pipeline's layers), firewall rules allowing 6379 and 9000 only from the worker's VPC address, and the worker install pointer to the Malleus repo `deploy/worker/README.md`.

- [ ] **Step 1: Edit the compose and env files**
- [ ] **Step 2: Verify** `docker compose -f docker-compose-prod.yml config -q` with a stub `.env` and `karton.ini` → exit 0
- [ ] **Step 3: Write the runbook section**
- [ ] **Step 4: Commit** `git commit -m "Prod compose: Karton stack, VPC-bound Redis and S3, malleus plugin"`

### Task 8: End-to-end on the dev stack (this fork, manual verification)

**Files:**
- Create: `dev/malleus-smoke.py` (mwdblib script: upload `tests/fixtures/malleus/weak.php` with tag `source:researcher`, poll `object.attributes` up to 10 min, print `malleus_verdict` and children names, exit 1 on timeout), `tests/fixtures/malleus/weak.php` (a benign file that matches one exploratory rule from the rules repo, chosen when the task runs; record the rule name in the file header comment)
- Create: `compose/compose.with-malleus-dev.yml` exposing `127.0.0.1:6379:6379` on `redis` (`compose.silo.yml` already publishes `127.0.0.1:9000`)

- [ ] **Step 1:** `./gen_vars.sh && ./compose.sh --with dev --with plugins --with karton --with malleus-dev up -d`
- [ ] **Step 2:** On the laptop, `pip install -e '/Users/fioa8c/WORK/Malleus-Monstrorum[service]'`, write `~/.config/karton/karton.ini` with `redis host=127.0.0.1`, `s3 address=http://127.0.0.1:9000` (keys from `dev/karton.ini`), `[malleus]` pointing at the local checkouts with `sandbox_mode=never`; run `malleus-static`.
- [ ] **Step 3:** `python3 dev/malleus-smoke.py` → prints `malleus_verdict: ...` and at least one child blob named `*-llm.json`; exit 0.
- [ ] **Step 4:** Open the sample page: Karton analysis shows `karton.malleus-static`; the `verdict:*` tag and `malleus_*` attributes render; the layer blob opens.
- [ ] **Step 5: Commit** `git commit -m "Dev: malleus smoke script and Redis exposure overlay"`
