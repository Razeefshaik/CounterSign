"""Load the dashboard module independently of pytest's conftest import order."""

import importlib.util
import sys
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "src/countersign/api/dashboard.py"
spec = importlib.util.spec_from_file_location("dashboard_under_test", PATH)
dashboard = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = dashboard
spec.loader.exec_module(dashboard)
