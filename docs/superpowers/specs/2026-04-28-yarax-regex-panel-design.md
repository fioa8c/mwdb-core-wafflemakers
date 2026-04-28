# YARA-X Regex Panel — Design

**Status:** approved, ready for implementation planning
**Date:** 2026-04-28
**Fork:** `mwdb-core-wafflemakers` (diverged from upstream `CERT-Polska/mwdb-core`)
**Subsystem:** v0 of the YARA-X authoring tooling for the Waffle Makers malware research team

## 1. Goal

Add a **live regex playground** to the MWDB sample detail page so the team can iterate on YARA-X regex patterns against a sample they are already viewing, with sub-second feedback on matches, parse errors, and yara-x compile diagnostics. This is the v0 of a broader effort to bring rule-authoring tooling into the same surface where the team already keeps samples and (in the near future) signatures.

The team writes rules for web threats — PHP, JS, SQL, HTML — where regex is the dominant tool, not hex strings or jumps. Today, validating a regex against a sample is a manual loop: write the rule, compile it, run yara-x against the file, read CLI output, edit, repeat. The panel collapses that loop to "type and see."

The v0 deliberately does **not** include team-specific lints (e.g. `\w → [a-z_\x80-\xff]…`), snippet libraries, corpus-wide evaluation, full rule editing, or AI features. Those depend on either tribal knowledge that needs codifying or design decisions that should be informed by real usage of v0.

## 2. Scope & non-goals

### In scope

- New sample tab labeled "YARA-X Regex" registered via the existing `<Extendable ident="sampleTabs">` slot in `mwdb/web/src/components/Views/ShowSampleView.tsx`.
- Single-line regex input with autofocus on tab open and per-sample draft persistence in `localStorage` (survives page reload, scoped by `sample_id`).
- Live evaluation against the **currently-viewed sample only**, debounced ~200 ms after the last keystroke. In-flight requests are cancelled when the regex changes.
- Sample content rendered using MWDB's existing `ObjectPreview`, reusing the language-detection helper from `SamplePreview.tsx` (today: PHP and HTML; `.js` is mis-mapped to `"json"` upstream — out of scope to fix here, but worth flagging during plan writing). Match offsets are drawn as a tinted overlay layer on top.
- Match list sidebar: one row per match with `Lℓ:c` offset and matched substring; clicking jumps the sample view to the line and pulses the highlight.
- Lint strip directly under the input, surfacing **whatever yara-x's compile API natively reports**: parse errors, compile errors, atom-extraction warnings, performance hints. No team-specific judgments.
- Backend endpoint `POST /api/yarax/regex` taking `{sample_id, regex}` and returning matches plus diagnostics. Backend reads sample bytes from MWDB storage directly (frontend does not POST sample content).

### Explicitly deferred (v1 or later)

- Snippet library / autocomplete for vetted character classes (PHP variable, JS identifier, UTF-8 high-byte ranges, etc.). Deferred until v0 usage shows which patterns are most valuable.
- Team-specific lints (the `\w` family of false-shorthand corrections). Same reason.
- Mining the existing `.yara` signature directory for canonical patterns or regression-test corpora.
- Mining the malwareresearchuniversity.wordpress.com blog for documented gotchas.
- Decoded-view pane for `eval(base64_decode(gzinflate(...)))` chains.
- Concatenation-aware matching (`$a="ev"."al"`).
- Multi-sample / corpus-wide evaluation. Hit-miss matrix across samples.
- Full YARA rule editing (multiple strings, conditions, modifiers).
- Condition explainer / debugger.
- Regression testing against historical sample hits.
- Goodware / negative corpus checks.
- AI-assisted rule generalization, "why didn't this match" diff, etc.
- Saving regexes server-side. v0 persistence is browser-only.

### Explicitly NOT added (YAGNI)

- No script-aware atom-quality heuristic. yara-x's atom extractor is tuned for binary samples; for scripted web threats its output is often weak. v0 surfaces what the engine reports and labels it as such; reworking the heuristic for scripts is a research item, not v0 polish.
- No new auth surface. The endpoint reuses MWDB's existing sample-read decorator. If a user can read the sample, they can eval regex against it; otherwise they get the existing 404.
- No new storage or schema. Nothing about v0 touches the database.
- No formal Cypress E2E coverage in v0. Manual run-through against the dev stack plus unit/component tests is sufficient for the surface area; the existing Cypress harness covers MWDB itself, and adding E2E here adds days for a feature this small. Revisit when v1 features (corpus eval, etc.) make it worth it.
- No rate limiting at the endpoint level. Client-side debounce is enough; if abuse appears, MWDB's existing rate-limit infrastructure is the right place to add it.

