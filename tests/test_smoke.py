"""Package smoke checks and executable money-write import boundary checks."""

import os
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest

import countersign


def test_installed_package_version_matches_module():
    assert countersign.__version__ == version("countersign")


@pytest.fixture
def contract_project(tmp_path):
    """Exercise the real CLI and project config without changing repository modules."""
    project_root = Path(__file__).resolve().parents[1]
    (tmp_path / "pyproject.toml").write_bytes((project_root / "pyproject.toml").read_bytes())
    for filename in (
        "countersign/__init__.py",
        "countersign/agent/__init__.py",
        "countersign/agent/commander.py",
        "countersign/db/__init__.py",
        "countersign/executor/__init__.py",
        "countersign/executor/protocol.py",
        "countersign/paypal/__init__.py",
        "countersign/paypal/read.py",
        "countersign/paypal/write.py",
        "countersign/paypal/sandbox_controls.py",
        "countersign/shield.py",
        "countersign/helper.py",
    ):
        module = tmp_path / filename
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text("", encoding="utf-8")
    return tmp_path


def run_import_linter(project):
    executable = Path(sys.executable).parent / (
        "lint-imports.exe" if os.name == "nt" else "lint-imports"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project)
    return subprocess.run(
        [str(executable), "--no-cache"],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize(
    "forbidden_module",
    (
        "countersign.paypal.write",
        "countersign.paypal.sandbox_controls",
        "countersign.executor",
        "countersign.db",
    ),
)
@pytest.mark.parametrize("indirect", (False, True), ids=("direct", "indirect"))
def test_agent_cannot_import_execution_internals(contract_project, forbidden_module, indirect):
    commander = contract_project / "countersign/agent/commander.py"
    if indirect:
        commander.write_text("import countersign.helper\n", encoding="utf-8")
        importer = contract_project / "countersign/helper.py"
    else:
        importer = commander
    importer.write_text(f"import {forbidden_module}\n", encoding="utf-8")

    result = run_import_linter(contract_project)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "Agent cannot reach money writes or core execution internals BROKEN" in result.stdout


@pytest.mark.parametrize(
    "importer",
    ("countersign/paypal/read.py", "countersign/shield.py", "countersign/__init__.py"),
)
def test_non_executor_cannot_import_write_adapter(contract_project, importer):
    (contract_project / importer).write_text("import countersign.paypal.write\n", encoding="utf-8")

    result = run_import_linter(contract_project)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "Only the executor may import the PayPal write adapter BROKEN" in result.stdout


@pytest.mark.parametrize("importer", ("__init__.py", "protocol.py"))
def test_executor_can_import_write_adapter(contract_project, importer):
    (contract_project / "countersign/executor" / importer).write_text(
        "import countersign.paypal.write\n", encoding="utf-8"
    )

    result = run_import_linter(contract_project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Contracts: 2 kept, 0 broken." in result.stdout


def test_agent_can_import_read_adapter(contract_project):
    (contract_project / "countersign/agent/commander.py").write_text(
        "from countersign.paypal import read\n", encoding="utf-8"
    )

    result = run_import_linter(contract_project)

    assert result.returncode == 0, result.stdout + result.stderr
