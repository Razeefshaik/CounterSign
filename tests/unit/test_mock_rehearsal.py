"""Rehearsal preparation uses stubbed local HTTP and fake processes only."""

import json
import os
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from scripts import mock_rehearsal as rehearsal

WEBHOOK = "WH-MOCK-000000024DADCE834960"
CALLBACK = "http://127.0.0.1:8000/webhooks/paypal"
DATABASE = "postgresql://countersign:FAKE-PRIVATE-PASSWORD@127.0.0.1:55433/week_demo"


class Process:
    pid = 12345

    def __init__(self):
        self.stopped = False

    def poll(self):
        return 0 if self.stopped else None

    def terminate(self):
        self.stopped = True

    def wait(self, timeout):
        return 0


def local_http(*, missing=False, registered=False, fail_registration=False):
    requests = []
    first = True

    def handler(request):
        nonlocal first
        requests.append(request)
        assert request.url.host == "127.0.0.1" and request.url.port == 8099
        path = request.url.path
        if path == "/_mock/clock":
            if first and missing:
                first = False
                raise httpx.ConnectError("local service absent", request=request)
            return httpx.Response(200, json={"now": "2026-11-02T09:00:00Z"})
        if path == "/_mock/reset":
            assert json.loads(request.content) == {"seed": 17}
            return httpx.Response(200, json={"seed": 17, "now": "2026-11-02T09:00:00Z"})
        if path == "/v1/oauth2/token":
            assert request.headers["authorization"].startswith("Basic ")
            assert request.content == b"grant_type=client_credentials"
            return httpx.Response(200, json={"access_token": "FAKE-PRIVATE-OAUTH-TOKEN"})
        assert request.headers["authorization"] == "Bearer FAKE-PRIVATE-OAUTH-TOKEN"
        if path == "/v1/notifications/webhooks":
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "webhooks": [
                            {"id": WEBHOOK, "url": CALLBACK, "event_types": [{"name": "*"}]},
                        ]
                        if registered
                        else []
                    },
                )
            assert json.loads(request.content) == {"url": CALLBACK, "event_types": [{"name": "*"}]}
            if fail_registration:
                return httpx.Response(500, json={"message": "FAKE-PRIVATE-OAUTH-TOKEN"})
            return httpx.Response(201, json={"id": WEBHOOK, "url": CALLBACK})
        pytest.fail("unexpected local request: " + path)

    client = httpx.Client(base_url=rehearsal.MOCK_URL, transport=httpx.MockTransport(handler))
    return client, requests