## 3. Architecture overview

```
                                  ┌─────────────────────────────────────┐
                                  │  MWDB sample detail page (React)    │
                                  │  ShowSampleView.tsx                 │
                                  │   <Extendable ident="sampleTabs">   │
                                  │     ├── Details                     │
                                  │     ├── Relations                   │
                                  │     ├── Preview                     │
                                  │     ├── Static Config               │
                                  │     ├── Comments                    │
                                  │     └── ▶ YARA-X Regex (new)        │
                                  └────────────────┬────────────────────┘
                                                   │
                                                   ▼
       ┌──────────────────────────────────────────────────────────────────┐
       │ <YaraXRegexTab>                  (frontend plugin module)        │
       │  ├── <RegexInput>      autofocus, localStorage draft per sample  │
       │  ├── <LintStrip>       errors / warnings / atoms                 │
       │  ├── <SampleWithHighlights>      wraps <ObjectPreview>           │
       │  └── <MatchList>       click → jump to line + pulse              │
       │                                                                  │
       │  state: {regex, lastResult, inFlight}                            │
       │  effect: debounce(regex, 200ms) → POST /api/yarax/regex          │
       │          (AbortController cancels stale requests)                │
       └──────────────────────────────┬───────────────────────────────────┘
                                      │  axios via APIContext
                                      ▼
       ┌──────────────────────────────────────────────────────────────────┐
       │ POST /api/yarax/regex            (backend plugin)                │
       │ docker/plugins/yarax-regex/      Flask blueprint                 │
       │                                                                  │
       │   1. Reuse MWDB auth decorator     → 401/404 inherited           │
       │   2. Load sample bytes from MWDB storage (de-obfuscated)         │
       │   3. Synthesize:                                                 │
       │        rule _r {                                                 │
       │          strings: $a = /USER_REGEX_ESCAPED/                      │
       │          condition: any of them                                  │
       │        }                                                         │
       │   4. yara_x.compile(rule).scan(bytes) via Python lib             │
       │   5. Return { matches[], diagnostics[], atoms[], elapsed_ms }    │
       └──────────────────────────────┬───────────────────────────────────┘
                                      │
                                      ▼
                              ┌────────────────────┐
                              │  yara-x (PyPI)     │
                              │  Rust → Python     │
                              │  via maturin       │
                              └────────────────────┘
```

**Design choices that drove the shape:**

- **In-tree plugin in the existing fork repo, not a separate repository.** The fork is the team's source of truth; one repo, one PR, one CI pipeline. Frontend code hooks via the documented `<Extendable>` slot so encapsulation is preserved without distribution overhead.
- **Server-side regex evaluation, not client-side WASM.** yara-x's compile diagnostics (atom info, performance warnings, strict-mode complaints) are surfaced through the rule-compile API, not the regex engine in isolation. Going through the compiler is half the value of the panel; doing that server-side avoids shipping yara-x as WASM and keeps a single truth path. Per-keystroke server load is negligible at single-sample scale.
- **Synthetic-rule wrapping, not raw regex eval.** Wrapping the user's regex in `rule _r { strings: $a = /…/ condition: any of them }` exercises the exact path a real rule will hit in production, and gets us atom warnings + perf diagnostics for free. yara-x is the source of truth for "is this regex valid"; the panel does no client-side regex validation.
- **Backend fetches the sample, frontend does not POST bytes.** Single source of truth (MWDB storage), no oversized POST bodies on every keystroke, and the existing sample-read auth decorator covers it. The frontend already shows the sample for rendering; that is independent of the eval round-trip.
- **Python `yara-x` PyPI package, not subprocess.** Subprocess startup overhead (~10-30 ms) is a real penalty for a debounce-driven UI. Inline Python import is the natural idiom for an MWDB backend plugin and the latency budget has room for it.

## 4. Dependencies & build

### 4.1 Python dependency

- `yara-x` from PyPI, pinned in the plugin's `pyproject.toml`. Installed into the MWDB backend container at build time.
- The exact API surface (specifically: whether the Python binding exposes atom info and compile diagnostics in the same form the CLI does) is **verified during plan writing**, not assumed here. If the binding is partial, v0 ships with a smaller `diagnostics`/`atoms` payload; the endpoint contract still applies.

