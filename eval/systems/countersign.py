"""M16: adapt the public Engine facade to the M11 System protocol.

Supply a migrated, exclusively owned PostgreSQL database ending in _test/_eval
to CountersignSystem(database_url=...). Nothing reads .env or DATABASE_URL.
The runner owns mock reset/seeding/clock advancement; Engine owns every financial
decision, approval, write, reconciliation and application-table reset. This module
imports only countersign.harness from core and never queries application tables.

No model means the commander's disabled Engine paths, labelled model='none' by
the caller, not a scripted substitute for real reasoning. An optional env.llm
must implement complete() and expose per-call last_usage_metadata with integer
input_tokens/output_tokens. Configuration objects alone cannot supply honest
usage measurements and are rejected before Engine construction. Real evaluations
must pass the same explicitly selected model to both model-based systems.

The genuine toolkit subprocess also needs a TCP-reachable mock at mock_base_url.
M11's current in-process runner suffices for the model-disabled F01 acceptance;
configured-model benchmarking needs a TCP-backed runner follow-up outside this
card. Fake-model metering tests are not a real Gemini evaluation.
"""

import asyncio
import hashlib
import json
from copy import deepcopy
from datetime import date, datetime
from urllib.parse import urlsplit

import httpx

from countersign.harness import Engine, EngineError, EngineSettings
from eval.systems.base import ActionRecord, OwnerRequest, SystemTrace, require_local
from eval.systems.baseline_llm import _quota_exhausted


class _MockTransport(httpx.AsyncBaseTransport):
    """Forward Engine HTTP through the runner's public client; never own/close it."""

    def __init__(self, client, base):
        self.client, self.base = client, str(httpx.URL(base)).rstrip("/")

    async def handle_async_request(self, request):
        origin = urlsplit(self.base)
        target = urlsplit(str(request.url))
        if (
            str(self.client.base_url).rstrip("/") != self.base
            or (target.scheme, target.hostname, target.port or 80)
            != (origin.scheme, origin.hostname, origin.port or 80)
            or target.username
            or target.password
            or target.fragment
            or not target.path.startswith(("/v1/", "/v2/"))
        ):
            raise ValueError("Engine transport requires the configured local mock")
        return await self.client.send(request, follow_redirects=False)


class _MeasuredModel:
    """Instrument the borrowed public method without changing the model's type.

    Engine uses the concrete model type for provider startup and step budgets.
    A proxy would change those decisions. Restore the method on every exit path.
    """

    def __init__(self, model, trace):
        self._model, self._trace = model, trace
        self._original = model.complete
        if isinstance(getattr(self._original, "__self__", None), _MeasuredModel):
            raise TypeError("Model is already borrowed by an evaluation")
        self._instance_method = "complete" in getattr(model, "__dict__", {})
        self._installed = False

    def install(self):
        try:
            self._model.complete = self.complete
        except (AttributeError, TypeError):
            raise ValueError("Model does not support evaluation metering") from None
        self._installed = True

    def restore(self):
        if self._installed:
            if self._instance_method:
                self._model.complete = self._original
            else:
                del self._model.complete
            self._installed = False

    def __repr__(self):
        return "MeasuredModel(values=<REDACTED>)"

    async def complete(self, request):
        self._trace.llm_calls += 1
        try:
            response = await self._original(request)
        except Exception as error:
            if _quota_exhausted(error):
                self._trace.errors.append("MODEL_QUOTA_EXHAUSTED")
            raise
        usage = getattr(self._model, "last_usage_metadata", None)
        if not isinstance(usage, dict) or any(
            type(usage.get(key)) is not int or usage[key] < 0
            for key in ("input_tokens", "output_tokens")
        ):
            self._trace.errors.append("MODEL_USAGE_UNAVAILABLE")
            raise ValueError("MODEL_USAGE_UNAVAILABLE")
        self._trace.tokens_in += usage["input_tokens"]
        self._trace.tokens_out += usage["output_tokens"]
        return response


