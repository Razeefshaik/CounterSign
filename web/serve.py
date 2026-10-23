"""Local preview only. Mount the dashboard GET API when M07 is available."""

import importlib.util
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI()
dashboard = ROOT / "src/countersign/api/dashboard.py"
if dashboard.exists():
    spec = importlib.util.spec_from_file_location(
        "countersign_dashboard_preview", dashboard
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    app.include_router(module.router, prefix="/api/v1")
app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8100)
