from unittest.mock import MagicMock


def test_entrypoint_registers_resources(monkeypatch):
    import wpsandbox
    monkeypatch.setattr("wpsandbox.model.ensure_schema", lambda: True)
    monkeypatch.setattr("wpsandbox.attributes.ensure_attribute_definitions", lambda: None)
    ctx = MagicMock()
    wpsandbox.entrypoint(ctx)
    urls = [c.args[1] for c in ctx.register_resource.call_args_list]
    assert "/wpsandbox/<hash64:identifier>" in urls
    assert "/wpsandbox/run/<run_id>" in urls


def test_ensure_attribute_definitions_creates_missing(monkeypatch):
    from wpsandbox import attributes

    existing = {"c2_host"}
    added = []
    committed = []

    class FakeSession:
        def add(self, obj):
            added.append(obj.key)

        def commit(self):
            committed.append(True)

    class FakeDef:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    monkeypatch.setattr(attributes, "_session", lambda: FakeSession())
    monkeypatch.setattr(attributes, "_definition_cls", lambda: FakeDef)
    monkeypatch.setattr(attributes, "_definition_exists", lambda session, cls, key: key in existing)
    attributes.ensure_attribute_definitions()
    assert sorted(added) == ["dropped_file", "wp_user_added"]
    assert committed == [True]


def test_ensure_attribute_definitions_noop_when_all_exist(monkeypatch):
    from wpsandbox import attributes

    class FakeSession:
        def add(self, obj):
            raise AssertionError("should not add")

        def commit(self):
            raise AssertionError("should not commit")

    monkeypatch.setattr(attributes, "_session", lambda: FakeSession())
    monkeypatch.setattr(attributes, "_definition_cls", lambda: object)
    monkeypatch.setattr(attributes, "_definition_exists", lambda session, cls, key: True)
    attributes.ensure_attribute_definitions()
