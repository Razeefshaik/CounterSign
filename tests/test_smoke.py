"""Smoke checks for the installed CounterSign package."""

from importlib.metadata import version

import countersign


def test_installed_package_version_matches_module():
    assert countersign.__version__ == version("countersign")
