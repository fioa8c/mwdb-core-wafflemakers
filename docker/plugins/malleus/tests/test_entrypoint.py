import sys
from unittest.mock import MagicMock

import pytest

import malleus
from malleus import attributes, hooks, upload_guard

ENV_NAMES = (
    "MWDB_MALLEUS_SET_GROUPS",
    "MWDB_MALLEUS_READ_GROUPS",
    "MWDB_MALLEUS_REPORTER_LOGINS",
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    # entrypoint sets this class attribute; restore it after each test
    monkeypatch.setattr(hooks.MalleusHookHandler, "reporter_logins", ("karton",))
    return monkeypatch


@pytest.fixture
def setup_mocks(clean_env):
    ensure_defs, ensure_perms = MagicMock(), MagicMock()
    clean_env.setattr(attributes, "ensure_attribute_definitions", ensure_defs)
    clean_env.setattr(attributes, "ensure_group_permissions", ensure_perms)
    fake_db = MagicMock()
    # configure() rolls back through mwdb.model.db
    clean_env.setattr(sys.modules["mwdb.model"], "db", fake_db)
    return ensure_defs, ensure_perms, fake_db


@pytest.fixture
def register_guard(clean_env):
    fake = MagicMock()
    clean_env.setattr(upload_guard, "register", fake)
    return fake


def test_plugin_exposes_entrypoint_and_configure():
    assert malleus.__plugin_entrypoint__ is malleus.entrypoint
    assert malleus.__plugin_configure__ is malleus.configure


def test_entrypoint_registers_hook_and_guard_without_db_setup(
    setup_mocks, register_guard
):
    ensure_defs, ensure_perms, _ = setup_mocks
    ctx = MagicMock()
    malleus.entrypoint(ctx)
    ctx.register_hook_handler.assert_called_once_with(hooks.MalleusHookHandler)
    register_guard.assert_called_once_with()
    ensure_defs.assert_not_called()
    ensure_perms.assert_not_called()
    assert hooks.MalleusHookHandler.reporter_logins == ("karton",)


def test_entrypoint_reads_reporter_logins(clean_env, register_guard):
    clean_env.setenv("MWDB_MALLEUS_REPORTER_LOGINS", " karton , ,karton-2")
    malleus.entrypoint(MagicMock())
    assert hooks.MalleusHookHandler.reporter_logins == ("karton", "karton-2")


def test_configure_defaults(setup_mocks):
    ensure_defs, ensure_perms, _ = setup_mocks
    malleus.configure()
    ensure_defs.assert_called_once_with()
    ensure_perms.assert_called_once_with(
        ["karton"], ["jetpack-scan", "wpcloud-scan", "elderward"]
    )


def test_configure_env_split_strips_and_drops_empties(setup_mocks, clean_env):
    _, ensure_perms, _ = setup_mocks
    clean_env.setenv("MWDB_MALLEUS_SET_GROUPS", " a , ,b")
    clean_env.setenv("MWDB_MALLEUS_READ_GROUPS", "")
    malleus.configure()
    ensure_perms.assert_called_once_with(["a", "b"], [])


def test_configure_survives_failure_and_rolls_back(setup_mocks, clean_env):
    _, ensure_perms, fake_db = setup_mocks
    clean_env.setattr(
        attributes,
        "ensure_attribute_definitions",
        MagicMock(side_effect=RuntimeError("db down")),
    )
    malleus.configure()  # must not raise
    fake_db.session.rollback.assert_called_once_with()
    ensure_perms.assert_not_called()
