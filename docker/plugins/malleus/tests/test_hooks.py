import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from malleus import hooks


@pytest.fixture
def env(monkeypatch):
    fake_db = MagicMock()
    monkeypatch.setattr(hooks, "db", fake_db)
    monkeypatch.setattr(
        hooks, "g", SimpleNamespace(auth_user=SimpleNamespace(login="jetpack-scan"))
    )
    return fake_db


def _attr(key, value):
    return SimpleNamespace(key=key, value=value)


def test_hook_strips_context_and_tags(env):
    obj = MagicMock()
    value = {"scanner": "v2", "site_id": 1}

    hooks.MalleusHookHandler().on_created_attribute(obj, _attr("context", value))

    obj.remove_attribute.assert_called_once_with(
        "context", value, check_permissions=False
    )
    obj.add_attribute.assert_called_once()
    args, kwargs = obj.add_attribute.call_args
    assert args == ("context", {"scanner": "v2"})
    assert kwargs["check_permissions"] is False
    assert kwargs["commit"] is False
    obj.add_tag.assert_called_once_with("wm:context-stripped", commit=False)
    env.session.commit.assert_called_once_with()


def test_hook_strips_everything_when_no_key_allowed(env):
    obj = MagicMock()
    hooks.MalleusHookHandler().on_created_attribute(
        obj, _attr("context", {"site_id": 1})
    )
    assert obj.add_attribute.call_args.args == ("context", {})
    obj.add_tag.assert_called_once_with("wm:context-stripped", commit=False)


def test_hook_ignores_clean_context(env):
    obj = MagicMock()
    hooks.MalleusHookHandler().on_created_attribute(
        obj, _attr("context", {"scanner": "v2", "path": "a/b.php"})
    )
    assert obj.mock_calls == []
    env.session.commit.assert_not_called()


@pytest.mark.parametrize("value", ["bare string", ["site_id"], 7, None])
def test_hook_leaves_non_object_context_untouched(env, value):
    obj = MagicMock()
    hooks.MalleusHookHandler().on_created_attribute(obj, _attr("context", value))
    assert obj.mock_calls == []
    env.session.commit.assert_not_called()


def test_hook_ignores_other_attributes(env):
    obj = MagicMock()
    hooks.MalleusHookHandler().on_created_attribute(
        obj, _attr("malleus_verdict", {"site_id": 1})
    )
    assert obj.mock_calls == []


def test_hook_does_not_log_context_values(env, caplog):
    obj = MagicMock()
    with caplog.at_level(logging.DEBUG, logger="mwdb.plugin.malleus"):
        hooks.MalleusHookHandler().on_created_attribute(
            obj, _attr("context", {"scanner": "v2", "site_id": "SECRET-VALUE"})
        )
    assert "site_id" in caplog.text
    assert "SECRET-VALUE" not in caplog.text


def test_hook_swallows_errors_and_rolls_back(env):
    obj = MagicMock()
    obj.add_attribute.side_effect = RuntimeError("db down")
    hooks.MalleusHookHandler().on_created_attribute(
        obj, _attr("context", {"site_id": 1})
    )
    env.session.rollback.assert_called_once_with()


def _tag(name):
    return SimpleNamespace(tag=name)


def test_hook_removes_foreign_source_tag(env):
    obj = MagicMock()
    hooks.MalleusHookHandler().on_created_tag(obj, _tag("source:wpcloud-scan"))

    obj.remove_tag.assert_called_once_with("source:wpcloud-scan", commit=False)
    obj.add_tag.assert_called_once_with("wm:source-stripped", commit=False)
    env.session.commit.assert_called_once_with()


def test_hook_keeps_own_source_tag(env):
    obj = MagicMock()
    hooks.MalleusHookHandler().on_created_tag(obj, _tag("source:jetpack-scan"))
    assert obj.mock_calls == []
    env.session.commit.assert_not_called()


def test_hook_keeps_non_source_tag(env):
    obj = MagicMock()
    hooks.MalleusHookHandler().on_created_tag(obj, _tag("wm:source-stripped"))
    assert obj.mock_calls == []


def test_hook_allows_researcher_source_tag(env, monkeypatch):
    monkeypatch.setattr(
        hooks, "g", SimpleNamespace(auth_user=SimpleNamespace(login="researcher"))
    )
    obj = MagicMock()
    hooks.MalleusHookHandler().on_created_tag(obj, _tag("source:wpcloud-scan"))
    assert obj.mock_calls == []


@pytest.mark.parametrize("g_value", [SimpleNamespace(), SimpleNamespace(auth_user=None)])
def test_hook_without_auth_user_does_nothing(env, monkeypatch, g_value):
    monkeypatch.setattr(hooks, "g", g_value)
    obj = MagicMock()
    hooks.MalleusHookHandler().on_created_tag(obj, _tag("source:wpcloud-scan"))
    assert obj.mock_calls == []
    env.session.commit.assert_not_called()


def test_tag_hook_swallows_errors_and_rolls_back(env):
    obj = MagicMock()
    obj.remove_tag.side_effect = RuntimeError("db down")
    hooks.MalleusHookHandler().on_created_tag(obj, _tag("source:wpcloud-scan"))
    env.session.rollback.assert_called_once_with()
