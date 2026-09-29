import pytest

from phpdeobf.presets import OPTION_KEYS, resolve

ALL_ON = {k: True for k in OPTION_KEYS}


def test_default_is_full_and_sniffs_eval():
    assert resolve(None, b"<?php eval($x);") == ALL_ON
    assert resolve({}, b"<?php EVAL($x);")["eval_peel"] is True
    no_eval = resolve({"preset": "full"}, b"<?php echo 1;")
    assert no_eval == {**ALL_ON, "eval_peel": False}


def test_static_is_all_off():
    assert resolve({"preset": "static"}, b"<?php eval($x);") == {k: False for k in OPTION_KEYS}


def test_custom_takes_explicit_map_with_false_defaults():
    r = resolve({"preset": "custom", "options": {"analyze": True}}, b"<?php eval($x);")
    assert r == {**{k: False for k in OPTION_KEYS}, "analyze": True}


@pytest.mark.parametrize("body", [
    {"preset": "nope"},
    {"preset": "custom", "options": {"analyze": "yes"}},
    {"preset": "custom", "options": {"unknown": True}},
    {"preset": "custom", "options": "x"},
])
def test_bad_bodies_raise(body):
    with pytest.raises(ValueError):
        resolve(body, b"")
