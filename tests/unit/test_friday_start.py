"""C62: invalid startup inputs fail before starting any service."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") if os.name != "nt" else "C:/Program Files/Git/bin/bash.exe"


@pytest.mark.parametrize(("values", "code"), [
    ({"COUNTERSIGN_HOSTED_START":"sunday"}, "must be friday or monday"),
    ({"COUNTERSIGN_HOSTED_START":"friday", "COUNTERSIGN_HOSTED_SEED":"week"}, "requires"),
    ({"COUNTERSIGN_HOSTED_IDLE_MINUTES":"-1"}, "nonnegative"),
    ({"COUNTERSIGN_HOSTED_IDLE_MINUTES":"10081"}, "between"),
    ({"COUNTERSIGN_HOSTED_START":"monday", "PAYPAL_CLIENT_SECRET":"fake"}, "refusing"),
    ({"COUNTERSIGN_HOSTED_START":"friday", "GOOGLE_API_KEY":"fake"}, "refusing"),
])
def test_invalid_hosted_configuration(values, code, tmp_path):
    if not BASH or not Path(BASH).exists():
        pytest.skip("bash is unavailable")
    script = tmp_path / "start.sh"
    script.write_text((ROOT / "scripts/hosted_start.sh").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    env = {"PATH":os.environ.get("PATH", ""), "SYSTEMROOT":os.environ.get("SYSTEMROOT", "C:/Windows"), **values}
    result = subprocess.run([BASH, str(script)], capture_output=True, text=True, env=env, check=False, timeout=10)
    assert result.returncode == 64
    assert code in result.stdout
    assert "starting PostgreSQL" not in result.stdout
