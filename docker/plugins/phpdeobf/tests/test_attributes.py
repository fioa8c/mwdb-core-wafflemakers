"""Unit tests for phpdeobf.attributes.ensure_attribute_definition.

The DB layer is replaced by a tiny in-memory fake session so the test can
assert that repeated startups create the definition and the `registered`
group's read permission exactly once.
"""
import logging
from unittest.mock import MagicMock

import pytest


class _Model:
    key = "key"
    group_id = "group_id"

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeAttributeDefinition(_Model):
    pass


class FakeAttributePermission(_Model):
    pass


class FakeQuery:
    """Chainable query: every filter() is ignored, first() returns the first
    previously added row of the queried model (the tests only ever hold rows
    for phpdeobf_sink / the registered group)."""

    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args):
        return self

    def first(self):
        return self.rows[0] if self.rows else None


class FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0

    def query(self, model):
        return FakeQuery([o for o in self.added if isinstance(o, model)])

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1


@pytest.fixture
def fake_db(monkeypatch):
    session = FakeSession()
    db = MagicMock()
    db.session = session
    group = MagicMock()
    group.id = 7
    fake_group_cls = MagicMock()
    fake_group_cls.get_by_name.return_value = group
    monkeypatch.setattr("mwdb.model.db", db)
    monkeypatch.setattr("mwdb.model.AttributeDefinition", FakeAttributeDefinition)
    monkeypatch.setattr("mwdb.model.AttributePermission", FakeAttributePermission)
    monkeypatch.setattr("mwdb.model.Group", fake_group_cls)
    return session, fake_group_cls


def test_definition_and_registered_read_permission_created_once(fake_db):
    session, group_cls = fake_db
    from phpdeobf import attributes

    attributes.ensure_attribute_definition()
    attributes.ensure_attribute_definition()

    defs = [o for o in session.added if isinstance(o, FakeAttributeDefinition)]
    perms = [o for o in session.added if isinstance(o, FakeAttributePermission)]
    assert len(defs) == 1
    assert defs[0].key == "phpdeobf_sink"
    assert len(perms) == 1
    assert perms[0].key == "phpdeobf_sink"
    assert perms[0].group_id == 7
    assert perms[0].can_read is True
    assert perms[0].can_set is False
    group_cls.get_by_name.assert_called_with("registered")


def test_permission_added_when_definition_already_exists(fake_db):
    session, _ = fake_db
    session.added.append(FakeAttributeDefinition(key="phpdeobf_sink"))
    from phpdeobf import attributes

    attributes.ensure_attribute_definition()

    perms = [o for o in session.added if isinstance(o, FakeAttributePermission)]
    assert len(perms) == 1


def test_missing_registered_group_warns_and_skips(fake_db, caplog):
    session, group_cls = fake_db
    group_cls.get_by_name.return_value = None
    from phpdeobf import attributes

    with caplog.at_level(logging.WARNING, logger="mwdb.plugin.phpdeobf"):
        attributes.ensure_attribute_definition()

    assert not [o for o in session.added if isinstance(o, FakeAttributePermission)]
    assert [o for o in session.added if isinstance(o, FakeAttributeDefinition)]
    assert "registered" in caplog.text