class CountersignSystem:
    name = "countersign"

    def __init__(self, *, database_url, engine_factory=Engine):
        self._database_url, self._factory = database_url, engine_factory
        self._engine = None
        self._meter = None
        self.trace = SystemTrace([], 0, [], 0, 0, 0)

    def __repr__(self):
        return "CountersignSystem(values=<REDACTED>)"

    async def setup(self, env):
        require_local(env.mock_base_url)
        if env.scenario["ground"] != "mock" or urlsplit(env.mock_base_url).path not in {"", "/"}:
            raise ValueError("CounterSign evaluation requires a local mock")
        if self._engine is not None:
            raise RuntimeError("Engine is already open")
        self._env = env
        self.trace = SystemTrace([], 0, [], 0, 0, 0)
        self._seen, self._requests, self._decided = set(), {}, set()
        self._reserves = {}
        self._scheduled = set()
        self._tick_error = False
        model = env.llm
        if model is not None and not callable(getattr(model, "complete", None)):
            raise ValueError("An explicit measurable ChatModel is required")
        transport = _MockTransport(env.mock.client, env.mock_base_url)
        # Registration is runner-owned setup, not a financial operation. Its ID is
        # not in EvalEnv; discover exactly the runner's callback via the public API.
        response = await transport.handle_async_request(
            httpx.Request(
                "GET",
                env.mock_base_url.rstrip("/") + "/v1/notifications/webhooks",
                headers=dict(env.mock.client.headers),
            )
        )
        if response.status_code != 200:
            raise RuntimeError("Mock webhook registration unavailable")
        registrations = [
            r for r in response.json().get("webhooks", []) if r.get("url") == env.webhook_url
        ]
        if len(registrations) != 1:
            raise ValueError("Exactly one runner webhook registration is required")
        try:
            if model is not None:
                self._meter = _MeasuredModel(model, self.trace)
                self._meter.install()
            settings = EngineSettings(
                database_url=self._database_url,
                paypal_base_url=env.mock_base_url,
                paypal_client_id=env.paypal_client_id,
                paypal_client_secret=env.paypal_client_secret,
                webhook_id=registrations[0]["id"],
                clock=lambda: datetime.fromisoformat(env.mock.now),
                llm=model,
            )
            self._engine = self._factory(settings, transport=transport)
            await self._engine.reset()
            state = deepcopy(env.scenario["initial_state"])
            state.pop("paypal", None)
            await self._engine.seed(state)
            await self._observe()
        except BaseException as exc:
            try:
                if self._engine is not None:
                    await self._engine.aclose()
            finally:
                self._engine = None
                if self._meter is not None:
                    self._meter.restore()
                    self._meter = None
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            raise RuntimeError("Engine evaluation setup failed") from None

    def _record(self, action, params, outcome="ok", approved=False):
        self.trace.actions.append(
            ActionRecord(self._env.mock.now, action, deepcopy(params), outcome, approved)
        )

    async def _observe(self):
        snapshot = await self._engine.snapshot()
        outcomes = {
            "EXECUTED": "ok",
            "BLOCKED": "blocked",
            "BLOCKED_AT_EXECUTION": "blocked",
            "REJECTED": "blocked",
            "EXPIRED": "blocked",
            "FAILED": "failed",
        }
        for proposal in snapshot["proposals"]:
            # A consumed run approval proves scheduling started even when a lost
            # create response leaves the proposal EXECUTING. It does not prove
            # payment: completion still comes exclusively from mock ground truth.
            scheduling_started = (
                proposal["action"] == "SCHEDULE_PAYOUT_RUN"
                and proposal["state"] == "EXECUTING"
                and any(
                    run["id"] == proposal["params"].get("run_id")
                    and (run.get("approval") or {}).get("used_at") is not None
                    for run in snapshot.get("runs", [])
                )
            )
            identity = (proposal["id"], proposal["state"])
            if (
                proposal["state"] not in outcomes and not scheduling_started
            ) or identity in self._seen:
                continue
            self._seen.add(identity)
            outcome = "ok" if scheduling_started else outcomes[proposal["state"]]
            if proposal["action"] == "SCHEDULE_PAYOUT_RUN" and outcome == "ok":
                if proposal["id"] in self._scheduled:
                    continue
                self._scheduled.add(proposal["id"])
            self._record(
                proposal["action"],
                proposal["params"],
                outcome,
                proposal["id"] in self._decided,
            )
        # The Engine reserves disputed shares itself, without a proposal. Record
        # only what its reservations show, as rules_only records its own reserves.
        disputes = {d["id"]: d.get("paypal_dispute_id") for d in snapshot.get("disputes", [])}
        for reservation in snapshot.get("reservations", []):
            if reservation["kind"] != "DISPUTE_EARMARK":
                continue
            recorded = self._reserves.setdefault(reservation["id"], set())
            params = {
                "paypal_dispute_id": disputes.get(reservation["dispute_id"]),
                "amount": reservation["amount"],
            }
            if "RESERVE_EARNINGS" not in recorded:
                recorded.add("RESERVE_EARNINGS")
                self._record("RESERVE_EARNINGS", params)
            if reservation["state"] == "RELEASED" and "RELEASE_RESERVE" not in recorded:
                recorded.add("RELEASE_RESERVE")
                self._record("RELEASE_RESERVE", params)

    async def on_step(self, step, env):
        if step["do"] == "plan_payout_run":
            # Synchronize GET-confirmed states/cash before the Engine planner runs.
            await self._tick()
            try:
                await self._engine.plan_payout_run(date.fromisoformat(step["week_start"]))
            except EngineError as error:
                # These planner refusals roll back before any operation is written.
                # Unknown errors (including storage failures) must still fail the run.
                if error.args not in (
                    ("NO_PAYABLE_OBLIGATIONS",),
                    ("RUN_ALREADY_EXISTS",),
                    ("SHIELD_BLOCKED",),
                ):
                    raise
                self._record(
                    "SCHEDULE_PAYOUT_RUN",
                    {"week_start": step["week_start"], "reason": error.args[0]},
                    "blocked",
                )
        elif step["do"] == "tick":
            await self._tick()
        else:
            raise ValueError("Unsupported Engine step")
        await self._observe()

    async def _tick(self):
        result = await self._engine.tick()
        if "MODEL_QUOTA_EXHAUSTED" in self.trace.errors:
            raise RuntimeError("MODEL_QUOTA_EXHAUSTED")
        self._tick_error = bool(result["errors"])
        if result["errors"]:
            # Core reports transient/unknown operations here too. Keep them visible;
            # do not turn a later successful recovery into an invented hard failure.
            self._record("ENGINE_TICK", {"errors": result["errors"]}, "unknown")

    async def on_webhook(self, headers, raw_body):
        if self._engine is None:
            raise RuntimeError("Engine is not open")
        status = await self._engine.deliver_webhook(deepcopy(headers), bytes(raw_body))
        if status not in {200, 400}:
            raise RuntimeError("Engine webhook ingestion failed")

    async def pending_owner_requests(self):
        result = []
        for proposal in await self._engine.pending_approvals():
            binding = proposal["approval_request"]
            digest = hashlib.sha256(
                json.dumps(
                    [proposal["id"], binding, proposal["params"]],
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            identifier = proposal["id"] + ":" + digest
            change = proposal.get("expected_effect", {}).get("available_change")
            total = None
            if change is not None:
                if type(change.get("minor")) is not int or change.get("currency") != "USD":
                    raise ValueError("Invalid Engine monetary effect")
                total = {"minor": abs(change["minor"]), "currency": "USD"}
            request = OwnerRequest(
                identifier,
                proposal["action"],
                deepcopy(proposal["params"]),
                total,
                proposal.get("rationale", "Engine owner decision"),
            )
            if identifier not in self._requests:
                self._requests[identifier] = (
                    deepcopy(request),
                    proposal["id"],
                    binding["payload_hash"],
                )
                self.trace.owner_requests += 1
            result.append(request)
        return deepcopy(result)

    async def owner_decide(self, request_id, approve):
        request, proposal_id, payload_hash = self._requests[request_id]
        if request_id in self._decided:
            raise ValueError("Owner request already decided")
        self._decided.add(request_id)
        try:
            if approve:
                await self._engine.approve(proposal_id, payload_hash, approved_by="eval-owner")
                self._decided.add(proposal_id)
            else:
                await self._engine.reject(proposal_id, "Evaluation owner rejected")
        except EngineError as exc:
            if str(exc) not in {"STALE_PROPOSAL", "APPROVAL_EXPIRED", "PROPOSAL_NOT_PENDING"}:
                raise RuntimeError("Engine owner decision failed") from None
            self._record(request.action, request.params, "blocked", approve)
        await self._observe()

    async def teardown(self):
        try:
            if self._engine is not None:
                try:
                    await self._observe()
                finally:
                    try:
                        await self._engine.aclose()
                    finally:
                        self._engine = None
        finally:
            if self._meter is not None:
                self._meter.restore()
                self._meter = None
        if self._tick_error:
            self.trace.errors.append("ENGINE_TICK_ERRORS_UNRESOLVED")
        return deepcopy(self.trace)
