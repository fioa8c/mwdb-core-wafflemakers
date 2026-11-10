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


@pytest.mark.parametrize(
    "g_value", [SimpleNamespace(), SimpleNamespace(auth_user=None)]
)
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


@pytest.fixture
def reporter(env, monkeypatch):
    monkeypatch.setattr(
        hooks, "g", SimpleNamespace(auth_user=SimpleNamespace(login="karton"))
    )
    monkeypatch.setattr(hooks.MalleusHookHandler, "reporter_logins", ("karton",))
    return env


def test_hook_removes_source_tag_created_by_reporter(reporter):
    obj = MagicMock()
    hooks.MalleusHookHandler().on_created_tag(obj, _tag("source:x"))
    obj.remove_tag.assert_called_once_with("source:x", commit=False)
    obj.add_tag.assert_called_once_with("wm:source-stripped", commit=False)
    reporter.session.commit.assert_called_once_with()


def test_hook_keeps_other_tags_created_by_reporter(reporter):
    obj = MagicMock()
    hooks.MalleusHookHandler().on_created_tag(obj, _tag("script:php"))
    assert obj.mock_calls == []
    reporter.session.commit.assert_not_called()


def test_reporter_logins_come_from_the_class_attribute(reporter, monkeypatch):
    monkeypatch.setattr(hooks.MalleusHookHandler, "reporter_logins", ("other",))
    obj = MagicMock()
    hooks.MalleusHookHandler().on_created_tag(obj, _tag("source:x"))
    assert obj.mock_calls == []


@pytest.fixture
def env_reporter(reporter):
    return reporter


def test_hook_verdict_tag_by_reporter_clears(env_reporter, monkeypatch):
    calls = []
    monkeypatch.setattr(
        hooks.reanalysis,
        "clear_previous_verdict",
        lambda o, t: calls.append((o, t)) or ["verdict:old", "wm:needs-review"],
    )
    obj = MagicMock()
    hooks.MalleusHookHandler().on_created_tag(obj, _tag("verdict:malicious"))
    assert calls == [(obj, "verdict:malicious")]
    env_reporter.session.commit.assert_called_once_with()
    env_reporter.session.rollback.assert_not_called()


def test_hook_verdict_tag_clears_real_tags(env_reporter):
    obj = MagicMock(
        tags=[_tag("verdict:old"), _tag("verdict:new"), _tag("wm:needs-review")]
    )
    hooks.MalleusHookHandler().on_created_tag(obj, _tag("verdict:new"))
    removed = sorted(c.args[0] for c in obj.remove_tag.call_args_list)
    assert removed == ["verdict:old", "wm:needs-review"]
    env_reporter.session.commit.assert_called_once_with()


def test_hook_verdict_tag_by_researcher_does_nothing(env, monkeypatch):
    monkeypatch.setattr(
        hooks, "g", SimpleNamespace(auth_user=SimpleNamespace(login="alice"))
    )
    obj = MagicMock(tags=[_tag("verdict:old"), _tag("verdict:new")])
    hooks.MalleusHookHandler().on_created_tag(obj, _tag("verdict:new"))
    obj.remove_tag.assert_not_called()
    env.session.commit.assert_not_called()


def test_hook_non_verdict_tag_by_reporter_does_not_clear(env_reporter, monkeypatch):
    called = []
    monkeypatch.setattr(
        hooks.reanalysis, "clear_previous_verdict", lambda o, t: called.append(t)
    )
    obj = MagicMock()
    hooks.MalleusHookHandler().on_created_tag(obj, _tag("yara:rule"))
    assert called == []
    env_reporter.session.commit.assert_not_called()


def test_hook_stripped_source_tag_skips_verdict_cleanup(env_reporter, monkeypatch):
    called = []
    monkeypatch.setattr(
        hooks.reanalysis, "clear_previous_verdict", lambda o, t: called.append(t)
    )
    obj = MagicMock()
    hooks.MalleusHookHandler().on_created_tag(obj, _tag("source:x"))
    assert called == []
    obj.remove_tag.assert_called_once_with("source:x", commit=False)


def test_hook_analysis_attr_by_reporter_clears(env_reporter, monkeypatch):
    calls = []
    monkeypatch.setattr(
        hooks.reanalysis,
        "clear_previous_analysis",
        lambda o, a: calls.append((o, a)) or 5,
    )
    obj = MagicMock()
    anchor = _attr("malleus_analysis", "uid-1")
    hooks.MalleusHookHandler().on_created_attribute(obj, anchor)
    assert calls == [(obj, anchor)]


def test_hook_analysis_attr_by_scanner_does_nothing(env, monkeypatch):
    called = []
    monkeypatch.setattr(
        hooks.reanalysis, "clear_previous_analysis", lambda o, a: called.append(a)
    )
    hooks.MalleusHookHandler().on_created_attribute(
        MagicMock(), _attr("malleus_analysis", "uid-1")
    )
    assert called == []


def test_hook_analysis_attr_without_auth_user_does_nothing(env, monkeypatch):
    monkeypatch.setattr(hooks, "g", SimpleNamespace())
    called = []
    monkeypatch.setattr(
        hooks.reanalysis, "clear_previous_analysis", lambda o, a: called.append(a)
    )
    hooks.MalleusHookHandler().on_created_attribute(
        MagicMock(), _attr("malleus_analysis", "uid-1")
    )
    assert called == []


def test_hook_other_attribute_by_reporter_does_nothing(env_reporter, monkeypatch):
    called = []
    monkeypatch.setattr(
        hooks.reanalysis, "clear_previous_analysis", lambda o, a: called.append(a)
    )
    hooks.MalleusHookHandler().on_created_attribute(
        MagicMock(), _attr("malleus_reasons", "x")
    )
    assert called == []


def test_hook_cleanup_failure_is_logged_not_raised(env_reporter, monkeypatch, caplog):
    def boom(*args):
        raise RuntimeError("db down")

    monkeypatch.setattr(hooks.reanalysis, "clear_previous_analysis", boom)
    monkeypatch.setattr(hooks.reanalysis, "clear_previous_verdict", boom)
    obj = MagicMock()
    with caplog.at_level(logging.ERROR, logger="mwdb.plugin.malleus"):
        hooks.MalleusHookHandler().on_created_attribute(
            obj, _attr("malleus_analysis", "uid-1")
        )
        hooks.MalleusHookHandler().on_created_tag(obj, _tag("verdict:malicious"))
    assert env_reporter.session.rollback.call_count == 2
    assert [r.levelno for r in caplog.records] == [logging.ERROR, logging.ERROR]
    assert "db down" in caplog.text
