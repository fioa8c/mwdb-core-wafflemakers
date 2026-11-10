# malleus plugin

Prepares MWDB for the Malleus Karton service (`malleus-static`), which runs
outside MWDB and reports through karton-mwdb-reporter. No frontend part.

On every `mwdb-core configure` (`__plugin_configure__`, run after the
migrations; `start.sh` runs it on every container start) it:

- creates the attribute definitions in `ATTRIBUTE_KEYS` (`attributes.py`;
  keep in sync with `malleus/service/payloads.py` in the Malleus repo; the
  description of an existing definition is rewritten when it differs from
  `DESCRIPTIONS`);
- grants `MWDB_MALLEUS_SET_GROUPS` (default `karton`, the reporter) read+set on
  every key, and `MWDB_MALLEUS_READ_GROUPS` (default
  `jetpack-scan,wpcloud-scan,elderward`, the scanners) read on every key plus
  set on `context`. Groups that do not exist are logged and skipped.

The env is the source of truth for these permissions: the `can_read` /
`can_set` flags of every listed group are rewritten on every configure, so a
change made in the UI to one of them is undone at the next start. A group named
in both lists ends up with the read-group flags (read, set on `context` only),
because the read list is applied second. Removing a group from the env does not
revoke anything: its existing permission rows stay until deleted in the UI
(Settings -> Attributes -> the key -> Permissions).

Upload guard (`upload_guard.py`, an API `before_request`): an upload
(`POST /api/file`, `/api/config`, `/api/blob` and the deprecated
`/api/<type>/<hash>` uploads) whose `context` attribute is a JSON object with
keys outside `scanner`, `scanner_version`, `rules`, `path`, `engine` is rejected
with 400 (`context attribute has disallowed keys: ...`). It has to be rejected:
MWDB spawns the Karton task, with the upload's attributes, inside the request
and before any post-commit hook runs.

Post-commit hooks (they cannot help the Karton task, whose payload is built at upload; feeders must send `context`
and the `source:` tag in the upload options, because anything added later reaches the consumer only after a reanalysis):

- `context` added after upload may only hold the keys above. Other keys are
  stripped and the object is tagged `wm:context-stripped`. A `context` that is
  not a JSON object is left alone.
- A scanner account (`jetpack-scan`, `wpcloud-scan`, `elderward`) may only tag
  `source:<its own login>`; a foreign `source:` tag is removed and the object
  is tagged `wm:source-stripped`.
- A reporter account (`MWDB_MALLEUS_REPORTER_LOGINS`, default `karton`) may not
  create any `source:` tag; it is removed and the object is tagged
  `wm:source-stripped`. The reporter replays the tags of the Karton payload,
  which was built before the scanner-side strip, so a new `source:` tag from it
  is a stripped one coming back. Replaying a tag the object still has creates
  nothing and fires no hook.

Reanalysis (`reanalysis.py`): karton-mwdb-reporter only adds, it never removes,
so a reanalysis would leave two verdicts and two values of every attribute.
Both hooks act only for a reporter login (`MWDB_MALLEUS_REPORTER_LOGINS`):

- Every Malleus sample task carries `malleus_analysis` (the Karton root uid,
  always new, posted before the run's other attributes). When the reporter
  creates it, every other value of a pipeline key (`PIPELINE_KEYS`: all keys
  except `context`, `layer_variant` and `layer_unlocked`) on that object is
  removed. `context` and the layer attributes are never touched.
- When the reporter creates a `verdict:*` tag, the object's other `verdict:*`
  tags and `wm:needs-review` are removed (the reporter re-adds
  `wm:needs-review` right after when the new verdict needs review). The
  `verdict:*` namespace is owned by the pipeline: a `verdict:*` tag set by hand
  is replaced by the next analysis.

Residual: `yara:*` tags are not cleaned, so a rule that matched the previous
run but not the new one keeps its tag. Cleanup failures are logged and rolled
back, never raised; the object then keeps the stale values until the next
analysis.

Residual: the depth of analysis is decided from the payload tags, so a scanner
key that uploads with `source:researcher` still gets researcher depth for that
task (the tag itself does not survive). Scanner keys belong to trusted
internal systems.

Tests (no MWDB stack needed): `cd docker/plugins/malleus && python3 -m pytest -q`
