# Second-Opinion Pipeline, Part 1: Execution Rulings and Follow-ups

Decisions taken while executing the Phase 0 plan (Malleus `2026-11-09-stage-records-and-llm-provider.md`) and the Part 1 plan (`2026-11-09-second-opinion-1-karton-static.md`) on 2026-11-10, with what each one costs if it turns out wrong. Branches: Malleus `feature/stage-records-and-llm-provider` (Phase 0) and `feature/karton-service` stacked on it; fork `feature/second-opinion-karton-static`. Nothing merged or pushed when this was written.

## Rulings that change how the system behaves

1. **Untagged objects leave no trace; full depth never overrides a known-bad gate.** A file with no `source:` tag is not downloaded, scanned or annotated. A strong hub hit always ends the analysis as `known-bad`, even at researcher depth; the Malleus CLI remains the way to see layers of a known-bad file. *Cost if wrong:* researchers reanalysing a known-bad file get no layers until part 2 adds an override.
2. **`run_static(force=True)` whenever the policy says to continue.** Auto depth with caller-reported rules on an empty hub gate, and full depth, run the deobfuscation and model stages even though the hub found nothing. *Cost if wrong:* model budget spent on files the hub considers clean.
3. **Too-large samples never go to detonation.** Over `MAX_STATIC_BYTES` the consumer skips the sandbox decision and finishes with sandbox stage reason `too-large`, so the verdict is `undetermined`. The rule lives in the consumer, not the pipeline, so the CLI is unchanged. *Cost if wrong:* oversized samples stay undetermined until part 2 lifts the rule.
4. **`context` with disallowed keys is rejected at upload (HTTP 400), stripped only when added later.** MWDB spawns the Karton task inside the upload request, before plugin hooks run, so stripping after the fact would still let the raw value reach Redis, the worker and the reporter logs. The spec's strip-and-tag now applies only to attributes added after upload. *Cost if wrong:* feeders see a 400 instead of silent stripping; no feeders exist yet.
5. **A `source:` tag created by a reporter login is always removed.** The reporter replays the upload payload's tags, so a forged `source:` tag would otherwise be re-added under the `karton` login. Reporter logins come from `MWDB_MALLEUS_REPORTER_LOGINS` (default `karton`). *Accepted residual:* a scanner key can still choose a deeper depth by tagging `source:researcher` at upload; scanner keys are trusted internal systems.
6. **Reporter capabilities include `karton_assign`.** Without it every layer and model-read child upload is refused (HTTP 403) while the sample's verdict still lands; the runbook's verify step now requires the children. Proven on the dev stack with a non-admin reporter account.
7. **Attribute formatting.** `malleus_confidence` is `"%.2f"`; `malleus_gate` is `strong`/`weak`/`none`; `malleus_unlocked` and `malleus_gate_disagreement` are omitted when empty; every verdict, including `no-hit`, `known-bad` and `not-applicable`, carries `malleus_rules_commit`, and a hub-strong/caller-weak disagreement is recorded. The comment is one string: `Malleus verdict: <v> (<conf>)` plus `- reason` lines.
8. **Rules compile atomically and a bad commit keeps the last good ruleset.** The `.yarc` is compiled to a temp file and renamed; a failing compile logs an error, keeps the previous ruleset, index and commit, and is not retried until the checkout moves. The first compile still fails loudly.
9. **Secrets live in env files, never in `karton.ini`.** Prod: `.env` → `KARTON_*` environment overrides on the karton containers and `mwdb`; the committed `deploy/karton.ini.example` has no secrets. Worker: `/etc/malleus/env` (`KARTON_REDIS_PASSWORD`, `KARTON_S3_*`, `KARTON_MALLEUS_LLM_API_KEY`) and a separate `/etc/malleus/rules-sync.env` holding only `RULES_REPO`.
10. **Prod network binding fails closed.** Redis and S3 are published only on `${VPC_BIND_IP:?}`; `.env.example` ships empty secrets so compose refuses to start until values are set. `MWDB_REDIS_URI` is overridden in the `mwdb` service to carry the password.
11. **Worker unit stop behaviour.** `KillMode=mixed`, `TimeoutStopSec=900`, `StateDirectoryMode=0700`, `NoNewPrivileges`, `PrivateTmp`; the consumer sweeps leftover per-sample work directories at start. *Parked:* the sweep removes every subdirectory of `work_root` (restrict to 64-hex names), and the worst-case task can exceed 900 s (raise or recompute).
12. **`sandbox_mode` must stay `never` until part 2.** `auto`/`always` validate, but without a `malleus-detonate` consumer a PHP sample needing runtime evidence gets layer blobs and no verdict.
13. **Model key stays `MALLEUS_MODEL`; upload is `POST /api/file`.** Spec text corrected (the spec file is excluded from git by the user's global ignore, so the correction is on disk only).
14. **Plugin DB setup runs in `__plugin_configure__`,** once after migrations, not at import in every gunicorn worker. Permission flags are rewritten on every configure; the env lists are the source of truth.

## Rulings about process and environment

- Feature branches in place rather than git worktrees (two repositories involved; both checkouts were idle).
- Pre-existing uncommitted CLAUDE.md compose wording committed as its own commit; `.env.example` created (did not exist).
- `wget` added to the mwdb runtime image (the compose healthcheck needs it; the Debian-slim image had none, so `mwdb` could never become healthy and the reporter never started); `unzip` added to the phpdeobf sidecar image (composer install).
- Alembic merge migration `bef8d7548812` over the fork's `7cb478ac1523` (tlsh) and upstream `6e7b2dbd8c4f`; `mwdb-core configure` failed with multiple heads on a fresh database.
- Dev overlay `compose.with-malleus-dev.yml` publishes the SPA on 127.0.0.1:8082 and mailhog on 8027 (80, 8081 and 8025–8026 are taken on the laptop by OrbStack/ddev and Kadath) and gives the dev reporter the generated admin credentials at start.
- The `ghcr.io/astral-sh/uv` builder image was fetched on the host through the registry API and loaded into Docker because the OrbStack daemon proxy returned Bad Gateway for ghcr.io; no Docker or OrbStack settings were changed.
- Golden fixtures keep the model's prose (which names a few `$_POST` parameter names, an attacker IP and a gist account) and corpus sample basenames; acceptable for the internal repo, scrub before the repo leaves the team.
- Task 8's per-task review was folded into the fork's whole-branch review.

## Part 2 must do first

- Reanalysis cleanup: the reporter only adds, so a reanalysis leaves a second `verdict:*` tag and a second value of every `malleus_*` attribute; readers taking `[0]` get the stale one.
- `StaticResult.from_dict` (restoring `forced`, `sandbox_mode`, `is_php`, `too_large`, gate, deobf, llm); per-task work directories keyed by `task.uid`; detonate payload carries the disagreement and drops inherited raw `attributes` and `llm.raw`.
- Decide what a crashed analysis leaves behind (today: no verdict, no retry) and add a startup preflight for `yr`, `php`, the `phpdeobf` image and the rules checkout.
- Polyglot handling: the 256-byte sniff can miss PHP in image files; consider sniffing the whole file or always running the gate when the caller reported rules.
- Single-alembic-head check in CI; `pg_dump` before the first prod deploy (`configure` swallows upgrade failures).

## Verified and not verified

Verified on 2026-11-10 with the final commits: 270 Malleus tests and 75 plugin tests; `docker compose config` on the prod file; repo-wide `ruff check --select I` / `ruff format --check` clean for the files this branch touched (pre-existing violations remain in `docker/plugins/phpdeobf`, `docker/plugins/yarax_regex` and `mwdb/app.py`; `dev/` is excluded from ruff by `pyproject.toml`); an end-to-end smoke on the dev stack (researcher upload → `likely-benign`/`undetermined` with llm-read and two deobfuscated-php children, `malleus_depth` `full`, a real `malleus_rules_commit`), the same with a non-admin `karton` reporter holding exactly the runbook's capabilities, the scanner no-hit path (`source:jetpack-scan` → `verdict:no-hit`, depth `auto`, no children), the HTTP 400 on a disallowed `context` key at upload, the strip-and-tag of a `context` added after upload, the removal of a forged `source:` tag replayed by the reporter, and `mwdb-core configure` on a fresh database (single head `bef8d7548812`, 15 attribute definitions created).

Not verified: the sample page's rendering in the web UI; a production deploy (take a `pg_dump` first; `configure` swallows upgrade failures); `systemd-analyze verify` of the worker units (no Linux host); a foreign `source:` tag sent with a scanner's own API key (the reporter-replay variant was verified instead); the overlay's `!override` port mapping on Compose older than 2.24.