### 4.1.1 Backend base-image shift (added 2026-04-28)

yara-x 1.x ships only manylinux wheels on PyPI — no musllinux wheels and no source distribution from v1.1.0 onward. The fork's existing backend Docker base (`python:3.12-alpine`) uses musl libc and cannot run manylinux wheels, and there is no path to compile yara-x on Alpine because the upstream maintainers stopped publishing sdists.

The fork is therefore migrating the backend container to **`python:3.12-slim-bookworm`** (Debian-slim) as a prerequisite to this plugin. This is a deliberate strategic shift, not a workaround: the team views in-tree plugins as a long-term direction, the dependency footprint that comes with them aligns better with PyPI's wheel ecosystem (which targets glibc), and the slim-bookworm baseline gives easier access to system packages without the apk/musl friction.

Scope of the migration:

- `deploy/docker/Dockerfile` only. Both the builder stage (`ghcr.io/astral-sh/uv:python3.12-bookworm-slim`) and the runtime stage (`python:3.12-slim-bookworm`).
- Frontend Dockerfiles (`Dockerfile-web`, `Dockerfile-web-dev`, `Dockerfile-web-unit-test`) stay on `node:22-alpine` — they do not need yara-x and the migration cost there is unjustified.
- `apk add` lines become `apt-get install -y --no-install-recommends ...` with `rm -rf /var/lib/apt/lists/*` for image-size hygiene.
- Build-stage system packages: `g++` + `python3-dev` (Debian) replace `g++ musl-dev python3-dev` (Alpine). Required because `py-tlsh` has no PyPI wheels at all and is built from sdist on every platform.
- Runtime-stage system packages: `libpq5 postgresql-client libmagic1 libfuzzy2` (Debian) replace `postgresql-client postgresql-dev libmagic libfuzzy2` (Alpine). `libpq-dev` is a build-only dep and should not be in runtime.
- The `nobody` user's default group on Debian is `nogroup` (not `nobody`); `--chown=nobody:nobody` becomes `--chown=nobody:nogroup` in the COPY step.

Image-size impact: roughly +60–100 MB compressed compared to Alpine. Acceptable for the team's deployment context per the decision.

### 4.2 Plugin scaffolding

- **Backend:** `docker/plugins/yarax-regex/` — Python module with `pyproject.toml`/`setup.py` matching the convention used by the fork's other backend plugins. Registers a Flask blueprint via MWDB's plugin entry point. The exact registration mechanics (entry point name, init hook signature) are matched to existing plugins during plan writing.
- **Frontend:** module wired in via the existing frontend plugin loader at `mwdb/web/src/commons/plugins/loader.tsx`. Source layout matches whatever the fork's other in-tree frontend plugins use; if none exist yet, default to `mwdb/web/src/plugins/yarax-regex/` and pin the path during plan writing.

### 4.3 Docker compose

- No new service. The plugin runs inside the existing MWDB backend container. yara-x is a Python import, not a sidecar, not a microservice.

## 5. Backend endpoint contract

### 5.1 Request

```
POST /api/yarax/regex
Auth: same decorator as MWDB's existing sample-read endpoints
Body: { "sample_id": "<sha256 or mwdb id>", "regex": "<user pattern>" }
```

### 5.2 Response — success

```json
{
  "status": "ok",
  "matches": [
    { "offset": 47, "length": 8, "line": 2, "column": 7, "text": "$payload" }
  ],
  "diagnostics": [
    { "severity": "warning", "code": "no-strong-atom", "message": "..." }
  ],
  "atoms": ["$"],
  "elapsed_ms": 12
}
```

### 5.3 Response — compile failure

```json
{
  "status": "compile_error",
  "diagnostics": [
    { "severity": "error", "code": "...", "message": "...", "span": [4, 7] }
  ]
}
```

The `span` on errors lets the frontend underline the bad part of the regex inside `<RegexInput>` — small touch, real value.

### 5.4 Other response codes

- `400` — request body malformed (missing `sample_id` or `regex`).
- `404` — sample doesn't exist or the user can't access it (matches MWDB convention; do not differentiate to avoid sample-existence leakage).
- `413` — regex exceeds length cap (4 KB, hard-coded; real regexes are sub-200 chars).
- `503` — yara-x backend unavailable (Python import failed at server startup or eval consistently raises). Frontend shows "yara-x backend unavailable, contact admin."
- `500` — unexpected exception during scan; full traceback to backend log, generic message to client. We do not surface yara-x stack traces to users.

