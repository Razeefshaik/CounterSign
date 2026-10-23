from enum import StrEnum

import pytest

from countersign.fsm import IllegalTransition, Machine


class Light(StrEnum):
    RED = "RED"
    GREEN = "GREEN"


class Signal(StrEnum):
    GO = "GO"
    STOP = "STOP"


LIGHT = Machine("light", Light, Signal, {(Light.RED, Signal.GO): Light.GREEN})


def test_next_returns_the_table_target_and_accepts_plain_strings():
    assert LIGHT.next(Light.RED, Signal.GO) is Light.GREEN
    assert LIGHT.next("RED", "GO") is Light.GREEN
    assert LIGHT.allowed(Light.RED) == {Signal.GO}
    assert LIGHT.allowed(Light.GREEN) == frozenset()


@pytest.mark.parametrize(("state", "event"), [("GREEN", "GO"), ("RED", "STOP"), ("BLUE", "GO")])
def test_moves_outside_the_table_raise_illegal_transition(state, event):
    with pytest.raises(IllegalTransition):
        LIGHT.next(state, event)


def test_tables_must_use_the_machines_own_enums():
    with pytest.raises(TypeError):
        Machine("bad", Light, Signal, {(Light.RED, "GO"): Light.GREEN})
