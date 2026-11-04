"""Real CLI stage-one rehearsal: loopback only, fresh home, no login or model."""

import json
import secrets
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from countersign.agent import codex_cli
from scripts import c67_cli_stage1 as stage


def sse(kind, **body):
    return f"event: {kind}\ndata: {json.dumps({'type': kind, **body})}\n\n".encode()


@pytest.mark.parametrize("echo_canary", [False, True], ids=["passed", "canary-disclosure-stops"])
def test_real_stage_one_cli_rehearsal(monkeypatch, tmp_path, echo_canary):
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(stage, "RECORD", tmp_path / "record" / "run.json")
    # A random, transient test value lets the stub deliberately exercise disclosure.
    # Neither this value nor request/prompt contents are exported from the test.
    canary = secrets.token_hex(32)
    monkeypatch.setattr(stage.secrets, "token_hex", lambda _size: canary)
    observed = {"requests": 0, "native_tools": [], "auth_headers": [], "tool_result_seen": False}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed["requests"] += 1
            number = observed["requests"]
            observed["native_tools"].append(len(data.get("tools", [])))
            observed["auth_headers"].append("Authorization" in self.headers)
            if number == 2:
                for message in data.get("input", []):
                    for part in message.get("content", []) if isinstance(message.get("content"), list) else []:
                        if part.get("type") != "input_text":
                            continue
                        try:
                            prompt = json.loads(part["text"])
                        except (ValueError, KeyError):
                            continue
                        for turn in prompt.get("conversation", []) if isinstance(prompt, dict) else []:
                            if (isinstance(turn, dict) and turn.get("type") == "tool"
                                    and turn.get("tool_call_id") == "offline-read"
                                    and turn.get("name") == "get_payout_item"):
                                observed["tool_result_seen"] = json.loads(turn["content"]) == {
                                    "untrusted_result": {"status": "UNCLAIMED"}}
            reply = {"tool_calls": [], "proposal_json": "{}"}
            if number == 1:
                reply = {"tool_calls": [{"id": "offline-read", "name": "get_payout_item",
                    "args": {"payout_item_id": "PAYOUT-C67"}}], "proposal_json": ""}
            elif number == 2:
                reply["proposal_json"] = json.dumps({"status": "UNCLAIMED"})
            elif echo_canary:
                reply["proposal_json"] = json.dumps({"answer": canary})
            item = {"type": "message", "role": "assistant", "id": f"m{number}",
                    "content": [{"type": "output_text", "text": json.dumps(reply)}]}
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(sse("response.created", response={"id": f"r{number}"}))
            self.wfile.write(sse("response.output_item.done", item=item))
            self.wfile.write(sse("response.completed", response={"id": f"r{number}", "usage": {
                "input_tokens": number * 10, "output_tokens": number, "total_tokens": number * 11}}))

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    real_command = codex_cli.command
    real_popen = subprocess.Popen
    processes = []

    def offline_command(*args):
        command = real_command(*args)
        command[-1:-1] = ["-c", 'model_provider="c67_rehearsal"',
            "-c", 'model_providers.c67_rehearsal.name="offline rehearsal"',
            "-c", f'model_providers.c67_rehearsal.base_url="http://127.0.0.1:{server.server_port}/v1"',
            "-c", 'model_providers.c67_rehearsal.wire_api="responses"',
            "-c", 'model_providers.c67_rehearsal.requires_openai_auth=false',
            "-c", 'model_providers.c67_rehearsal.request_max_retries=0',
            "-c", 'model_providers.c67_rehearsal.stream_max_retries=0']
        return command

    def checked_popen(args, **kwargs):
        if "exec" in args:
            # The preflight wire proof also uses a CLI, with its own local stub.
            provider = [a for a in args if a.startswith("model_provider=")][-1]
            assert kwargs["env"]["CODEX_HOME"] != str(Path.home() / ".codex")
            assert not any("KEY" in key or "PAYPAL" in key for key in kwargs["env"])
            for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
                         "--sandbox", "--json", "--output-schema"):
                assert flag in args
            assert args[args.index("--sandbox") + 1] == "read-only"
            assert not list(Path(kwargs["cwd"]).iterdir())
            if provider == 'model_provider="c67_rehearsal"':
                assert kwargs["env"]["CODEX_HOME"] == str(home)
                processes.append(1)
        return real_popen(args, **kwargs)

    monkeypatch.setattr(codex_cli, "command", offline_command)
    monkeypatch.setattr(subprocess, "Popen", checked_popen)
    try:
        result = stage.main()  # Real stage_one, model/session, client, CLI and event parser.
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    record = json.loads(stage.RECORD.read_text())
    assert len(processes) == observed["requests"] == len(record["turns"]) == 3
    assert observed["native_tools"] == [0, 0, 0] and observed["auth_headers"] == [False, False, False]
    assert observed["tool_result_seen"] is True
    assert record["round_trip"] == "passed"
    assert record["previous_attempts"] == stage.PREVIOUS_RECORDS
    assert not record["week_run"] and not record["pilot_run"] and not record["evaluation_run"]
    assert canary not in stage.RECORD.read_text()
    if echo_canary:
        assert result == 1 and record["status"] == "failed"
        assert record["error"] == "MODEL_CANARY_DISCLOSED" and record["security_stop"] is True
        assert record["canary"]["status"] == "failed"
    else:
        assert result == 0 and record["status"] == "passed" and record["canary"]["status"] == "passed"
        for number, turn in enumerate(record["turns"], 1):
            assert turn["usage"] == {"input_tokens": number * 10, "output_tokens": number,
                                     "total_tokens": number * 11}
