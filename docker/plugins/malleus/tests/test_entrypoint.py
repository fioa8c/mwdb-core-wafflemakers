from unittest.mock import MagicMock

import malleus
from malleus import attributes, hooks


def _run(monkeypatch, env=None):
    ensure_defs, ensure_perms = MagicMock(), MagicMock()
    monkeypatch.setattr(attributes, "ensure_attribute_definitions", ensure_defs)
    monkeypatch.setattr(attributes, "ensure_group_permissions", ensure_perms)
    monkeypatch.setattr(hooks, "db", MagicMock())
    for name in ("MWDB_MALLEUS_SET_GROUPS", "MWDB_MALLEUS_READ_GROUPS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in (env or {}).items():
        monkeypatch.setenv(name, value)
    ctx = MagicMock()
    malleus.entrypoint(ctx)
    return ensure_defs, ensure_perms, ctx


def test_entrypoint_defaults(monkeypatch):
    ensure_defs, ensure_perms, ctx = _run(monkeypatch)
    ensure_defs.assert_called_once_with()
    ensure_perms.assert_called_once_with(
        ["karton"], ["jetpack-scan", "wpcloud-scan", "elderward"]
    )
    ctx.register_hook_handler.assert_called_once_with(hooks.MalleusHookHandler)


def test_entrypoint_env_split_strips_and_drops_empties(monkeypatch):
    _, ensure_perms, _ = _run(
        monkeypatch,
        {
            "MWDB_MALLEUS_SET_GROUPS": " a , ,b",
            "MWDB_MALLEUS_READ_GROUPS": "",
        },
    )
    ensure_perms.assert_called_once_with(["a", "b"], [])


def test_entrypoint_survives_setup_failure(monkeypatch):
    fail = MagicMock(side_effect=RuntimeError("no tables yet"))
    monkeypatch.setattr(attributes, "ensure_attribute_definitions", fail)
    monkeypatch.setattr(attributes, "ensure_group_permissions", MagicMock())
    fake_db = MagicMock()
    monkeypatch.setattr(hooks, "db", fake_db)
    ctx = MagicMock()
    malleus.entrypoint(ctx)  # must not raise
    ctx.register_hook_handler.assert_called_once_with(hooks.MalleusHookHandler)
