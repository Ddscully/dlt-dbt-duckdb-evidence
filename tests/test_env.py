"""`modern_data_stack.env.flag`, which every on/off switch reads through:
`INGEST_FIXTURES`, `INGEST_FX_FULL` and `INGEST_WDI_FULL`."""

from __future__ import annotations

import pytest

from modern_data_stack import env


@pytest.mark.parametrize("value", ["1", "true", "True", "YES"])
def test_a_switch_is_on_for_each_spelling_of_yes(monkeypatch, value):
    monkeypatch.setenv("SOME_SWITCH", value)
    assert env.flag("SOME_SWITCH") is True


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "on"])
def test_a_switch_is_off_for_anything_else(monkeypatch, value):
    # "on" is off. Adding it to `TRUE` would turn it on for every switch at once.
    if value is None:
        monkeypatch.delenv("SOME_SWITCH", raising=False)
    else:
        monkeypatch.setenv("SOME_SWITCH", value)
    assert env.flag("SOME_SWITCH") is False