### 5.5 Synthetic rule construction

The handler builds the rule by string substitution into a fixed template:

```
rule _r { strings: $a = /__USER_REGEX__/ condition: any of them }
```

The user's regex is escaped only enough to keep the synthetic rule parseable: any literal `/` in the user input is replaced with `\/` before substitution. `\` is not escaped because it is part of regex syntax. Anything yara-x rejects after that flows back as a `compile_error` — yara-x is the validator, not us.

### 5.6 Sample fetch

The handler reads sample bytes via MWDB's existing internal sample-read path (the same path that ultimately feeds `SamplePreview`'s `api.downloadFile`), applies the same de-obfuscation that the frontend's `negateBuffer` would have applied, and passes plaintext bytes to yara-x's `scan()`. The exact internal API used here is matched to the existing fork's conventions during plan writing.

### 5.7 Limits

- Regex length: 4 KB cap at the endpoint.
- Sample size: soft cap at 50 MB. Larger samples return `status: "sample_too_large"` rather than blocking the worker. Web-threat samples are typically KB to low MB; the cap is defense, not a real expected case.
- yara-x's built-in compile-time and scan-time limits (timeouts, regex complexity caps) are used as-is. We do not roll our own.

## 6. Frontend component design

### 6.1 Tree

- `<YaraXRegexTab>` — top-level. Reads `sample_id` from `ObjectContext` (the same context `SamplePreview` uses). Owns regex draft + last result + in-flight state.
  - `<RegexInput>` — single-line monospace input. Autofocus on tab open. Persists draft to `localStorage` keyed by `sample_id`. On change, schedules a debounced eval.
  - `<LintStrip>` — engine diagnostics: errors (red), warnings (amber), info (atoms, grey). Empty when regex is empty. Shows a subtle "evaluating…" indicator while a request is in flight.
  - `<SampleWithHighlights>` — the sample body. Fetches sample content via the same path as `SamplePreview.tsx` (`api.downloadFile(id, obfuscate=1)` + `negateBuffer`). **v0 implementation:** renders the decoded UTF-8 text in a plain `<pre>` block with inline `<mark>` overlays for matches, rather than wrapping `ObjectPreview`. Reason: positioning Ace editor markers from byte offsets is fiddly and error-prone for a v0 timeline; a plain `<pre>` gives match-span correctness and direct DOM-node mapping for free, at the cost of no syntax highlighting on the sample text. **v1:** integrate `ObjectPreview` / react-ace once the byte-vs-char-offset translation is implemented, since both concerns share the same input transform. Match spans tint as background-highlighted ranges either way.
  - `<MatchList>` — sidebar. One row per match: `Lℓ:c` offset + matched substring (truncated to ~40 chars with full text on hover). Click a row → `<SampleWithHighlights>` scrolls to the line and pulses the corresponding highlight.

### 6.2 Eval lifecycle

1. User types. `<RegexInput>` updates local state immediately (no debounce on the visual input — only on the network call).
2. After 200 ms idle, the component sends `POST /api/yarax/regex` via the existing `APIContext` axios setup.
3. Any in-flight request from a previous regex value is cancelled (`AbortController` on the previous fetch).
4. On response: lint strip and match list populate; highlight overlay rebuilds from match offsets.
5. While a request is in flight, match list and highlights remain showing the previous result. No flicker, no skeleton state. Lint strip is the only "evaluating…" surface.

### 6.3 Edge behaviors

- **Empty regex:** no eval, no error, no highlights. Lint strip empty.
- **Invalid regex (yara-x rejects):** lint strip shows the parse error with optional underline span on the input. Match list and highlights stay at previous successful result, dimmed, with a "stale — regex doesn't compile" indicator. Final visual treatment of the dimmed-stale state is pinned during plan writing.
- **Sample fetch failure (404 etc.):** tab body shows "sample not available" — same surface MWDB uses elsewhere.
- **yara-x backend unavailable (503):** tab body shows "yara-x backend unavailable, contact admin." Plugin mis-deployment, not a user error.

### 6.4 State that lives only in the browser

- Regex draft per sample (`localStorage`, key `yarax-regex-draft:<sample_id>`).
- Last successful eval result (in-memory; lost on tab change, refetched on remount).

### 6.5 State that does not exist in v0

- No server-side regex storage.
- No regex history.
- No sharing of drafts between users.

## 7. Performance, observability, errors

### 7.1 Performance budgets (design targets, not SLAs)

