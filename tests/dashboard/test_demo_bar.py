"""M17 demo control bar, static revalidation and staged mock disputes.

Fake runners stand in for the staged week CLI; no subprocess, network or .env.
The real week is exercised end to end in the hosted container check.
"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from countersign.api import dashboard
from countersign.config import MOCK_URL, SANDBOX_URL, PayPalSettings, Settings
from countersign.main import create_app, create_hosted_app

DEMO = Settings("postgresql://unit-user:unit-password@localhost/unit_demo")
LIVE = Settings(
    "postgresql://unit-user:unit-password@localhost/unit_demo",
    mode="live",
    owner_token="unit-owner-token",
    paypal=PayPalSettings(base_url=SANDBOX_URL, client_id="unit-id", client_secret="unit-secret"),
)
APP_URL = "http://127.0.0.1:8090"
NAMES = [name for name, _, _ in dashboard.DEMO_STEPS]


class FakeWeek:
    """Records each command and writes the checkpoint the real CLI would leave."""

    def __init__(self, *, code=0, last=None, gate=None):
        self.calls, self.code, self.last, self.gate = [], code, last, gate

    async def __call__(self, arguments):
        self.calls.append(arguments)
        if self.gate is not None:
            await self.gate.wait()
        if "--pause-after-step" in arguments and self.code == 0:
            root = arguments[arguments.index("--root") + 1]
            date = arguments[arguments.index("--date") + 1]
            directory = dashboard.Path(root) / date
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "state.json"
            state = (
                json.loads(path.read_text("utf-8"))
                if path.exists()
                else {"steps": {}, "data": {"monday": "2026-11-02T09:00:00+00:00"}}
            )
            name = next(n for n in NAMES if state["steps"].get(n) != "SUCCEEDED")
            state["steps"][name] = "SUCCEEDED"
            state["data"]["mock_now"] = "2026-11-04T09:00:00+00:00"
            path.write_text(json.dumps(state), "utf-8")
            return 0, self.last or "DEMO_PAUSED_AFTER_" + name.upper()
        return self.code, self.last


def bar(tmp_path, runner, runtime=DEMO):
    return dashboard.DemoBar(runtime, tmp_path / "week", APP_URL, runner=runner)


def client_for(demo):
    app = FastAPI()
    app.include_router(dashboard.build_demo_router(demo), prefix="/api/v1")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize(
    ("runtime", "allowed"),
    [
        (DEMO, True),
        (LIVE, False),
        (Settings("postgresql://u:p@localhost/countersign"), False),
        (SimpleNamespace(mode="demo", database_url="postgresql://u@h/x_demo",
                         paypal=SimpleNamespace(is_mock=True, base_url=SANDBOX_URL)), False),
        (SimpleNamespace(mode="live", database_url="postgresql://u@h/x_demo",
                         paypal=SimpleNamespace(is_mock=True, base_url=MOCK_URL)), False),
        (SimpleNamespace(mode="demo", database_url="not a dsn", paypal=None), False),
    ],
)
def test_demo_bar_only_in_demo_mode_on_the_loopback_mock(runtime, allowed):
    assert dashboard.demo_bar_allowed(runtime) is allowed


def test_demo_bar_refuses_construction_outside_demo(tmp_path):
    with pytest.raises(ValueError, match="demo mode"):
        bar(tmp_path, FakeWeek(), LIVE)


def test_configuration_needs_both_settings_and_never_enables_live(tmp_path, monkeypatch):
    monkeypatch.delenv("COUNTERSIGN_DEMO_ROOT", raising=False)
    monkeypatch.delenv("COUNTERSIGN_DEMO_APP_URL", raising=False)
    assert dashboard.configured_demo_bar(DEMO) is None
    monkeypatch.setenv("COUNTERSIGN_DEMO_ROOT", str(tmp_path))
    assert dashboard.configured_demo_bar(DEMO) is None
    for url in ["https://example.test", "http://10.0.0.1:8080", "http://127.0.0.1:80/x"]:
        monkeypatch.setenv("COUNTERSIGN_DEMO_APP_URL", url)
        assert dashboard.configured_demo_bar(DEMO) is None
    monkeypatch.setenv("COUNTERSIGN_DEMO_APP_URL", APP_URL)
    assert isinstance(dashboard.configured_demo_bar(DEMO), dashboard.DemoBar)
    assert dashboard.configured_demo_bar(LIVE) is None


def test_first_load_state_is_never_empty_and_labels_staged_steps(tmp_path):
    state = bar(tmp_path, FakeWeek()).state()
    assert state["scenario"] == "demo_week"
    assert (state["step"], state["total"], state["complete"]) == (0, len(NAMES), False)
    assert state["replaying"] is True and state["running"] is False
    assert state["simulation_label"] == "Simulated: mock PayPal"
    assert state["next_label"] == state["steps"][0]["label"]
    assert [s["index"] for s in state["steps"]] == list(range(1, len(NAMES) + 1))
    assert all(s["label"] and s["done"] is False for s in state["steps"])
    staged = {s["name"] for s in state["steps"] if s["staged"]}
    # Buyer disputes, settlement, the lost response and owner clicks are staged.
    assert {"monday_dispute", "wednesday_dispute", "friday_execute", "safety_blocks",
            "following_week_settle"} <= staged
    assert "scripted model" in state["staged_note"]


@pytest.mark.asyncio
async def test_step_runs_one_fixed_mock_command_and_reports_progress(tmp_path):
    week = FakeWeek()
    demo = bar(tmp_path, week)
    async with client_for(demo) as client:
        # A body is ignored: nothing about the command can be chosen by the caller.
        response = await client.post("/api/v1/demo/step", json={"root": "/etc", "target": "sandbox"})
        assert response.status_code == 202
        await demo.wait()
        state = (await client.get("/api/v1/demo/state")).json()
    [arguments] = week.calls
    assert arguments[:6] == ["--target", "mock", "--model", "scripted", "--app-url", APP_URL]
    assert arguments[arguments.index("--root") + 1] == str(tmp_path / "week")
    assert "--pause-after-step" in arguments and "--reset-mock-week" not in arguments
    assert "/etc" not in arguments and "sandbox" not in arguments
    assert state["step"] == 1 and state["steps"][0]["done"]
    assert state["next_label"] == state["steps"][1]["label"]
    assert state["clock"] == "2026-11-04T09:00:00+00:00"
    assert state["last_result"] == {"action": "step", "ok": True, "code": "DEMO_PAUSED_AFTER_SETUP"}


@pytest.mark.asyncio
async def test_resume_uses_the_saved_run_date(tmp_path):
    week = FakeWeek()
    saved = tmp_path / "week" / "2026-10-20"
    saved.mkdir(parents=True)
    (saved / "state.json").write_text(json.dumps({"steps": {"setup": "SUCCEEDED"}, "data": {}}))
    demo = bar(tmp_path, week)
    assert demo.state()["step"] == 1
    assert demo.start("step") is None
    await demo.wait()
    assert week.calls[0][week.calls[0].index("--date") + 1] == "2026-10-20"
    assert demo.state()["step"] == 2


@pytest.mark.asyncio
async def test_one_command_at_a_time_and_complete_week_refuses_more_steps(tmp_path):
    gate = asyncio.Event()
    week = FakeWeek(gate=gate)
    demo = bar(tmp_path, week)
    async with client_for(demo) as client:
        assert (await client.post("/api/v1/demo/step")).status_code == 202
        assert (await client.get("/api/v1/demo/state")).json()["running"] is True
        for path in ["/api/v1/demo/step", "/api/v1/demo/reset"]:
            busy = await client.post(path)
            assert busy.status_code == 409 and busy.json()["error"]["code"] == "DEMO_BUSY"
        gate.set()
        await demo.wait()
        week.gate = None
        for _ in NAMES[1:]:
            assert (await client.post("/api/v1/demo/step")).status_code == 202
            await demo.wait()
        state = (await client.get("/api/v1/demo/state")).json()
        assert state["complete"] is True and state["next_label"] is None
        done = await client.post("/api/v1/demo/step")
        assert done.status_code == 409
        assert done.json()["error"]["code"] == "DEMO_WEEK_COMPLETE"
    assert len(week.calls) == len(NAMES)


@pytest.mark.asyncio
async def test_reset_moves_to_a_new_empty_root_and_keeps_old_evidence(tmp_path):
    week = FakeWeek()
    demo = bar(tmp_path, week)
    assert demo.start("step") is None
    await demo.wait()
    assert demo.start("reset") is None
    await demo.wait()
    reset = week.calls[1]
    assert "--reset-mock-week" in reset and "--pause-after-step" not in reset
    assert reset[reset.index("--root") + 1] == str(tmp_path / "week")
    assert demo.root == tmp_path / "week.reset-1"
    assert demo.state()["step"] == 0
    assert demo.state()["last_result"] == {"action": "reset", "ok": True, "code": "DEMO_RESET_DONE"}
    assert (tmp_path / "week").is_dir()
    # A restarted app continues after the newest reset.
    demo.root.mkdir()
    assert bar(tmp_path, FakeWeek()).root == tmp_path / "week.reset-1"


@pytest.mark.asyncio
async def test_failures_are_fixed_codes_and_do_not_switch_roots(tmp_path):
    demo = bar(tmp_path, FakeWeek(code=1, last="DEMO_CONTROL_FAILED"))
    demo.start("reset")
    await demo.wait()
    assert demo.root == tmp_path / "week"
    assert demo.state()["last_result"] == {
        "action": "reset",
        "ok": False,
        "code": "DEMO_CONTROL_FAILED",
    }

    async def broken(arguments):
        raise RuntimeError("private driver text")

    demo = bar(tmp_path, broken)
    demo.start("step")
    await demo.wait()
    assert demo.state()["last_result"] == {"action": "step", "ok": False, "code": "DEMO_STEP_FAILED"}


@pytest.mark.asyncio
async def test_router_refuses_when_runtime_turns_out_not_to_be_demo(tmp_path):
    demo = bar(tmp_path, FakeWeek())
    demo.runtime = LIVE
    async with client_for(demo) as client:
        for method, path in [("GET", "state"), ("POST", "step"), ("POST", "reset")]:
            response = await client.request(method, "/api/v1/demo/" + path)
            assert response.status_code == 404
    assert demo.task is None


class NoNetwork:
    async def verify(self, raw, headers):
        raise AssertionError("No webhook verification in this test")


def app_kwargs():
    fake = NoNetwork()
    return {"database": SimpleNamespace(), "http": fake, "reader": fake, "verifier": fake}


@pytest.fixture
def demo_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("COUNTERSIGN_DEMO_ROOT", str(tmp_path / "week"))
    monkeypatch.setenv("COUNTERSIGN_DEMO_APP_URL", APP_URL)
    monkeypatch.delenv("COUNTERSIGN_DASHBOARD_SOURCE", raising=False)


@pytest.mark.asyncio
async def test_live_application_never_mounts_demo_endpoints(demo_settings):
    app = create_app(settings=LIVE, start_worker=False, **app_kwargs())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.get("/api/v1/demo/state")).status_code == 404
        for path in ["step", "reset"]:
            assert (await client.post("/api/v1/demo/" + path)).status_code in {404, 405}
        assert not any("/demo/" in getattr(route, "path", "") for route in app.router.routes)


@pytest.mark.asyncio
async def test_hosted_application_serves_demo_bar_and_revalidated_dashboard_code(demo_settings):
    app = create_hosted_app(settings=DEMO, **app_kwargs())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        state = await client.get("/api/v1/demo/state")
        assert state.status_code == 200 and state.json()["step"] == 0
        for path in ["/", "/index.html", "/app.mjs", "/data.mjs", "/demo.mjs", "/styles.css"]:
            response = await client.get(path)
            assert response.status_code == 200, path
            assert response.headers["cache-control"] == "no-cache", path
            etag = response.headers["etag"]
            again = await client.get(path, headers={"If-None-Match": etag})
            assert again.status_code == 304, path
            assert again.headers["cache-control"] == "no-cache", path
            if path.endswith(".mjs"):
                assert response.headers["content-type"].startswith("text/javascript"), path
        vendor = await client.get("/vendor/ag-grid-community.min.js")
        assert vendor.status_code == 200 and "cache-control" not in vendor.headers
    assert not any("/demo/" in getattr(route, "path", "") for route in app.router.routes)


def test_database_source_marks_mock_disputes_staged():
    mock = dashboard.DbSource("postgresql://u@localhost/x_demo", runtime=DEMO)
    live = dashboard.DbSource("postgresql://u@localhost/x_demo", runtime=LIVE)
    doc = {"id": "d", "staged": False}
    assert mock._present("disputes", dict(doc))["staged"] is True
    assert live._present("disputes", dict(doc))["staged"] is False
    assert mock._present("incidents", dict(doc))["staged"] is False
    assert mock._present("disputes", None) is None


def test_week_cli_pauses_once_per_new_step_and_never_on_the_last():
    from scripts.demo_week import FINAL_STEP, DemoPaused, pause_after_new_step

    ran = []

    class Store:
        def __init__(self):
            self.state = {"steps": {"setup": "SUCCEEDED"}}

        async def step(self, name, callback):
            if self.state["steps"].get(name) != "SUCCEEDED":
                await callback()
                self.state["steps"][name] = "SUCCEEDED"

    store = pause_after_new_step(Store())

    async def work():
        ran.append(True)

    async def play():
        await store.step("setup", work)  # already done: resume continues
        with pytest.raises(DemoPaused):
            await store.step("seed", work)
        await store.step("seed", work)  # finished steps never pause again
        store.state["steps"].update({name: "SUCCEEDED" for name in NAMES[:-1]})
        await store.step(FINAL_STEP, work)

    asyncio.run(play())
    assert FINAL_STEP == NAMES[-1]
    assert len(ran) == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["--target", "sandbox", "--reset-mock-week"],
        ["--target", "mock", "--reset-mock-week", "--pause-after-step"],
        ["--target", "mock", "--reset-mock-week", "--serve"],
        ["--target", "sandbox", "--pause-after-step"],
    ],
)
def test_week_cli_refuses_demo_bar_options_outside_the_mock(argv, monkeypatch, capsys):
    from scripts import demo_week

    # Refused before any database or PayPal connection: no settings exist here.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert demo_week.main([*argv, "--env-file", "missing-unit.env"]) == 1
    output = capsys.readouterr().out.strip().splitlines()[-1]
    assert output == ("DEMO_BAR_MOCK_ONLY" if "sandbox" in argv else "DEMO_OPTIONS_CONFLICT")
