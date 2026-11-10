import logging

import pytest

from malleus import attributes


class _Col:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return (self.name, other)

    def __hash__(self):
        return hash(self.name)


class FakeAttributeDefinition:
    key = _Col("key")

    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeAttributePermission:
    key = _Col("key")
    group_id = _Col("group_id")

    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeGroup:
    registry = {}

    def __init__(self, id, name):
        self.id, self.name = id, name

    @staticmethod
    def get_by_name(name):
        return FakeGroup.registry.get(name)


class FakeQuery:
    def __init__(self, session, cls):
        self.session, self.cls, self.conds = session, cls, []

    def filter(self, *conds):
        self.conds.extend(conds)
        return self

    def first(self):
        for row in self.session.rows[self.cls]:
            if all(getattr(row, name) == value for name, value in self.conds):
                return row
        return None


class FakeSession:
    def __init__(self):
        self.rows = {FakeAttributeDefinition: [], FakeAttributePermission: []}
        self.commits = 0
        self.added = []

    def query(self, cls):
        return FakeQuery(self, cls)

    def add(self, obj):
        self.added.append(obj)
        self.rows[type(obj)].append(obj)

    def commit(self):
        self.commits += 1


@pytest.fixture
def session(monkeypatch):
    s = FakeSession()
    FakeGroup.registry = {
        name: FakeGroup(i, name)
        for i, name in enumerate(
            ["karton", "jetpack-scan", "wpcloud-scan", "elderward"], start=1
        )
    }
    monkeypatch.setattr(attributes, "_session", lambda: s)
    monkeypatch.setattr(
        attributes,
        "_models",
        lambda: (FakeAttributeDefinition, FakeAttributePermission, FakeGroup),
    )
    return s


def _perms(session, group_name):
    gid = FakeGroup.registry[group_name].id
    return {
        p.key: p for p in session.rows[FakeAttributePermission] if p.group_id == gid
    }


def test_attribute_keys_exact_and_described():
    assert attributes.ATTRIBUTE_KEYS == (
        "malleus_verdict",
        "malleus_confidence",
        "malleus_reasons",
        "malleus_stages",
        "malleus_depth",
        "malleus_gate",
        "malleus_gate_disagreement",
        "malleus_rules_commit",
        "malleus_unlocked",
        "malleus_model",
        "malleus_prompt_sha256",
        "malleus_llm",
        "context",
        "layer_variant",
        "layer_unlocked",
        "malleus_analysis",
        "sandbox_level",
        "sandbox_coverage",
        "sandbox_network",
        "sandbox_files_written",
        "sandbox_dangerous_calls",
    )
    assert set(attributes.DESCRIPTIONS) == set(attributes.ATTRIBUTE_KEYS)
    assert all(attributes.DESCRIPTIONS.values())


def test_ensure_attribute_definitions_creates_all_once(session):
    attributes.ensure_attribute_definitions()
    keys = [d.key for d in session.rows[FakeAttributeDefinition]]
    assert keys == list(attributes.ATTRIBUTE_KEYS)
    defn = session.rows[FakeAttributeDefinition][0]
    assert defn.rich_template == "" and defn.hidden is False
    assert session.commits == 1


def test_ensure_attribute_definitions_is_idempotent(session):
    attributes.ensure_attribute_definitions()
    attributes.ensure_attribute_definitions()
    assert len(session.rows[FakeAttributeDefinition]) == len(attributes.ATTRIBUTE_KEYS)
    assert session.commits == 1  # second run had nothing to commit


def test_ensure_attribute_definitions_adds_only_missing(session):
    session.rows[FakeAttributeDefinition].append(
        FakeAttributeDefinition(
            key="malleus_verdict",
            description=attributes.DESCRIPTIONS["malleus_verdict"],
        )
    )
    attributes.ensure_attribute_definitions()
    assert len(session.added) == len(attributes.ATTRIBUTE_KEYS) - 1
    assert session.commits == 1


def test_pipeline_keys_exclude_scanner_and_layer_keys():
    assert attributes.ANALYSIS_KEY == "malleus_analysis"
    assert set(attributes.ATTRIBUTE_KEYS) - set(attributes.PIPELINE_KEYS) == {
        "context",
        "layer_variant",
        "layer_unlocked",
    }
    assert attributes.ANALYSIS_KEY in attributes.PIPELINE_KEYS
    assert "sandbox_level" in attributes.PIPELINE_KEYS


def test_corrected_descriptions():
    d = attributes.DESCRIPTIONS
    assert d["malleus_depth"] == "Analysis depth: none, auto or full"
    assert d["layer_unlocked"] == "Rules this layer matched that the sample did not"
    assert d["sandbox_level"] == "Kadath sandbox evidence level (red, amber, green)"


def test_existing_definition_description_updated(session):
    stale = FakeAttributeDefinition(key="malleus_depth", description="old text")
    session.rows[FakeAttributeDefinition].append(stale)
    attributes.ensure_attribute_definitions()
    assert stale.description == "Analysis depth: none, auto or full"
    assert len(session.added) == len(attributes.ATTRIBUTE_KEYS) - 1


def test_description_update_alone_commits(session):
    attributes.ensure_attribute_definitions()
    assert session.commits == 1
    session.rows[FakeAttributeDefinition][0].description = "drifted"
    attributes.ensure_attribute_definitions()
    assert session.commits == 2
    assert (
        session.rows[FakeAttributeDefinition][0].description
        == attributes.DESCRIPTIONS["malleus_verdict"]
    )


def test_set_group_can_read_and_set_everything(session):
    attributes.ensure_group_permissions(["karton"], [])
    perms = _perms(session, "karton")
    assert set(perms) == set(attributes.ATTRIBUTE_KEYS)
    assert all(p.can_read is True and p.can_set is True for p in perms.values())
    assert session.commits == 1


def test_read_group_can_set_only_context(session):
    attributes.ensure_group_permissions([], ["jetpack-scan"])
    perms = _perms(session, "jetpack-scan")
    assert set(perms) == set(attributes.ATTRIBUTE_KEYS)
    assert all(p.can_read is True for p in perms.values())
    assert [k for k, p in perms.items() if p.can_set] == ["context"]


def test_ensure_group_permissions_skips_missing_group(session, caplog):
    with caplog.at_level(logging.WARNING, logger="mwdb.plugin.malleus"):
        attributes.ensure_group_permissions(["nope"], ["also-nope"])
    assert session.added == []
    assert "nope" in caplog.text and "also-nope" in caplog.text


def test_ensure_group_permissions_is_idempotent(session):
    attributes.ensure_group_permissions(["karton"], ["jetpack-scan", "elderward"])
    first = len(session.added)
    attributes.ensure_group_permissions(["karton"], ["jetpack-scan", "elderward"])
    assert len(session.added) == first
    assert first == 3 * len(attributes.ATTRIBUTE_KEYS)


def test_ensure_group_permissions_updates_existing_row(session):
    gid = FakeGroup.registry["jetpack-scan"].id
    stale = FakeAttributePermission(
        key="malleus_verdict", group_id=gid, can_read=False, can_set=True
    )
    session.rows[FakeAttributePermission].append(stale)
    attributes.ensure_group_permissions([], ["jetpack-scan"])
    assert stale.can_read is True and stale.can_set is False
    assert len(_perms(session, "jetpack-scan")) == len(attributes.ATTRIBUTE_KEYS)
