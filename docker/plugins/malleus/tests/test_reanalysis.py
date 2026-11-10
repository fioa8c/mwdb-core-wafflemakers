from types import SimpleNamespace as ns
from unittest.mock import MagicMock, call

from malleus import reanalysis


def test_clear_previous_verdict_removes_other_verdicts_and_review():
    obj = MagicMock(
        tags=[
            ns(tag="verdict:undetermined"),
            ns(tag="verdict:likely-benign"),
            ns(tag="wm:needs-review"),
            ns(tag="yara:r"),
            ns(tag="source:researcher"),
        ]
    )
    removed = reanalysis.clear_previous_verdict(obj, "verdict:likely-benign")
    assert removed == ["verdict:undetermined", "wm:needs-review"]
    obj.remove_tag.assert_has_calls(
        [
            call("verdict:undetermined", commit=False),
            call("wm:needs-review", commit=False),
        ],
        any_order=True,
    )
    assert obj.remove_tag.call_count == 2


def test_clear_previous_verdict_with_nothing_stale():
    obj = MagicMock(tags=[ns(tag="verdict:malicious"), ns(tag="yara:r")])
    assert reanalysis.clear_previous_verdict(obj, "verdict:malicious") == []
    obj.remove_tag.assert_not_called()


def test_clear_previous_verdict_returns_sorted_names():
    obj = MagicMock(
        tags=[
            ns(tag="wm:needs-review"),
            ns(tag="verdict:b"),
            ns(tag="verdict:a"),
            ns(tag="verdict:new"),
        ]
    )
    assert reanalysis.clear_previous_verdict(obj, "verdict:new") == [
        "verdict:a",
        "verdict:b",
        "wm:needs-review",
    ]


def _attrs(*pairs):
    return [ns(id=i, key=k) for i, k in pairs]


def test_clear_previous_analysis_removes_other_pipeline_values(monkeypatch):
    rows = _attrs(
        (1, "malleus_analysis"),
        (2, "malleus_analysis"),
        (3, "malleus_reasons"),
        (4, "sandbox_level"),
        (5, "context"),
        (6, "malleus_unlocked"),
    )
    anchor = rows[0]

    def fake_stale(object, a):
        # What the real query returns: pipeline keys only, anchor excluded.
        return [
            r for r in rows if r.key in reanalysis.PIPELINE_KEYS and r.id != a.id
        ]

    monkeypatch.setattr(reanalysis, "_stale_attributes", fake_stale)
    obj = MagicMock()
    assert reanalysis.clear_previous_analysis(obj, anchor) == 4
    assert [c.args for c in obj.remove_attribute_by_id.call_args_list] == [
        (2,),
        (3,),
        (4,),
        (6,),
    ]
    assert all(
        c.kwargs == {"check_permissions": False}
        for c in obj.remove_attribute_by_id.call_args_list
    )


def test_clear_previous_analysis_nothing_to_remove(monkeypatch):
    monkeypatch.setattr(reanalysis, "_stale_attributes", lambda o, a: [])
    obj = MagicMock()
    assert reanalysis.clear_previous_analysis(obj, ns(id=1)) == 0
    obj.remove_attribute_by_id.assert_not_called()


def test_stale_attributes_queries_the_pipeline_keys(monkeypatch):
    class Col:
        def __init__(self, name):
            self.name = name

        def __eq__(self, other):
            return ("eq", self.name, other)

        def __ne__(self, other):
            return ("ne", self.name, other)

        def in_(self, values):
            return ("in", self.name, tuple(values))

    class FakeAttribute:
        object_id = Col("object_id")
        key = Col("key")
        id = Col("id")

    seen = {}
    query = MagicMock()
    query.filter.side_effect = lambda *conds: seen.setdefault("conds", conds) and query
    query.all.return_value = ["row"]
    session = MagicMock()
    session.query.return_value = query
    monkeypatch.setattr(reanalysis, "_attribute_model", lambda: FakeAttribute)
    monkeypatch.setattr(reanalysis, "_session", lambda: session)

    rows = reanalysis._stale_attributes(ns(id=9), ns(id=1))

    assert rows == ["row"]
    session.query.assert_called_once_with(FakeAttribute)
    assert seen["conds"] == (
        ("eq", "object_id", 9),
        ("in", "key", tuple(reanalysis.PIPELINE_KEYS)),
        ("ne", "id", 1),
    )
