# Second-Opinion Pipeline, Part 2: `malleus-detonate` and the Worker Droplet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A researcher upload to MWDB ends with a Kadath sandbox report child, `sandbox_*` attributes and one current `verdict:*` tag, produced on a new DigitalOcean worker droplet; reanalyses replace the previous verdict instead of piling up beside it, and a crashed analysis still leaves a verdict.

**Architecture:** `malleus-static` (Part 1) hands PHP samples that need runtime evidence to a `{"type": "sample", "stage": "malleus-detonate"}` task. A new Karton consumer, `malleus-detonate`, runs as a systemd host process on the worker. It rebuilds the `StaticResult` from the payload, runs one Kadath offering at a time behind a blocking host lock, merges the verdict with Malleus's unchanged `finish`, and emits the `analyzed` sample task plus a `sandbox-report` blob for karton-mwdb-reporter. Every sample task now carries a per-analysis marker attribute (`malleus_analysis` = Karton root uid). The fork's `malleus` plugin uses that marker to clear the previous analysis's `malleus_*`/`sandbox_*` values, and uses `verdict:*` tag creation to clear the previous verdict tag.

**Tech Stack:** Python 3.9+ (Malleus, stdlib only in core) / 3.10+ (MWDB plugin), karton-core 5.9.x, KadathSandbox (Docker Compose v2), systemd, DigitalOcean (droplet, VPC, Cloud Firewall).

**Spec:** `docs/superpowers/specs/2026-11-09-second-opinion-pipeline-design.md`: sections 4.1, 4.3, 5 ("detonate" row), 6, 8 and 10 (phase 2). Also read the Part 1 plan `docs/superpowers/plans/2026-11-09-second-opinion-1-karton-static.md` and its rulings `docs/superpowers/plans/2026-11-10-second-opinion-1-execution-rulings.md` ("Part 2 must do first"). Both repos are on trunk/master with Part 1 merged (Malleus `fca4a8b`, fork `e991bbb`).

## Scope rulings (decided while planning; reviewers check against these)

1. **Full depth always detonates.** Spec section 5 says detonation "runs at full depth, or when static stages did not settle and the model said green/amber/error". `should_run("auto", ...)` skips on a red model read or a settled deobfuscation. So `malleus-static` sets `res.sandbox_mode = "always"` when the depth is `full` and the configured mode is `auto`. `never` stays `never`, known-bad and too-large still never detonate, and `verdict.merge` treats `always` like `auto`, so verdicts are unchanged.
2. **Blob kind `sandbox-report`, name `<sha256[:12]>-kadath-summary.json`.** The spec fixes the MWDB `blob_type` (the reporter uses the task's `kind` as the blob type); the brief's "kadath-summary" becomes the file name.
3. **Reanalysis cleanup uses an analysis marker, not "remove older values per key".** The reporter (`karton-mwdb-reporter` 1.3.0, read on 2026-11-10) posts tags first, then attributes one value per request, skipping values already present. Karton serialises payloads with `sort_keys=True`, so the reporter sees attributes alphabetically. A per-key rule would delete the sibling values of multi-valued keys (`malleus_reasons`, `malleus_unlocked`, `sandbox_network`). It would also never fire for unchanged values and never clear keys the new analysis omits (`malleus_unlocked`, `malleus_gate_disagreement`, `malleus_llm`, `sandbox_*`). Instead:
   - Every sample task carries `malleus_analysis = [task.root_uid]`. It is always new and sorts first among emitted keys.
   - When a reporter login creates it, the plugin removes every other pipeline-owned attribute value on the object.
   - When a reporter login creates a `verdict:*` tag, the plugin removes the other `verdict:*` tags and `wm:needs-review`. The reporter re-adds `wm:needs-review` right after, when the new verdict needs review.
   - `yara:*` tags are left alone (other analyzers use that prefix); stale `yara:*` after a rules change is an accepted residual.
4. **Kadath serialisation.** Kadath's own lock (`kadath/run.py:580`) is non-blocking: a second offering exits rc 2 "another offering is in progress". `malleus-detonate` therefore takes a **blocking** `fcntl` lock on its own file (`/var/lib/malleus/kadath.lock`, never Kadath's `offer.lock`) around each offering. An offering that still hits Kadath's lock (someone ran `bin/kadath` by hand on the worker) becomes a sandbox `error` stage, verdict `undetermined` + `wm:needs-review`, fixed by Reanalyze. The README reserves the worker's Kadath for the service.
5. **Timeouts stop Kadath cleanly.** `procs._kill` sends SIGKILL to the direct child only. Kadath runs every `make`/`docker` call in its own process group and kills those groups on `KeyboardInterrupt` (`kadath/shell.py`). A timed-out offering therefore gets SIGINT first, then a 60 s grace, then SIGKILL. Offer timeout `sandbox_timeout = 600`.
6. **A crash leaves a verdict.** Both consumers wrap their work. On an unexpected exception they emit `verdict:undetermined` + `wm:needs-review`, with reason `analysis crashed: <ExceptionType>` (type only, never the message, which can carry sample text), then re-raise so Karton marks the task crashed. They emit nothing if the sample task was already sent, and nothing at depth `none`. SIGKILL/OOM bypass this; the runbook documents Reanalyze for an analysis left without a verdict.
7. **Deferred, with reasons:**
   - Dropped files as child Files: Kadath's summary lists paths, not content, so fetching them needs a Kadath change.
   - `wpsandbox_run` rows with a `backend` column: the `wpsandbox` branch is not merged into the fork's `master`.
   - Polyglot sniffing, the single-alembic-head CI check, and `GET /api/secondopinion`: not in this part's brief.
   - The `pg_dump` before the first prod deploy **is** done here (Task 10).

## Global Constraints

