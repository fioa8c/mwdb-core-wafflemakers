# malleus plugin

Prepares MWDB for the Malleus Karton service (`malleus-static`), which runs
outside MWDB and reports through karton-mwdb-reporter. No frontend part.

On startup (idempotent) it:

- creates the attribute definitions in `ATTRIBUTE_KEYS` (`attributes.py`;
  keep in sync with `malleus/service/payloads.py` in the Malleus repo);
- grants `MWDB_MALLEUS_SET_GROUPS` (default `karton`, the reporter) read+set on
  every key, and `MWDB_MALLEUS_READ_GROUPS` (default
  `jetpack-scan,wpcloud-scan,elderward`, the scanners) read on every key plus
  set on `context`. Groups that do not exist are logged and skipped.

Post-commit hooks:

- `context` may only hold `scanner`, `scanner_version`, `rules`, `path`,
  `engine`. Other keys are stripped and the object is tagged
  `wm:context-stripped`. A `context` that is not a JSON object is left alone.
- A scanner account (`jetpack-scan`, `wpcloud-scan`, `elderward`) may only tag
  `source:<its own login>`; a foreign `source:` tag is removed and the object
  is tagged `wm:source-stripped`.

Tests (no MWDB stack needed): `cd docker/plugins/malleus && python3 -m pytest -q`