- Keystroke → first highlight: **≤ 350 ms** typical (200 ms debounce + ≤ 150 ms server round-trip).
- Server-side eval per request: **≤ 50 ms** for samples under 1 MB. Bottleneck is rule compile, which is approximately constant.
- Worst-case sample (50 MB cap): under 1 second. If exceeded in real samples, the team revisits the cap.

### 7.2 Logging

- Default level (info): per request — `sample_id`, `regex_length`, `compile_or_scan_elapsed_ms`, `result_status`. **Regex content is not logged.** (A researcher's draft regex can leak which family they are investigating; same op-sec reason rule repos are guarded.)
- Debug level: include the full regex text. Operators must opt in by setting MWDB's existing logging config to debug for the plugin's logger.
- Error level: yara-x exceptions with full traceback.

### 7.3 Error handling summary

- yara-x not installed / Python import fails → `503` with admin-facing message.
- Sample doesn't exist or unauthorized → `404`.
- yara-x raises during scan → `500`, traceback to log, generic message to client.
- Regex over 4 KB → `413`.

## 8. Testing

### 8.1 Backend unit tests (pytest, fork's existing convention)

Fixture set of `(regex, sample, expected_matches)` triplets covering:

- Valid regex with one or more hits.
- Valid regex with no hits.
- Invalid regex (parse failure).
- Regex yara-x rejects at compile (e.g. unsupported feature).
- Empty regex (handler returns 400 or no-op result, decided during plan writing).
- Regex hitting the length cap (413).
- Sample over the size cap (`status: "sample_too_large"`).

Synthetic-rule template + slash-escape function are unit-tested directly on slash-containing inputs.

### 8.2 Frontend component tests (Jest, fork's existing setup)

- `<RegexInput>` persists draft to `localStorage` and restores on remount.
- `<LintStrip>` renders error / warning / info severities.
- `<MatchList>` jumps to line on row click.
- API responses are mocked; no real backend.

### 8.3 Manual end-to-end

Short checklist run against the dev MWDB stack with one or two real samples from the team's corpus before deploying. No formal Cypress E2E in v0.

## 9. Open items pinned during plan writing

These are not undecided design questions — they are details that should be resolved by reading the existing fork code rather than guessed in a design doc.

1. Exact source layout for the frontend plugin (where the new tab module lives in the React tree). Match existing in-tree plugin convention if any; otherwise default to `mwdb/web/src/plugins/yarax-regex/`.
2. Exact registration mechanics for the backend plugin: entry-point name, init-hook signature, blueprint registration. Match the convention used by other plugins under `docker/plugins/`.
3. The internal sample-read API used by the backend handler (the server-side equivalent of `api.downloadFile`).
4. The yara-x Python binding's exact diagnostic surface — whether atoms and performance warnings are exposed, or only the CLI exposes them. If the binding is partial, the `atoms` and `diagnostics` fields ship reduced; the endpoint contract holds.
5. Final visual treatment of the "stale result, regex doesn't compile" state in the match list and highlight overlay.
6. Whether the empty regex case returns `400` or a 200 no-op response.

## 10. Future work (informational, not part of v0)

In rough order of likely value, based on the brainstorm:

- **Snippet library / autocomplete** for vetted character classes (PHP variable, JS identifier, UTF-8 high-byte ranges, etc.), seeded by **mining the existing `.yara` signature directory**. Becomes valuable once we have v0-usage data showing which patterns matter most.
- **Team-specific lints** (the `\w → [a-z_\x80-\xff]…` family). Designed against real friction once v0 surfaces it.
- **Corpus-wide evaluation** — same panel, but evaluate a regex against a tag-filtered set of samples, results streamed back. Needs a Karton-like job runner; reuses the same yara-x entry point on the backend.
- **Decoded-view pane** for `eval(base64_decode(gzinflate(...)))` chains, so the user can author regex against the decoded form while still scanning the encoded form.
- **Full YARA rule editor** (multiple strings, conditions, modifiers) with the same live-feedback loop.
- **Condition explainer / debugger** showing which clauses of a multi-string rule are true/false against a given sample.
- **Regression testing** — before saving a rule change, run it against historical hits to catch coverage regressions.
- **Script-aware atom heuristic** — yara-x's atom extractor is binary-tuned; for scripted threats it often produces weak atoms. A v1+ research item, not just polish.
- **AI-assisted features** (rule generalization, "why didn't this match" diff, sample-cluster → rule synthesis) once the live-feedback engine is stable enough to ground LLM proposals.