def test_new_background_mock_uses_only_minimal_environment_and_registers_webhook(monkeypatch):
    process, calls = Process(), []
    monkeypatch.setenv("GOOGLE_API_KEY", "FAKE-GOOGLE-KEY")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "FAKE-PAYPAL-SECRET")
    monkeypatch.setenv("OWNER_TOKEN", "FAKE-OWNER-TOKEN")

    def spawn(*argv, **kwargs):
        calls.append((argv, kwargs))
        return process

    monkeypatch.setattr(rehearsal.subprocess, "Popen", spawn)
    client, requests = local_http(missing=True)
    with client:
        result = rehearsal.prepare(client, pause=lambda _: None)
    assert result == {"webhook_id": WEBHOOK, "callback": CALLBACK, "pid": 12345}
    [launch] = calls
    assert launch[0][0][1:] == ["-m", "mock_paypal"]
    options = launch[1]
    assert options["cwd"] == rehearsal.ROOT
    assert options["env"]["MOCK_CONTROL"] == "1"
    assert not {"GOOGLE_API_KEY", "PAYPAL_CLIENT_SECRET", "OWNER_TOKEN"} & options["env"].keys()
    assert options["stdin"] == options["stdout"] == options["stderr"] == subprocess.DEVNULL
    assert options["creationflags"] == (subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert any(r.url.path == "/_mock/reset" for r in requests)
    assert not process.stopped


def test_existing_mock_and_matching_webhook_are_reused_without_reset_or_duplicate():
    client, requests = local_http(registered=True)
    with client:
        result = rehearsal.prepare(
            client, start_mock=lambda: pytest.fail("do not start another mock")
        )
    assert result["webhook_id"] == WEBHOOK and result["pid"] is None
    assert [(r.method, r.url.path) for r in requests] == [
        ("GET", "/_mock/clock"),
        ("POST", "/v1/oauth2/token"),
        ("GET", "/v1/notifications/webhooks"),
    ]


def test_resetting_an_existing_mock_requires_the_explicit_flag():
    client, requests = local_http()
    with client:
        rehearsal.prepare(client, reset=True)
    assert [(r.method, r.url.path) for r in requests][1] == ("POST", "/_mock/reset")


@pytest.mark.parametrize("started", [False, True])
def test_failed_setup_stops_only_the_mock_this_helper_started(started):
    process = Process()
    client, _ = local_http(missing=started, fail_registration=True)
    with client, pytest.raises(rehearsal.RehearsalError, match="REHEARSAL_MOCK_REQUEST_FAILED"):
        rehearsal.prepare(client, start_mock=lambda: process, pause=lambda _: None)
    assert process.stopped is started


@pytest.mark.parametrize(
    "database",
    [
        "",
        "postgresql://localhost:55433/countersign",
        "postgresql://localhost:55433/countersign?application_name=week_demo",
        "postgresql://localhost:55432/week_demo",
        "postgresql://example.test:55433/week_demo",
        "sqlite:///week_demo",
        "postgresql://localhost:55433/week_demo/working",
    ],
)
def test_database_guard_refuses_working_remote_or_deceptive_targets_before_http(
    monkeypatch, database
):
    monkeypatch.setenv("DATABASE_URL", database)
    monkeypatch.setattr(
        rehearsal.httpx, "Client", lambda **_: pytest.fail("invalid DB cannot open HTTP")
    )
    assert rehearsal.main(["--root", "scratch"]) == 1


def test_cli_prints_exact_commands_without_reading_file_or_leaking_private_values(
    monkeypatch, capsys
):
    monkeypatch.setenv("DATABASE_URL", DATABASE)
    monkeypatch.setenv("GOOGLE_API_KEY", "FAKE-PRIVATE-MODEL-KEY")
    client, requests = local_http(registered=True)
    options = []

    def client_factory(**kwargs):
        options.append(kwargs)
        return client

    monkeypatch.setattr(rehearsal.httpx, "Client", client_factory)
    assert rehearsal.main(["--root", "scratch dir", "--env-file", "missing-owner-file"]) == 0
    output = capsys.readouterr().out
    assert "FAKE-PRIVATE" not in output
    assert DATABASE not in output and "FAKE-PRIVATE-OAUTH-TOKEN" not in output
    assert WEBHOOK in output
    assert "--model configured --env-file 'missing-owner-file' --serve --port 8000" in output
    assert "--root 'scratch dir' --app-url http://127.0.0.1:8000" in output
    assert "COUNTERSIGN_TOOLKIT_PYTHON" in output
    assert options == [
        {
            "base_url": rehearsal.MOCK_URL,
            "timeout": 5,
            "trust_env": False,
            "follow_redirects": False,
        }
    ]
    assert len(requests) == 3


def test_printed_commands_quote_shell_metacharacters_as_literals(capsys):
    rehearsal.print_commands(
        {"webhook_id": WEBHOOK, "pid": None},
        env_file="owner's `$file",
        root="scratch's $(ignored)",
    )
    text = capsys.readouterr().out
    assert "'owner''s `$file'" in text and "'scratch''s $(ignored)'" in text


@pytest.mark.parametrize("root", ["scratch\nCOMMAND", "scratch\rCOMMAND", ""])
def test_cli_refuses_multiline_or_empty_printed_paths(monkeypatch, root):
    monkeypatch.setenv("DATABASE_URL", DATABASE)
    monkeypatch.setattr(
        rehearsal.httpx, "Client", lambda **_: pytest.fail("invalid path cannot open HTTP")
    )
    assert rehearsal.main(["--root", root]) == 1


def test_control_api_must_be_enabled_in_an_existing_server():
    client = httpx.Client(
        base_url=rehearsal.MOCK_URL,
        transport=httpx.MockTransport(lambda _: httpx.Response(404, json={"name": "NOT_FOUND"})),
    )
    with client, pytest.raises(rehearsal.RehearsalError):
        rehearsal.prepare(
            client, start_mock=lambda: pytest.fail("do not replace an existing server")
        )


def test_invalid_receiver_port_never_contacts_mock():
    with pytest.raises(rehearsal.RehearsalError, match="REHEARSAL_RECEIVER_PORT_REFUSED"):
        rehearsal.prepare(SimpleNamespace(get=lambda *_: pytest.fail("no HTTP")), app_port=8099)
