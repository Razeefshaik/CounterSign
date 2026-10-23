import asyncio
import json
import threading
import traceback
from datetime import UTC, datetime

import httpx
import pytest

from scripts.sandbox_checks import __main__ as cli
from scripts.sandbox_checks import checks
from scripts.sandbox_checks.checks import CheckUnavailable, get_steps
from scripts.sandbox_checks.client import SandboxClient
from scripts.sandbox_checks.env import SandboxSettings
from scripts.sandbox_checks.manual import ManualSteps
from scripts.sandbox_checks.poller import BalancePoller
from scripts.sandbox_checks.recorder import Recorder
from scripts.sandbox_checks.redact import Redactor
from scripts.sandbox_checks.runner import CheckFailed, Runner, Step, load_check_data

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
CLIENT_ID = "FAKE-CLIENT-ID"
SECRET = "FAKE-CLIENT-SECRET"
TOKEN = "FAKE-ACCESS-TOKEN"
EMAIL = "maker-a@fake.example"
REQUIRED_SCOPES = (
    "https://uri.paypal.com/payments/payouts",
    "https://uri.paypal.com/services/disputes/read-seller",
    "https://uri.paypal.com/services/disputes/update-seller",
    "https://uri.paypal.com/services/reporting/search/read",
)


@pytest.fixture
def settings():
    return SandboxSettings(client_id=CLIENT_ID, client_secret=SECRET, maker_a_email=EMAIL)


def transport(scopes=REQUIRED_SCOPES, calls=None):
    def handle(request):
        if calls is not None:
            calls.append(request.url.path)
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(
                200, json={"access_token": TOKEN, "expires_in": 3600, "scope": " ".join(scopes)}
            )
        assert request.url.path == "/v1/reporting/balances"
        return httpx.Response(
            200,
            json={
                "balances": [
                    {
                        "currency": "USD",
                        "available_balance": {"value": "5000.00", "currency_code": "USD"},
                    }
                ],
                "last_refresh_time": "2026-10-23T09:00:00Z",
                "echo": f"{SECRET} {TOKEN} {EMAIL}",
            },
        )

    return httpx.MockTransport(handle)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payout_scope",
    ("https://uri.paypal.com/payments/payouts", "https://uri.paypal.com/services/payments/payouts"),
)
async def test_d1_records_scopes_finding_and_balance_without_secret_leakage(
    tmp_path, settings, payout_scope
):
    output = []
    scopes = (payout_scope, *REQUIRED_SCOPES[1:])
    state = await Runner(
        settings,
        root=tmp_path,
        transport=transport(scopes),
        clock=lambda: NOW,
        output=output.append,
    ).run("D1")

    assert state["completed_steps"] == ["authenticate", "inspect_scopes"]
    assert state["data"]["verdict"] == "CONFIRMED"
    assert state["data"]["permissions"]["disputes_create"] is False
    finding = json.loads((tmp_path / "2026-10-23/D1/findings.json").read_text())
    assert finding["scope"] == " ".join(scopes)
    assert finding["evidence_files"]
    balances = [json.loads(line) for line in (tmp_path / "balances.jsonl").read_text().splitlines()]
    assert balances[0]["response_body"]["last_refresh_time"] == "2026-10-23T09:00:00Z"
    public = "".join(path.read_text() for path in tmp_path.rglob("*") if path.is_file())
    for private in (CLIENT_ID, SECRET, TOKEN, EMAIL):
        assert private not in public + "\n".join(output)
    assert any("disputes_create: MISSING (optional" in line for line in output)


@pytest.mark.asyncio
async def test_missing_required_scope_is_blocked_but_missing_create_scope_is_not(
    tmp_path, settings
):
    state = await Runner(
        settings,
        root=tmp_path,
        transport=transport(REQUIRED_SCOPES[:-1]),
        clock=lambda: NOW,
        output=lambda _: None,
    ).run("D1")
    assert state["data"]["verdict"] == "BLOCKED"
    assert state["data"]["missing_required"] == ["reporting"]


@pytest.mark.asyncio
async def test_resume_skips_completed_steps_and_preserves_pre_call_checkpoint(tmp_path, settings):
    executions = []
    fail = [True]

    async def first(ctx):
        executions.append("first")
        return {"first_result": "saved"}

    async def second(ctx):
        executions.append("second")
        assert ctx.data["first_result"] == "saved"
        if "request_id" not in ctx.data:
            ctx.checkpoint(request_id="cs-fake-stable-a1")
        if fail[0]:
            raise RuntimeError(f"{SECRET} {TOKEN} {EMAIL}")
        assert ctx.data["request_id"] == "cs-fake-stable-a1"

    async def third(ctx):
        executions.append("third")

    steps = [Step("first", first), Step("second", second), Step("third", third)]
    options = {"root": tmp_path, "clock": lambda: NOW, "output": lambda _: None}
    with pytest.raises(CheckFailed) as error:
        await Runner(settings, transport=transport(), **options).run("D1", steps=steps)
    formatted = "".join(traceback.format_exception(error.value))
    assert SECRET not in formatted and TOKEN not in formatted
    saved = json.loads((tmp_path / "2026-10-23/D1/state.json").read_text())
    assert saved["completed_steps"] == ["first"]
    assert saved["in_progress"] == "second"
    assert saved["data"]["request_id"] == "cs-fake-stable-a1"
    fail[0] = False

    completed = await Runner(settings, transport=transport(), **options).run("D1", steps=steps)

    assert executions == ["first", "second", "second", "third"]
    assert completed["completed_steps"] == ["first", "second", "third"]
    calls = []
    await Runner(settings, transport=transport(calls=calls), **options).run("D1", steps=steps)
    assert calls == []


