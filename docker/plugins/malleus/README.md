# malleus plugin

Prepares MWDB for the Malleus Karton service (`malleus-static`), which runs
outside MWDB and reports through karton-mwdb-reporter. No frontend part.

On every `mwdb-core configure` (`__plugin_configure__`, run after the
migrations; `start.sh` runs it on every container start) it:

- creates the attribute definitions in `ATTRIBUTE_KEYS` (`attributes.py`;
  keep in sync with `malleus/service/payloads.py` in the Malleus repo);
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

Post-commit hooks:

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

Residual: the depth of analysis is decided from the payload tags, so a scanner
key that uploads with `source:researcher` still gets researcher depth for that
task (the tag itself does not survive). Scanner keys belong to trusted
internal systems.

Tests (no MWDB stack needed): `cd docker/plugins/malleus && python3 -m pytest -q`
