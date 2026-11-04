"""Offline wire proof: inspect the CLI request at a local, non-model HTTP stub."""

import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from countersign.agent.codex_cli import command, isolated_catalog, schema_for
from countersign.agent.models import ModelError


def prove(binary, model, catalog):
    captured = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            captured.append({"tools": data.get("tools", []), "model": data.get("model")})
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"offline proof complete","type":"invalid_request_error"}}')

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="c67-tool-proof-") as folder:
            root = Path(folder)
            cwd, home = root / "empty", root / "home"
            cwd.mkdir()
            home.mkdir()
            output, models = root / "output.json", root / "catalog.json"
            output.write_text(json.dumps(schema_for([])), encoding="utf-8")
            models.write_text(json.dumps(isolated_catalog(catalog, model)), encoding="utf-8")
            args = command(binary, model, cwd, output, models)
            args[-1:-1] = ["-c", 'model_provider="c67_probe"',
                "-c", 'model_providers.c67_probe.name="C67 offline probe"',
                "-c", f'model_providers.c67_probe.base_url="http://127.0.0.1:{server.server_port}/v1"',
                "-c", 'model_providers.c67_probe.wire_api="responses"',
                "-c", 'model_providers.c67_probe.requires_openai_auth=false',
                "-c", 'model_providers.c67_probe.request_max_retries=0',
                "-c", 'model_providers.c67_probe.stream_max_retries=0']
            env = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
            env["CODEX_HOME"] = str(home)
            process = subprocess.run(args, check=False, cwd=cwd, env=env, input=b"Return a JSON proposal with no tool calls.",
                capture_output=True, timeout=35,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if not captured:
                # Only fixed config diagnostics, never arbitrary CLI output.
                diagnostic = "unavailable"
                for known in ("unknown field", "unknown feature", "invalid type", "error parsing"):
                    if known.encode() in process.stderr.lower():
                        diagnostic = known
                raise ModelError("MODEL_TOOL_DISABLE_PROOF_FAILED:" + diagnostic)
            if any(row["tools"] for row in captured):
                raise ModelError("MODEL_NATIVE_TOOLS_ENABLED")
            return {"offline": True, "request_count": len(captured), "native_tools": 0,
                    "authenticated": False, "external_connections": 0}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