@pytest.mark.asyncio
async def test_fresh_keeps_evidence_but_repeats_steps(tmp_path, settings):
    options = {"root": tmp_path, "clock": lambda: NOW, "output": lambda _: None}
    await Runner(settings, transport=transport(), **options).run("D1")
    first_files = set((tmp_path / "2026-10-23/D1").glob("*_oauth_token.json"))
    await Runner(settings, transport=transport(), **options).run("D1", fresh=True)
    second_files = set((tmp_path / "2026-10-23/D1").glob("*_oauth_token.json"))
    assert first_files < second_files


@pytest.mark.asyncio
async def test_changed_step_definition_refuses_silent_replay(tmp_path, settings):
    async def nothing(ctx):
        return None

    options = {"root": tmp_path, "clock": lambda: NOW, "output": lambda _: None}
    await Runner(settings, transport=transport(), **options).run(
        "D1", steps=[Step("first", nothing)]
    )
    calls = []
    with pytest.raises(CheckFailed, match="step definition"):
        await Runner(settings, transport=transport(calls=calls), **options).run(
            "D1", steps=[Step("different", nothing)]
        )
    assert calls == []


def test_future_registry_checks_fail_clearly_and_prior_data_is_available(tmp_path, monkeypatch):
    monkeypatch.setitem(checks.CHECK_MODULES, "D8", "_missing_check_for_registry_test")
    with pytest.raises(CheckUnavailable, match="D8 is not implemented"):
        get_steps("D8")
    assert load_check_data(tmp_path, "D1") == {}
    state = tmp_path / "2026-10-23/D1/state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"data": {"scope": "fake", "verdict": "CONFIRMED"}}))
    assert load_check_data(tmp_path, "D1")["scope"] == "fake"


@pytest.mark.asyncio
async def test_other_checks_require_confirmed_d1_before_network_calls(tmp_path, settings):
    async def nothing(ctx):
        return None

    calls = []
    with pytest.raises(CheckFailed, match="D1 must be CONFIRMED"):
        await Runner(settings, root=tmp_path, transport=transport(calls=calls)).run(
            "D8", steps=[Step("first", nothing)]
        )
    assert calls == []


@pytest.mark.asyncio
async def test_manual_id_rejects_emails_and_known_secrets_without_echoing_them(settings):
    output = []
    entered = iter([EMAIL, SECRET, "PP-D-FAKE-123"])
    manual = ManualSteps(
        Redactor.from_settings(settings), input_fn=lambda _: next(entered), output_fn=output.append
    )
    result = await manual.ask_id(f"Open the case for {EMAIL} and enter its ID.")
    assert result == "PP-D-FAKE-123"
    assert EMAIL not in "\n".join(output)
    assert SECRET not in "\n".join(output)
    assert "maker-a@redacted" in output[0]


@pytest.mark.asyncio
async def test_balance_poller_continues_during_manual_browser_wait(tmp_path, settings):
    stop = asyncio.Event()
    release_input = threading.Event()
    count = [0]

    def handle(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3600})
        count[0] += 1
        if count[0] >= 2:
            release_input.set()
        return httpx.Response(200, json={"balances": []})

    def browser_input(_):
        assert release_input.wait(timeout=5), "polling stalled during manual input"
        return ""

    redactor = Redactor.from_settings(settings)
    manual = ManualSteps(redactor, input_fn=browser_input, output_fn=lambda _: None)
    recorder = Recorder("D1", root=tmp_path, redactor=redactor, clock=lambda: NOW)
    async with SandboxClient(
        settings, recorder, transport=httpx.MockTransport(handle), clock=lambda: NOW
    ) as client:
        poller = BalancePoller(client, interval=0.01, clock=lambda: NOW)
        task = asyncio.create_task(poller.run(stop))
        try:
            await manual.confirm("Approve the staged action in the browser.")
        finally:
            release_input.set()
            stop.set()
            await task
    assert count[0] >= 2
    assert len((tmp_path / "balances.jsonl").read_text().splitlines()) >= 2


@pytest.mark.asyncio
async def test_balance_failure_is_recorded_without_copying_private_exception(tmp_path, settings):
    def handle(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3600})
        raise httpx.ReadTimeout(f"{TOKEN} {SECRET}", request=request)

    recorder = Recorder(
        "D1", root=tmp_path, redactor=Redactor.from_settings(settings), clock=lambda: NOW
    )
    output = []
    async with SandboxClient(
        settings, recorder, transport=httpx.MockTransport(handle), clock=lambda: NOW
    ) as client:
        observed = await BalancePoller(client, clock=lambda: NOW, output=output.append).poll_once()
    assert observed["error"] == "SandboxRequestError"
    public = (tmp_path / "balances.jsonl").read_text() + "\n".join(output)
    assert TOKEN not in public and SECRET not in public


@pytest.mark.parametrize("scopes,expected_code", ((REQUIRED_SCOPES, 0), (REQUIRED_SCOPES[:-1], 1)))
def test_cli_uses_stubbed_runtime_configuration(
    tmp_path, settings, monkeypatch, scopes, expected_code
):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(
        cli,
        "Runner",
        lambda config, root: Runner(
            config, root=root, transport=transport(scopes), clock=lambda: NOW
        ),
    )
    assert cli.main(["run", "D1", "--recordings-root", str(tmp_path)]) == expected_code