- Two repositories: Malleus (`/Users/fioa8c/WORK/Malleus-Monstrorum`, Tasks 1–6) and this fork (Tasks 7–10). Each task commits in its own repo on a feature branch (`feature/detonate` in both); nothing is pushed or merged without the user.
- Malleus core (`malleus/*.py`) stays dependency-free: stdlib only (`fcntl`, `signal` are fine). Karton code lives under `malleus/service/` behind the `service` extra.
- Sample content is never executed outside Kadath or the hardened `cx` container. `malleus-detonate` writes the sample to disk and hands its path to `bin/kadath`; nothing else runs it.
- Secrets only in env files (`/etc/malleus/env`, the fork's `.env`), never in `karton.ini` or the repos.
- Attribute keys match `^[A-Za-z0-9_-]{1,32}$`. New keys this part: `malleus_analysis`, `sandbox_level`, `sandbox_coverage`, `sandbox_network`, `sandbox_files_written`, `sandbox_dangerous_calls`. Each goes into **both** `ATTRIBUTE_KEYS` lists (`malleus/service/payloads.py` and `docker/plugins/malleus/attributes.py`, which carry `# keep in sync with` comments) and into the fork's `DESCRIPTIONS`.
- `PIPELINE_KEYS` (cleared on reanalysis) = `ATTRIBUTE_KEYS` minus `context`, `layer_variant`, `layer_unlocked`.
- Tags: `verdict:<v>` with `v` in `known-bad, malicious, likely-malicious, likely-benign, undetermined, no-hit, not-applicable`; `wm:needs-review` on `likely-malicious, likely-benign, undetermined`.
- Run Malleus tests with `.venv/bin/python -m pytest` (the venv has `.[dev,service]`, karton-core 5.9.1); fork plugin tests with `cd docker/plugins/malleus && python3 -m pytest -q`.

## Review Focus

- **A reanalysis whose verdict and most values are unchanged.** Afterwards each `PIPELINE_KEYS` key holds only the new analysis's values, exactly one `malleus_analysis` value (the new root uid), one `verdict:*` tag, and no key the new analysis omitted. Tests in Task 3 (marker sorts first) and Task 7 (hooks); end to end in Task 8.
- **Kadath busy or hung.** An offering that returns rc 2, or exceeds `sandbox_timeout`, ends as `verdict:undetermined` with a `sandbox: error: ...` reason, not a crashed task. The timed-out Kadath process gets SIGINT before SIGKILL. Tests in Task 2 and Task 5.
- **A crash after the sample task was sent** (e.g. sending the first blob fails after the verdict went out). No second verdict is emitted; the task still crashes. Tests in Task 4 and Task 5.
- **A detonate payload in either shape.** Part 1's shape (`llm.raw` present, inherited `attributes`, no `malleus_disagreement`) and Part 2's are both accepted, with the same verdict. Tests in Task 1 and Task 5.
- **Leftover work directories after a SIGKILL.** The start-up sweep removes only directories named like a Karton task uid (UUID), never `rules.yarc`, `kadath.lock` or anything else under the state directory. Test in Task 3.

---

### Task 1: `StaticResult.from_dict` and friends (Malleus)

**Files:**
- Modify: `malleus/gate.py`, `malleus/deobf.py`, `malleus/llm.py`, `malleus/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces:
  ```python
  Gate.from_dict(d: dict) -> Gate                 # lists "strong", "weak", "unknown"; missing -> []
  DeobfResult.from_dict(d: Optional[dict]) -> Optional[DeobfResult]
      # layers -> [Layer(**l)], layer_hits -> {k: set(v)}, unlocked -> set, variants and settled as given
  LLMResult.from_dict(d: Optional[dict]) -> Optional[LLMResult]   # "raw" missing -> ""
  StaticResult.from_dict(d: dict) -> StaticResult
      # restores sha256, path, hits, is_php, too_large, gate, deobf, llm, stages, sandbox_mode, forced
      # (missing "forced" -> False); llm_messages is always None; unknown extra keys are ignored
  ```

- [ ] **Step 1: Write the failing tests.** Build the results with `run_static` and the fakes already used in `tests/test_pipeline.py`. One per scenario: weak hit with llm ok; deobf settled; forced empty gate; llm error; too-large; non-PHP.

```python
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_static_result_json_round_trip(tmp_path, scenario):
    res = scenario(tmp_path)
    d = json.loads(json.dumps(res.as_dict()))
    back = pipeline.StaticResult.from_dict(d)
    assert back.as_dict() == d
    assert back.llm_messages is None
    assert pipeline.sandbox_decision(back, settings) == pipeline.sandbox_decision(res, settings)
    stage = {"status": "skipped", "reason": "x", "duration": 0.0}
    assert pipeline.finish(back, None, dict(stage)).as_dict() == pipeline.finish(res, None, dict(stage)).as_dict()

def test_from_dict_without_raw_and_forced():
    d = json.loads(json.dumps(res.as_dict())); del d["llm"]["raw"]; del d["forced"]
    back = pipeline.StaticResult.from_dict(d)
    assert back.llm.raw == "" and back.forced is False

def test_from_dict_restores_sets():
    back = pipeline.StaticResult.from_dict(settled_dict)
    assert back.deobf.unlocked == {"strong_r"} and back.deobf.layer_hits == {"c": {"strong_r"}}
```

- [ ] **Step 2: Run to verify failure.** `.venv/bin/python -m pytest tests/test_pipeline.py -q` → `AttributeError: type object 'StaticResult' has no attribute 'from_dict'`
- [ ] **Step 3: Implement** the four `from_dict` classmethods (`@classmethod`, no new imports in `gate.py`/`llm.py`).
- [ ] **Step 4: Run the whole suite.** `.venv/bin/python -m pytest tests -q` → PASS
- [ ] **Step 5: Commit** `git commit -m "Pipeline: StaticResult.from_dict restores a result from its payload"`

### Task 2: Interruptible Kadath offers and a host lock (Malleus)

**Files:**
- Modify: `malleus/procs.py`, `malleus/sandbox.py`, the fake runners in `tests/test_sandbox.py` and `tests/test_cli.py` (accept `**kwargs`)
- Test: `tests/test_procs.py`, `tests/test_sandbox.py`

**Interfaces:**
- Produces:
  ```python
  def run_command(argv, timeout, cwd=None, max_output=20 * 1024 * 1024, stdin_bytes=None, *,
                  stop_signal: Optional[int] = None, stop_grace: float = 0.0) -> CommandResult
      # on timeout: when stop_signal is set, send it, wait up to stop_grace for exit, then _kill; result unchanged (timed_out=True)
  class HostLock:
      def __init__(self, path: Path) -> None
      def __enter__(self) -> "HostLock"   # opens path (creates, mode 0600), fcntl.flock(LOCK_EX) blocking
      def __exit__(self, *exc) -> None    # LOCK_UN and close
  OFFER_STOP_GRACE = 60.0                 # in sandbox.py
  def offer(sample, kadath_dir, runner=run_command, timeout: float = 600) -> SandboxResult
      # calls runner(argv, timeout, str(kadath_dir), 20 MiB, None, stop_signal=signal.SIGINT, stop_grace=OFFER_STOP_GRACE)
  ```

- [ ] **Step 1: Write the failing tests**

```python
def test_timeout_sends_stop_signal_before_kill(tmp_path):
    marker = tmp_path / "got-sigint"
    script = tmp_path / "child.py"   # installs a SIGINT handler that writes `marker` and exits 0; then sleeps 30 s
    r = procs.run_command([sys.executable, str(script), str(marker)], 0.5, stop_signal=signal.SIGINT, stop_grace=5)
    assert r.timed_out and marker.exists()

def test_stop_grace_bounded_when_child_ignores_signal(tmp_path):
    # child ignores SIGINT and sleeps 30 s
    r = procs.run_command([...], 0.5, stop_signal=signal.SIGINT, stop_grace=1)
    assert r.timed_out and r.duration < 5

def test_host_lock_blocks_second_holder(tmp_path):
    # thread A holds HostLock(p) for 0.5 s; thread B's `with HostLock(p)` enters only after A leaves (timestamps)

def test_offer_asks_runner_for_sigint_stop(tmp_path):
    seen = {}
    def runner(argv, timeout, cwd, max_output, stdin, **kw): seen.update(kw); return busy_result
    sandbox.offer(sample, kadath_dir, runner=runner)
    assert seen == {"stop_signal": signal.SIGINT, "stop_grace": sandbox.OFFER_STOP_GRACE}

def test_offer_busy_kadath_is_an_error_result(tmp_path):
    # runner returns rc 2, stderr "error: another offering is in progress"
    r = sandbox.offer(sample, kadath_dir, runner=runner)
    assert r.status == "error" and "another offering is in progress" in r.reason
```

- [ ] **Step 2: Run to verify failure.** `.venv/bin/python -m pytest tests/test_procs.py tests/test_sandbox.py -q` → `TypeError: ... unexpected keyword argument 'stop_signal'`
- [ ] **Step 3: Implement** `stop_signal`/`stop_grace` in the `TimeoutExpired` branch (`proc.send_signal`, `proc.wait(stop_grace)`, then `_kill`), `HostLock` in `procs.py`, and the `offer` change. Update existing fake runners to take `**kwargs`.
- [ ] **Step 4: Run the whole suite** → PASS, including `tests/test_cli.py` and `tests/test_golden.py` unchanged in behaviour
- [ ] **Step 5: Commit** `git commit -m "Procs: stop Kadath with SIGINT before SIGKILL; blocking host lock"`

### Task 3: Payload builders for Part 2 (Malleus)

**Files:**
- Modify: `malleus/service/payloads.py`, `malleus/service/static.py` (only: pass `analysis=task.root_uid` at its three payload call sites, so the suite stays green)
- Create: `malleus/service/common.py`
- Test: `tests/service/test_payloads.py`, `tests/service/test_common.py`

**Interfaces:**
- Consumes: `StaticResult` (Task 1), `sandbox.SandboxResult`.
- Produces:
  ```python
  # payloads.py
  ATTRIBUTE_KEYS  # + "malleus_analysis", "sandbox_level", "sandbox_coverage", "sandbox_network",
                  #   "sandbox_files_written", "sandbox_dangerous_calls"
  ANALYSIS_KEY = "malleus_analysis"
  MAX_SUMMARY_BYTES = 4 * 1024 * 1024
  def verdict_payload(res, v, *, depth, rules_commit, disagreement, analysis: str) -> Payload        # + malleus_analysis
  def terminal_payload(verdict, depth, reason, hits=(), *, rules_commit, disagreement=None, analysis: str) -> Payload
  def crash_payload(depth: str, exc: BaseException, *, rules_commit: Optional[str], analysis: str) -> Payload
      # tags ["verdict:undetermined", "wm:needs-review"]; attributes malleus_analysis, malleus_verdict ["undetermined"],
      # malleus_depth, malleus_reasons [f"analysis crashed: {type(exc).__name__}"], malleus_rules_commit [rules_commit or "unknown"];
      # comments ["Malleus verdict: undetermined (analysis crashed: <Type>). Press Reanalyze to retry."]
  def static_payload(res: StaticResult) -> dict        # res.as_dict() with llm["raw"] removed
  def sandbox_attributes(sb: Optional[SandboxResult]) -> Dict[str, list]
      # {} unless sb.status == "ok" and sb.evidence; else sandbox_level [level], sandbox_coverage [coverage],
      # sandbox_network: one value per non-wp_core flow host and per dropped "dst:port" (sorted, omitted when empty),
      # sandbox_files_written [len(summary["files_written"] or [])], sandbox_dangerous_calls [len(summary["dangerous_calls"] or [])]
  def sandbox_blob_task(sha256: str, summary: dict) -> Task
      # kind "sandbox-report", name f"{sha256[:12]}-kadath-summary.json", json.dumps(summary, indent=2, sort_keys=True);
      # over MAX_SUMMARY_BYTES: drop "callchain" and add "truncated": true before dumping
  # common.py
  TASK_DIR_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
  def sweep_work_root(work_root: Path, log) -> int     # removes only TASK_DIR_RE-named, non-symlink dirs; returns count
  @contextmanager
  def task_work_dir(work_root: Path, task_uid: str) -> Iterator[Path]   # mkdir work_root/task_uid; rmtree in finally
  def report_crash(karton, task, depth: str, exc: BaseException, rules_commit: Optional[str]) -> None
      # sends sample_task(task.get_resource("sample"), *crash_payload(...)); a failure here is logged, never raised
  ```

- [ ] **Step 1: Write the failing tests**

```python
def test_every_payload_carries_analysis_marker_first():
    for _, attrs, _ in (verdict_payload(..., analysis="u"), terminal_payload(..., analysis="u"),
                        crash_payload("full", ValueError("x"), rules_commit=None, analysis="u")):
        assert attrs["malleus_analysis"] == ["u"]
        serialized = json.loads(Task({"type": "sample"}, payload={"attributes": attrs}).serialize())
        assert list(serialized["payload"]["attributes"])[0] == "malleus_analysis"   # what the reporter iterates

def test_crash_payload_names_type_not_message():
    tags, attrs, comments = payloads.crash_payload("auto", RuntimeError("<?php secret"), rules_commit=None, analysis="u")
    assert tags == ["verdict:undetermined", "wm:needs-review"]
    assert attrs["malleus_reasons"] == ["analysis crashed: RuntimeError"] and attrs["malleus_rules_commit"] == ["unknown"]
    assert "secret" not in json.dumps([tags, attrs, comments])

def test_static_payload_drops_llm_raw(): assert "raw" not in payloads.static_payload(res)["llm"]
def test_sandbox_attributes_red(): ...       # flows [{"host": "evil.example", "wp_core": False}], dropped [{"dst": "1.2.3.4", "port": 25}]
    # -> sandbox_level ["red"], sandbox_network ["1.2.3.4:25", "evil.example"], sandbox_files_written [0]
def test_sandbox_attributes_empty_on_error(): assert payloads.sandbox_attributes(error_sb) == {}
def test_sandbox_blob_task_kind_and_name(): assert t.headers == {"type": "blob", "kind": "sandbox-report"} and t.payload["name"] == "abababababab-kadath-summary.json"
def test_sandbox_blob_truncates_large_callchain(): assert json.loads(t.payload["content"])["truncated"] is True
def test_attribute_keys_cover_everything_emitted(): ...  # extend the existing test with sandbox_attributes and crash_payload keys
def test_sweep_only_removes_task_uid_dirs(tmp_path):
    # work_root holds <uuid>/, rules.yarc, kadath.lock, "notes"/ and a 64-hex dir -> only <uuid>/ removed, returns 1
def test_task_work_dir_removed_on_exception(tmp_path): ...
def test_report_crash_swallows_send_failure(): ...      # karton.send_task raises -> logged, no exception
```

- [ ] **Step 2: Run to verify failure** → `TypeError: verdict_payload() got an unexpected keyword argument 'analysis'`
- [ ] **Step 3: Implement.** In `verdict_payload` and `terminal_payload`, `malleus_analysis` is the first key of the attributes dict. Keys sort first after Karton's `sort_keys=True` anyway; the test pins it.
- [ ] **Step 4: Run the whole suite** `.venv/bin/python -m pytest tests -q` → PASS (including `tests/service/test_static.py` with the three call sites updated)
- [ ] **Step 5: Commit** `git commit -m "Service: analysis marker, crash and sandbox payloads, task work dirs"`

### Task 4: `malleus-static` changes (Malleus)

**Files:**
- Modify: `malleus/service/static.py`
- Test: `tests/service/test_static.py`

**Interfaces:**
- Consumes: Task 3 (`common.*`, `payloads.static_payload`, `analysis=`).
- Produces (the detonate task's payload, read by Task 5):
  ```python
  # headers {"type": "sample", "stage": "malleus-detonate"}; derived from the incoming task, then:
  if detonate.has_payload("attributes"):
      detonate.remove_payload("attributes")                   # inherited raw attributes (context) do not travel
  detonate.add_payload("malleus_static", payloads.static_payload(res))
  detonate.add_payload("malleus_depth", depth)
  detonate.add_payload("malleus_rules_commit", self.rules_commit)
  detonate.add_payload("malleus_disagreement", disagreement)  # dict or None
  ```
- Behaviour changes:
  - Work dir is `common.task_work_dir(self.work_root, task.uid)` (not the sha256).
  - `_sweep_work_root` becomes `common.sweep_work_root`.
  - (Task 3 already passes `analysis=task.root_uid` on every emitted sample task.)
  - Scope ruling 1: after `run_static`, when `depth == "full"` and `res.sandbox_mode == "auto"`, set `res.sandbox_mode = "always"` before `sandbox_decision`.
  - Scope ruling 6: `process` computes depth, returns at `none`, then runs the rest in `try/except Exception`. On an exception it calls `common.report_crash(self, task, depth, exc, self.rules_commit)` unless the sample task was already sent, then re-raises. "Already sent" means a per-task `self._reported` flag, reset at the start of `process` and set right after the sample task's `send_task`.
  - The Part 1 comment about `sandbox_decision` checking `kadath_dir` locally is replaced by: "the detonate service re-decides on its own host".

- [ ] **Step 1: Write the failing tests** (in `StaticCase`, existing helpers)

```python
def test_sample_task_carries_analysis_marker(self):
    task = self._task("a.php", PHP, ["source:jetpack-scan"])   # strong hit; _task builds what _run sends
    out = self.run_task(task)
    assert attrs(out[0])["malleus_analysis"] == [task.root_uid]
def test_detonate_task_without_attributes_payload(self): ...   # incoming task has no "attributes" key -> detonate task still emitted
def test_detonate_payload_drops_attributes_and_raw_and_keeps_disagreement(self):
    # sandbox_mode "auto", context rules ["weak_r"], hub hits set(), green model
    det = out[-1]
    assert "attributes" not in det.payload and "raw" not in det.payload["malleus_static"]["llm"]
    assert det.payload["malleus_disagreement"] == {"hub": [], "caller": ["weak_r"]}
def test_full_depth_detonates_even_when_model_red(self):
    # sandbox_mode "auto", tags ["source:researcher"], asker returns red 0.9 -> last task is malleus-detonate,
    # payload malleus_static["sandbox_mode"] == "always"
def test_auto_depth_red_model_is_not_detonated(self): ...   # same, source:jetpack-scan + weak hit -> sample task, verdict likely-malicious
def test_work_dir_keyed_by_task_uid(self): ...    # sweeper records layers_dir.parent.name == task.uid
def test_crash_emits_undetermined_and_reraises(self):
    # sweeper raises RuntimeError -> run_task raises; the one emitted task is a sample task with
    # tags ["verdict:undetermined", "wm:needs-review"] and malleus_reasons ["analysis crashed: RuntimeError"]
def test_crash_after_sample_task_sent_emits_nothing_more(self):
    # weak hit settled by deobf (sample task, then a layer blob); wrap self.karton.send_task so it raises on the first
    # {"type": "blob"} task -> run_task raises, and the sent tasks are exactly one sample task carrying verdict:malicious
    # (layer_blob_tasks reads the files before the sample task is sent, so the failure must be in send_task to test the guard)
def test_untagged_crash_free_path_emits_nothing(self): ...   # depth none still emits nothing
def test_first_compile_failure_reports_crash(self): ...      # replaces test_first_compile_failure_fails_the_task: still raises, and emits the crash verdict with malleus_rules_commit ["unknown"]
```

Update `test_leftover_sample_dirs_swept_on_start` to UUID-named directories. Update `test_detonate_task_emitted_when_sandbox_wanted` for the new payload keys.

- [ ] **Step 2: Run to verify failure** → `.venv/bin/python -m pytest tests/service/test_static.py -q` FAIL
- [ ] **Step 3: Implement** the changes in `static.py`
- [ ] **Step 4: Run the whole suite** → PASS
- [ ] **Step 5: Commit** `git commit -m "Service: static keys work dirs by task uid, reports crashes, full depth detonates"`

### Task 5: `malleus-detonate` Karton consumer (Malleus)

**Files:**
- Create: `malleus/service/detonate.py`
- Modify: `pyproject.toml` (`[project.scripts] malleus-detonate = "malleus.service.detonate:main"`)
- Test: `tests/service/test_detonate.py` (`pytest.importorskip("karton.core")`, `KartonTestCase`)

**Interfaces:**
- Consumes: Task 1 `StaticResult.from_dict`; Task 2 `HostLock`, `sandbox.offer`; Task 3 `common.*`, `payloads.verdict_payload`, `sandbox_attributes`, `sandbox_blob_task`; `static.settings_from_config`; `pipeline.run_sandbox_stage`, `finish`.
- Produces:
  ```python
  DEFAULT_DETONATE_WORK_ROOT = "/var/lib/malleus/detonate"
  DEFAULT_LOCK_PATH = "/var/lib/malleus/kadath.lock"
  class MalleusDetonate(Karton):
      identity = "karton.malleus-detonate"
      filters = [{"type": "sample", "stage": "malleus-detonate"}]
      def __init__(self, config=None, identity=None, backend=None, *,
                   settings: Optional[pipeline.Settings] = None, work_root: Optional[Path] = None) -> None
          # settings default: detonate_settings(self.config); tests pass their own (fake offerer, HostLock under tmp)
          # work_root: [malleus] detonate_work_root, default DEFAULT_DETONATE_WORK_ROOT; swept with common.sweep_work_root at start
      def process(self, task: Task) -> None
  def detonate_settings(config: Config) -> pipeline.Settings
      # static.settings_from_config(config), then
      #   sandbox_lock = HostLock(Path([malleus] sandbox_lock, default DEFAULT_LOCK_PATH))
      #   offerer = lambda sample, kadath_dir, runner=None: sandbox.offer(sample, kadath_dir,
      #                 timeout=[malleus] sandbox_timeout (getint, default 600))
  def main() -> None
  ```
- `process` order: read `malleus_depth`, `malleus_static`, `malleus_rules_commit`, and `malleus_disagreement` (default `None`). Then `res = StaticResult.from_dict(...)` and open `task_work_dir(work_root, task.uid)`. Write the resource content to `<dir>/sample.php`; a sha256 that differs from `res.sha256` raises `ValueError("sample does not match the static result")`. Then `res.path = str(sample_path)`, `sb, stage = pipeline.run_sandbox_stage(sample_path, res, self.settings)`, `v = pipeline.finish(res, sb, stage)`. Build `verdict_payload(..., analysis=task.root_uid)` and merge `sandbox_attributes(sb)` into its attributes. Emit the `analyzed` sample task **first**, then `sandbox_blob_task` when `sb is not None and sb.summary is not None`. It never re-emits layer or llm blobs: static already sent them, and their paths are gone.
- Crash handling as in Task 4 (`_reported` flag, `common.report_crash`, re-raise).
- The resource name is never used as a path component.

- [ ] **Step 1: Write the failing tests.** Build the payload with Task 4's static case or `payloads.static_payload` on a `run_static` result; the offerer is a fake.

```python
def test_green_full_coverage_is_likely_benign_with_report_blob(self):
    # offerer returns ok summary (no flows, no dangerous calls, run without stubs); llm green 0.9
    out = self._run(payload)
    assert [t.headers.get("stage") or t.headers["kind"] for t in out] == ["analyzed", "sandbox-report"]
    a = attrs(out[0])
    assert "verdict:likely-benign" in out[0].payload["tags"] and a["sandbox_level"] == ["green"]
    assert a["malleus_analysis"] == [task.root_uid] and a["malleus_stages"][0]["sandbox"]["status"] == "ok"
def test_red_sandbox_is_malicious(self): ...            # flow to evil.example -> verdict:malicious, sandbox_network ["evil.example"]
def test_busy_kadath_is_undetermined_not_crash(self):
    # offerer returns SandboxResult("error", ..., "kadath offer rc=2: error: another offering is in progress", 1.0)
    assert "verdict:undetermined" in tags and "wm:needs-review" in tags and len(out) == 1
def test_kadath_missing_skips_and_still_reports(self): ...   # no kadath_dir/bin/kadath -> stage skipped, sample task emitted
def test_disagreement_carried_to_verdict(self): ...           # malleus_disagreement payload -> malleus_gate_disagreement attribute
def test_part1_shaped_payload_accepted(self): ...             # llm.raw present, attributes present, no malleus_disagreement
def test_mismatched_sample_reports_crash(self): ...           # resource content differs -> crash verdict, task raises
def test_crash_after_sample_task_sent_emits_nothing_more(self):
    # send_task wrapped to raise on the sandbox-report blob -> run_task raises; exactly one sample task, no crash verdict
def test_offer_holds_host_lock(self): ...                     # offerer asserts the lock file is flocked (a second HostLock in a thread cannot enter)
def test_detonate_settings_wires_timeout_and_lock(tmp_path, monkeypatch):
    # Config with [malleus] rules_dir, sandbox_timeout = 42, sandbox_lock = tmp/k.lock; sandbox.offer monkeypatched to record kwargs
    s = detonate.detonate_settings(cfg); s.offerer(sample, kadath_dir, runner=None)
    assert recorded["timeout"] == 42 and isinstance(s.sandbox_lock, procs.HostLock)
def test_work_dir_removed_and_swept(self): ...
def test_layers_and_llm_blobs_never_emitted(self): ...
```

- [ ] **Step 2: Run to verify failure** → `ModuleNotFoundError: No module named 'malleus.service.detonate'`
- [ ] **Step 3: Implement `detonate.py`.** Reinstall the editable package so the new console script exists: `.venv/bin/pip install -e '.[dev,service]'`.
- [ ] **Step 4: Run the whole suite** → PASS
- [ ] **Step 5: Commit** `git commit -m "Service: malleus-detonate Karton consumer"`

### Task 6: Preflight, worker units and install docs (Malleus)

**Files:**
- Create: `malleus/service/preflight.py`, `deploy/worker/malleus-detonate.service`, `tests/service/test_preflight.py`, `tests/fixtures/kadath/hello.php` (`<?php echo "malleus warm-up";`), `tests/fixtures/kadath/probe.php` (the containment probe of fork Task 9 item 7, with the literal placeholder `MWDB_VPC_IP`; it never holds a real address in git)
- Modify: `pyproject.toml` (`malleus-preflight = "malleus.service.preflight:main"`), `deploy/worker/malleus-static.service`, `deploy/worker/karton.ini.example`, `deploy/worker/README.md`

**Interfaces:**
- Produces:
  ```python
  def check_static(settings: pipeline.Settings, runner=procs.run_command) -> List[str]
      # problems, empty when fine: `yr --version` rc 0; `php --version` rc 0; phpdeobf_dir/index.php and vendor/autoload.php exist;
      # when settings.use_x: `docker image inspect phpdeobf` rc 0; settings.rules_dir is a dir and static.git_head(rules_dir) != "unknown"
  def check_detonate(settings: pipeline.Settings, lock_path: Path, work_root: Path, runner=procs.run_command) -> List[str]
      # kadath_dir/bin/kadath is a file; `docker info` rc 0; `docker compose version` rc 0;
      # lock_path's parent and work_root's parent are writable
  def main(argv: Optional[List[str]] = None) -> int
      # usage: malleus-preflight {static,detonate} [--config-file PATH]; reads karton Config, prints one line per problem
      # to stderr, returns 1 when any, 0 otherwise
  ```
- `malleus-detonate.service`:
  - `[Unit]`: `After=network-online.target docker.service`, `StartLimitIntervalSec=0`.
  - `[Service]`: `User=malleus`, `StateDirectory=malleus`, `StateDirectoryMode=0700`, `EnvironmentFile=/etc/malleus/env`, `ExecStartPre=/opt/malleus/venv/bin/malleus-preflight detonate --config-file /etc/karton/karton.ini`, `ExecStart=/opt/malleus/venv/bin/malleus-detonate --config-file /etc/karton/karton.ini`, `Restart=on-failure`, `RestartSec=30`, `RestartSteps=5`, `RestartMaxDelaySec=600`, `KillMode=mixed`, `TimeoutStopSec=900` (600 s offering + 60 s SIGINT grace + reporting), `NoNewPrivileges=yes`, `PrivateTmp=yes`.
  - **No start limit, stepped backoff instead.** karton-core's consume loop does not catch Redis errors (`karton/core/karton.py` `loop`), so an MWDB restart exits both consumers. A start limit would leave them permanently failed. The 30 s → 600 s backoff (systemd ≥ 254; Ubuntu 24.04 has 255) keeps a failing preflight from spinning and still recovers on its own.
  - A comment explains each timeout number.
- `malleus-static.service` gains the same `ExecStartPre` (with `static`), `StartLimitIntervalSec=0`, `RestartSec=30`, `RestartSteps=5`, `RestartMaxDelaySec=600`, and `TimeoutStopSec=1200` (the parked Part 1 ruling: 3×60 + 3×180 + 300 = 1020 s worst case).
- `karton.ini.example` `[malleus]`:
  - `sandbox_mode = never`, with the comment "switch to auto once malleus-detonate is running on this host (README, rollout order)".
  - `sandbox_timeout = 600`, `sandbox_lock = /var/lib/malleus/kadath.lock`, `detonate_work_root = /var/lib/malleus/detonate`.
  - The Part 1 "must stay never" note is removed.
- `README.md` gains a "Kadath" section:
  - Docker CE with the Compose v2 plugin (Ubuntu's `docker.io` has no `docker compose`).
  - `/opt/KadathSandbox` checked out at the pinned commit recorded in the README, owned by `malleus`, with `.env` from `.env.example` and fresh passwords. `chown -R 33:33 artifacts/`.
  - As `malleus`: `make up`, `make selftest`, then one manual `python3 bin/kadath offer --json --stub-missing --adopt-wp /opt/malleus/tests/fixtures/kadath/hello.php` (create that two-line benign fixture). That offering records Kadath's `selftest-pass` marker, so the first service offering does not spend its 600 s on the self-test.
  - The gateway/netcap containers need `NET_ADMIN`/`NET_RAW` (`docker-compose.yml`); the `tracer` profile is privileged and is never started by the service.
  - The worker's Kadath is reserved for the service (scope ruling 4).
  - After a Kadath upgrade, repeat `make up`, `make selftest` and the manual offering before restarting `malleus-detonate`.
  - Install and enable `malleus-detonate.service`, then switch static to `sandbox_mode = auto` and restart it, in that order.
  - Crash recovery: `journalctl -u malleus-detonate`, and Reanalyze on the sample page for an object left without a verdict.

- [ ] **Step 1: Write the failing tests**

```python
def test_check_static_all_ok(tmp_path): assert preflight.check_static(settings, runner=ok_runner) == []
def test_check_static_reports_missing_yr(tmp_path):
    problems = preflight.check_static(settings, runner=runner_failing("yr"))
    assert problems == ["yr --version failed (rc 127): command not found"]
def test_check_static_skips_image_when_use_x_false(tmp_path): ...   # no `docker image inspect` call recorded
def test_check_static_rules_checkout_unreadable(tmp_path): ...       # git rev-parse fails -> "rules checkout ... has no readable HEAD"
def test_check_detonate_missing_kadath(tmp_path): assert any("bin/kadath" in p for p in problems)
def test_main_returns_1_and_prints_problems(tmp_path, capsys, monkeypatch): ...
```

- [ ] **Step 2: Run to verify failure** → `ModuleNotFoundError`
- [ ] **Step 3: Implement `preflight.py`, the units, `karton.ini.example`, the README section and the fixture**
- [ ] **Step 4: Verify.** `.venv/bin/python -m pytest tests -q` → PASS. `bash -n deploy/worker/rules-sync.sh` → no output. `systemd-analyze verify deploy/worker/*.service deploy/worker/*.timer` is run on the worker in Task 10.
- [ ] **Step 5: Commit** `git commit -m "Deploy: malleus-detonate unit, start-up preflight, Kadath install steps"`

### Task 7: Plugin keys and reanalysis cleanup (this fork)

**Files:**
- Create: `docker/plugins/malleus/reanalysis.py`, `docker/plugins/malleus/tests/test_reanalysis.py`
- Modify: `docker/plugins/malleus/attributes.py`, `docker/plugins/malleus/hooks.py`, `docker/plugins/malleus/README.md`, `docker/plugins/malleus/tests/test_attributes.py`, `docker/plugins/malleus/tests/test_hooks.py`

**Interfaces:**
- Consumes: `MalleusHookHandler.reporter_logins`; `Object.remove_tag(tag_name, commit=False)`, `Object.remove_attribute_by_id(attribute_id, check_permissions=False)` (`mwdb/model/object.py:817`), `object.tags` (`.tag`), `mwdb.model.attribute.Attribute` (`id`, `key`, `object_id`).
- Produces:
  ```python
  # attributes.py
  ATTRIBUTE_KEYS           # + the six new keys (Global Constraints), same order as Malleus
  PIPELINE_KEYS            # ATTRIBUTE_KEYS minus context, layer_variant, layer_unlocked
  ANALYSIS_KEY = "malleus_analysis"
  DESCRIPTIONS             # + the six keys; corrected: malleus_depth "Analysis depth: none, auto or full",
                           #   malleus_unlocked "Weak rules a deobfuscated layer matched that the sample did not",
                           #   malleus_gate_disagreement "Hub and caller gate hits when they differ (JSON object)",
                           #   layer_unlocked "Rules this layer matched that the sample did not",
                           #   malleus_verdict "Malleus verdict (known-bad, malicious, likely-*, undetermined, no-hit, not-applicable)"
  def ensure_attribute_definitions() -> None   # also updates `description` of an existing row when it differs
  # reanalysis.py
  REVIEW_TAG = "wm:needs-review"
  def clear_previous_verdict(object, new_tag: str) -> List[str]
      # removes every other "verdict:*" tag and REVIEW_TAG (commit=False); returns the removed names, sorted
  def clear_previous_analysis(object, anchor) -> int
      # removes, by id and without permission checks, every Attribute of `object` whose key is in PIPELINE_KEYS
      # and whose id != anchor.id; returns the count
  # hooks.py
  on_created_tag: existing source: rule first; then, when login in reporter_logins and tag startswith "verdict:",
      clear_previous_verdict + db.session.commit (logged; exceptions rolled back and logged, never raised)
  on_created_attribute: existing context rule; then, when login in reporter_logins and key == ANALYSIS_KEY,
      clear_previous_analysis (logged with the count; exceptions logged, never raised)
  ```

- [ ] **Step 1: Write the failing tests**

```python
def test_attribute_keys_exact_and_described(): ...   # existing test, extended list; every key in DESCRIPTIONS
def test_existing_definition_description_updated(session): ...
def test_clear_previous_verdict_removes_other_verdicts_and_review():
    obj = MagicMock(tags=[ns("verdict:undetermined"), ns("verdict:likely-benign"), ns("wm:needs-review"), ns("yara:r"), ns("source:researcher")])
    assert reanalysis.clear_previous_verdict(obj, "verdict:likely-benign") == ["verdict:undetermined", "wm:needs-review"]
    obj.remove_tag.assert_has_calls([call("verdict:undetermined", commit=False), call("wm:needs-review", commit=False)], any_order=True)
def test_clear_previous_analysis_keeps_anchor_context_and_layers(monkeypatch):
    # fake query returns attributes (id, key): (1, malleus_analysis) anchor, (2, malleus_analysis), (3, malleus_reasons),
    # (4, sandbox_level), (5, context), (6, malleus_unlocked) -> remove_attribute_by_id called for 2, 3, 4, 6 only
def test_hook_verdict_tag_by_reporter_clears(env_reporter): ...
def test_hook_verdict_tag_by_researcher_does_nothing(env): ...       # login "alice"
def test_hook_analysis_attr_by_reporter_clears(env_reporter): ...
def test_hook_analysis_attr_by_scanner_does_nothing(env): ...        # login "jetpack-scan"
def test_hook_cleanup_failure_is_logged_not_raised(env_reporter): ...
```

- [ ] **Step 2: Run to verify failure** → `cd docker/plugins/malleus && python3 -m pytest -q` → `ModuleNotFoundError: No module named 'malleus.reanalysis'`
- [ ] **Step 3: Implement.** `reanalysis.py` imports `Attribute` and `db` lazily, the way `attributes._models()` does, so the conftest stubs keep working. Note in the README the reanalysis behaviour, the `verdict:*` ownership (a hand-set `verdict:*` tag is replaced by the next analysis) and the `yara:*` residual.
- [ ] **Step 4: Run** the plugin tests → PASS; `ruff check docker/plugins/malleus && ruff format --check docker/plugins/malleus` → clean
- [ ] **Step 5: Commit** `git commit -m "Plugin malleus: sandbox and analysis keys, reanalysis replaces the previous verdict"`

### Task 8: End to end on the dev stack with the laptop's Kadath (this fork, manual verification)

**Files:**
- Modify: `dev/malleus-smoke.py`. Add these flags:
  - `--expect-sandbox`: waits for a `sandbox-report` child named `*-kadath-summary.json` and a `sandbox_level` attribute; asserts `malleus_stages[0]["sandbox"]["status"] == "ok"`.
  - `--reanalyze`: after the first verdict, calls `obj.reanalyze()`, waits for a `malleus_analysis` value different from the first, then asserts exactly one `verdict:*` tag, exactly one value each of `malleus_analysis`, `malleus_verdict`, `malleus_confidence`, `malleus_depth`, and the right `wm:needs-review` presence for that verdict.
  - Exit 1 with the failing check on stderr.
- Modify: `CLAUDE.md` (the `malleus` plugin line mentions reanalysis cleanup)

- [ ] **Step 1:** `./gen_vars.sh && ./compose.sh --with dev --with plugins --with karton --with malleus-dev up -d --build`. Check `mwdb-core configure` created the six new attribute definitions: `GET /api/attribute/definition` lists `malleus_analysis` and `sandbox_level`.
- [ ] **Step 2:** On the laptop, check out Malleus `feature/detonate` and `.venv/bin/pip install -e '.[dev,service]'`. In `~/.config/karton/karton.ini` set `sandbox_mode = auto`, `kadath_dir = /Users/fioa8c/WORK/KadathSandbox`, `sandbox_lock` and `detonate_work_root` under a scratch dir, and `work_root` as in Part 1. Then:
  - `malleus-preflight static` and `malleus-preflight detonate` → exit 0.
  - `make -C /Users/fioa8c/WORK/KadathSandbox up selftest` → the self-test passes.
  - Run `malleus-static` and `malleus-detonate` in two terminals.
- [ ] **Step 3:** `python3 dev/malleus-smoke.py --expect-sandbox --reanalyze --timeout 1500` → exit 0. It prints the verdict, the children (deobfuscated layers, `-llm.json`, `-kadath-summary.json`) and "reanalysis: one verdict, one value per key".
- [ ] **Step 4:** Stop and crash paths by hand.
  - Press Ctrl-C on `malleus-detonate` during an offering: it finishes the offering and reports, then exits.
  - Point `rules_dir` at a scratch git checkout holding one rule file with a syntax error and restart `malleus-static`, so the first compile fails. Upload a new sample (`--salt` gives a fresh sha256). It ends `verdict:undetermined` + `wm:needs-review` with reason `analysis crashed: <Type>`, and the Karton analysis shows the task crashed.
  - Restore the config.
- [ ] **Step 5:** Open the sample page: one Karton analysis per run, with `karton.malleus-static` and `karton.malleus-detonate`. The `sandbox-report` blob opens. After the reanalysis only the new values show.
- [ ] **Step 6: Commit** `git commit -m "Dev: smoke checks detonation and reanalysis cleanup"`

### Task 9: Runbook for the worker droplet and the prod rollout (this fork)

**Files:**
- Modify: `deploy/DEPLOYMENT.md` section 9 ("Karton and the worker droplet"), `docs/superpowers/plans/2026-11-10-second-opinion-1-execution-rulings.md` (append "Part 2 rulings" pointing at this plan's scope rulings)

The runbook section gains, in this order:
1. **Before deploying:** `pg_dump` (section 10's manual backup) because `configure` swallows upgrade failures.
2. **Droplet.** The account that owns it is the user's decision, asked in Task 10. `doctl compute droplet create malleus-worker --size c-8 --image ubuntu-24-04-x64 --region <MWDB droplet's region> --vpc-uuid <MWDB droplet's VPC> --tag-name malleus-worker --ssh-keys <ids>`. Find the VPC with `doctl compute droplet get <mwdb> --format VPCUUID,Region`. `c-8` is CPU-Optimized, 8 vCPU / 16 GB, $168/month per spec section 11.
3. **Worker cloud firewall** (`malleus-worker-fw`, applied to tag `malleus-worker`): inbound TCP 22 from the operator's address only, nothing else; outbound all. Kadath samples need egress through its gateway.
4. **MWDB droplet firewall change.** In the MWDB droplet's cloud firewall, add inbound TCP 6379 and 9000 with source tag `malleus-worker`, and nothing from `Anywhere`. Add the matching `ufw allow from <worker VPC IP> to any port 6379,9000 proto tcp`. Verify from outside the VPC that `nc -vz <mwdb public IP> 6379` and `nc -vz <mwdb public IP> 9000` fail. Verify from the worker that `nc -vz <mwdb VPC IP> 6379` and `... 9000` succeed.
5. **Worker install:** follow Malleus `deploy/worker/README.md` (Part 1 sections plus Task 6's Kadath section).
6. **Rollout order:**
   1. Fork deploy (pull, build, up; `configure` creates the new keys).
   2. On the worker: `malleus-static` with `sandbox_mode = never`.
   3. Kadath `make up`, `make selftest`, the manual offering.
   4. `systemd-analyze verify /etc/systemd/system/malleus-*.service`.
   5. Enable `malleus-detonate`.
   6. Set `sandbox_mode = auto` and restart `malleus-static`.
7. **Containment check before real samples.** Copy Malleus `tests/fixtures/kadath/probe.php` (Task 6) to `/tmp`, replace `MWDB_VPC_IP` with the MWDB droplet's VPC address, and offer it with `bin/kadath offer --json --stub-missing --adopt-wp` as `malleus`. It is a benign file that tries `fsockopen(MWDB_VPC_IP, 6379)`, `fsockopen(MWDB_VPC_IP, 9000)` and `file_get_contents("http://169.254.169.254/metadata/v1/id")`, and echoes the results. Pass criterion, read from the printed `summary.json`: `network.flows` has no entry whose host is the MWDB VPC address or `169.254.169.254`, and `network.dropped` lists the MWDB VPC address on 6379 and 9000. Kadath may block a destination at the proxy instead of dropping it, which shows as neither. Then read `artifacts/` (`grep -a ... | cat -v`) and confirm no response came back before calling it blocked. If any connects, stop: Kadath's `GATEWAY_BLOCKED_DESTS` must cover the VPC range and the metadata address before any sample runs.
8. **Verify the pipeline.**
   - Section 9's existing verify steps.
   - `python3 dev/malleus-smoke.py --expect-sandbox --reanalyze` pointed at prod with a researcher account's key.
   - A scanner upload: an auto-depth weak hit with a green model read detonates; one with a red read does not.
9. **Recovery:**
   - `systemctl status malleus-detonate` shows `start-limit-hit` after 3 failed preflights: fix the cause, then `systemctl reset-failed malleus-detonate && systemctl start malleus-detonate`.
   - An object without a verdict after a hard kill: press Reanalyze.

- [ ] **Step 1: Write the section**
- [ ] **Step 2: Verify** that every command in the section exists as written: `doctl compute size list | grep c-8`, and `doctl compute droplet create --help` lists `--vpc-uuid` and `--tag-name`.
- [ ] **Step 3: Commit** `git commit -m "Runbook: worker droplet, firewall, detonation rollout and containment check"`

### Task 10: Worker droplet rollout and production smoke (manual, outward-facing)

Every step here creates billed resources, changes production or opens firewall rules. **Ask the user before each numbered group** (droplet creation, MWDB firewall change, prod deploy, enabling detonation), and quote what will change. Nothing here is automated by the executor without that yes.

This is also the **first production deploy of Part 1's Karton stack**, which the Part 1 rulings list as not verified.

- [ ] **Step 0: Merge gate.** Tasks 1–9 pass a whole-branch review in each repo. The user merges Malleus `feature/detonate` into `trunk` and the fork's `feature/detonate` into `master`. Record both merge commits; they are the refs deployed below (the worker clones Malleus at that `trunk` commit, the MWDB droplet pulls the fork at that `master` commit). No step below runs from an unmerged branch.
- [ ] **Step 1: Ask the user** which DigitalOcean account and project own the droplet, who holds its keys (spec open question), and the operator address for SSH.
- [ ] **Step 2:** `pg_dump` of prod, then deploy the fork to prod per `deploy/DEPLOYMENT.md` sections 9 and 11. Check the attribute definitions list the six new keys and the `karton` account has the section 9 capabilities.
- [ ] **Step 3:** Create the droplet and its firewall (runbook items 2–3). Change the MWDB firewall (item 4) and run both `nc` checks.
- [ ] **Step 4:** Install the worker per Malleus `deploy/worker/README.md`. Run `systemd-analyze verify` on all `malleus-*` and `rules-sync` units → no output. `malleus-preflight static` and `malleus-preflight detonate` as `malleus` → exit 0.
- [ ] **Step 5:** Kadath `make up`, `make selftest`, the manual offering, then the containment probe (runbook item 7) → all three blocked.
- [ ] **Step 6:** Rollout order (runbook item 6), then the production smoke (item 8) → exit 0. Record the results (verdicts, durations, one `kadath-summary` size) in a new "Part 2 verified" section of the rulings document.
- [ ] **Step 7:** Reboot the worker. After it is back, upload one more researcher sample and confirm it detonates. This is the first offering against a stack Docker restarted, and it must finish inside `sandbox_timeout`. If it does not, record the measured time and raise `sandbox_timeout` and `TimeoutStopSec` together.
- [ ] **Step 8: Commit** the rulings update `git commit -m "docs: part 2 verified on the worker droplet"`
