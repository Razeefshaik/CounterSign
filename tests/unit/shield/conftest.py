import pytest
from shield_world import World


@pytest.fixture
def world() -> World:
    return World()
