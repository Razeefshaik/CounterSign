"""C26: owner-operated sandbox checks and a mock-only rehearsal.

No import-time environment files, model providers, connections or API calls.
Real runs require owner-created captures; this script never creates/captures
orders or bypasses the shield for a financial write.
"""

import argparse
import asyncio
import copy
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psycopg
from psycopg.conninfo import conninfo_to_dict

from countersign.config import PayPalSettings, load_settings
from countersign.db import Database
from countersign.harness import Engine, EngineSettings
from countersign.paypal.http import PayPalHTTP
from eval.runner import run_sandbox_integration
from eval.sandbox import catalog_fingerprint, write_integration_results
from scripts.demo_week import DemoModel, PayoutTrace, read_registry
from scripts.sandbox_checks.manual import ManualSteps
from scripts.sandbox_checks.redact import Redactor
from scripts.sandbox_checks.relay import Relay
from scripts.slice_e2e import Checkpoint, LostResponse, SliceError, check_webhook, require

MOCK_START = datetime(2026, 11, 2, 9, tzinfo=UTC)
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def database_guard(value):
    try:
        fields = conninfo_to_dict(value)
        name = fields["dbname"]
        allowed = name.endswith(("_eval", "_test")) and not fields.get("options")
    except (ValueError, TypeError, KeyError, psycopg.Error):
        allowed = False
    require(allowed, "C26_DEDICATED_DATABASE_REQUIRED")
    return name


def execution_fingerprint():
    """Bind resume to the reviewed cases and integration implementation."""
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256(catalog_fingerprint().encode())
    for relative in ("scripts/sandbox_eval.py", "eval/sandbox.py", "eval/runner.py"):
        digest.update(relative.encode())
        digest.update((root / relative).read_bytes())
    return digest.hexdigest()


def target_guard(target, dry_run, settings=None):
    require(
        (target == "mock" and dry_run) or (target == "sandbox" and not dry_run),
        "C26_EXPLICIT_TARGET_REQUIRED",
    )
    if target == "sandbox":
        require(
            settings is not None
            and settings.mode == "live"
            and settings.paypal.base_url == "https://api-m.sandbox.paypal.com"
            and not settings.paypal.is_mock,
            "C26_SANDBOX_LIVE_SETTINGS_REQUIRED",
        )
        database_guard(settings.database_url)


def resource_manifest(value):
    """An owner input file, not credentials; permit only fixed small USD cases."""
    require(
        type(value) is dict and set(value) <= {"emails", "captures", "dispute_id"},
        "C26_RESOURCE_MANIFEST_INVALID",
    )
    require(
        set(value.get("emails", {})) == set("RULD")
        and set(value.get("captures", {})) == set("RULD"),
        "C26_RESOURCE_MANIFEST_INVALID",
    )
    require(
        all(
            isinstance(email, str) and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
            for email in value["emails"].values()
        ),
        "C26_RESOURCE_MANIFEST_INVALID",
    )
    require(len(set(value["emails"].values())) == 4, "C26_DISTINCT_RECIPIENTS_REQUIRED")
    require(
        len({entry.get("capture_id") for entry in value["captures"].values()}) == 4,
        "C26_DISTINCT_CAPTURES_REQUIRED",
    )
    for entry in value["captures"].values():
        require(
            type(entry) is dict
            and set(entry) == {"capture_id", "order_id", "captured_at"}
            and all(
                isinstance(entry[k], str) and _ID.fullmatch(entry[k])
                for k in ("capture_id", "order_id")
            ),
            "C26_RESOURCE_MANIFEST_INVALID",
        )
        try:
            parsed = datetime.fromisoformat(entry["captured_at"])
            require(parsed.utcoffset() is not None, "C26_CAPTURE_TIME_REQUIRED")
        except (ValueError, TypeError):
            raise SliceError("C26_CAPTURE_TIME_REQUIRED") from None
    if value.get("dispute_id"):
        require(
            isinstance(value["dispute_id"], str) and _ID.fullmatch(value["dispute_id"]),
            "C26_RESOURCE_MANIFEST_INVALID",
        )
    return copy.deepcopy(value)


def mock_resources():
    return resource_manifest(
        {
            "emails": {k: "c26-" + k.lower() + "@example.test" for k in "RULD"},
            "captures": {
                k: {
                    "capture_id": "C26CAP-" + k,
                    "order_id": "C26ORDER-" + k,
                    "captured_at": MOCK_START.isoformat(),
                }
                for k in "RULD"
            },
        }
    )


def engine_seed(resources):
    return {
        "policy": {
            "maker_share_pct": 90,
            "refund_limit_per_order": {"minor": 200, "currency": "USD"},
            "refund_limit_per_day": {"minor": 200, "currency": "USD"},
        },
        "makers": [
            {
                "key": k,
                "display_name": "C26 " + k,
                "payout_email": resources["emails"][k],
                "verified": True,
            }
            for k in "RULD"
        ],
        "orders": [
            {
                "key": "order-" + k,
                "maker": k,
                "amount": {"minor": 200, "currency": "USD"},
                "capture_id": entry["capture_id"],
                "paypal_order_id": entry["order_id"],
                "captured_at": entry["captured_at"],
                "tracking": None,
            }
            for k, entry in resources["captures"].items()
        ],
        "prior_payouts": [],
        "disputes": [{"paypal_dispute_id": resources["dispute_id"], "order": "order-D"}],
    }


async def confirm_resources(http, resources):
    for key, entry in resources["captures"].items():
        capture = await http.request("GET", "/v2/payments/captures/" + entry["capture_id"])
        require(
            capture.status_code == 200
            and capture.body.get("id") == entry["capture_id"]
            and capture.body.get("amount") == {"value": "2.00", "currency_code": "USD"}
            and capture.body.get("status") == ("PENDING" if key == "D" else "COMPLETED"),
            "C26_CAPTURE_UNCONFIRMED",
        )
        order = await http.request("GET", "/v2/checkout/orders/" + entry["order_id"])
        captures = (
            [
                item
                for unit in order.body.get("purchase_units", [])
                for item in unit.get("payments", {}).get("captures", [])
            ]
            if order.status_code == 200
            else []
        )
        require(
            any(item.get("id") == entry["capture_id"] for item in captures),
            "C26_CAPTURE_ORDER_MISMATCH",
        )
    dispute = await http.request("GET", "/v1/customer/disputes/" + resources["dispute_id"])
    require(
        dispute.status_code == 200
        and dispute.body.get("status") != "RESOLVED"
        and dispute.body.get("dispute_amount") == {"value": "2.00", "currency_code": "USD"}
        and any(
            item.get("seller_transaction_id") == resources["captures"]["D"]["capture_id"]
            for item in dispute.body.get("disputed_transactions", [])
        ),
        "C26_DISPUTE_UNCONFIRMED",
    )


async def execute(
    database_url,
    paypal,
    resources,
    store,
    *,
    target,
    clock,
    transport,
    advance,
    manual=None,
    poll_limit=120,
    holder=None,
):
    model = DemoModel()
    async with (
        Database(database_url) as database,
        PayPalHTTP(
            paypal,
            transport=LostResponse(PayoutTrace(transport, store), store),
            **({"monotonic": lambda: clock().timestamp()} if target == "mock" else {}),
        ) as http,
    ):
        if target == "sandbox":
            if not resources.get("dispute_id"):
                resources["dispute_id"] = store.data.get("dispute_id") or await manual.ask_id(
                    "Buyer browser: open one Item Not Received dispute on D's $2 capture in the sandbox Resolution Center. "
                    "Wait for discovery (usually 3-5 minutes), then supply its PP-D-... ID. On resume supply the existing ID; never open a second dispute."
                )
            store.checkpoint(dispute_id=resources["dispute_id"])
            await confirm_resources(http, resources)
        require(resources.get("dispute_id"), "C26_DISPUTE_ID_REQUIRED")
        store.checkpoint(dispute_id=resources["dispute_id"])
        if not store.data.get("inputs_logged"):
            capture_ids = [entry["capture_id"] for entry in resources["captures"].values()]
            order_ids = [entry["order_id"] for entry in resources["captures"].values()]
            store.record(
                "C26_INPUT_RESOURCES",
                capture_ids=capture_ids,
                order_ids=order_ids,
                dispute_id=resources["dispute_id"],
            )
            store.checkpoint(inputs_logged=True, capture_ids=capture_ids, order_ids=order_ids)
        async with Engine(
            EngineSettings(
                database_url,
                paypal.base_url,
                paypal.client_id,
                paypal.client_secret,
                paypal.webhook_id,
                clock,
                llm=model,
                paypal_behavior=paypal.behavior,
            ),
            database=database,
            http=http,
            agent_tools=read_registry(http, database),
        ) as engine:
            if holder is not None:
                holder["engine"] = engine
            if not store.data.get("week_start"):
                now = clock()
                store.checkpoint(
                    week_start=(now.date() - timedelta(days=now.weekday())).isoformat()
                )
            await engine.seed(engine_seed(resources))

            async def approve(proposal_id, payload_hash):
                if target == "sandbox":
                    await manual.confirm(
                        "Owner approval: pay R, U and L $1.80 each ($5.40 total); D's $1.80 remains reserved. "
                        "U has no PayPal account and will be canceled autonomously after GET UNCLAIMED. "
                        "Review exact payload hash "
                        + payload_hash
                        + ". Press Enter only to approve this exact run."
                    )
                await engine.approve(
                    proposal_id, payload_hash, "c26-owner" if target == "sandbox" else "mock-owner"
                )

            return await run_sandbox_integration(
                engine,
                http,
                store,
                model,
                resources,
                target=target,
                advance=advance,
                approve=approve,
                poll_limit=poll_limit,
            )


async def run_mock(database_url, output, *, poll_limit=120):
    """Real Engine and local mock; no live credentials even if process has them."""
    from eval.runner import mock_session

    database_guard(database_url)
    require(not (Path(output) / "state.json").exists(), "C26_MOCK_REHEARSAL_NEEDS_NEW_OUTPUT")
    resources, holder = mock_resources(), {}

    async def deliver(url, headers, body):
        if "engine" in holder:
            return await holder["engine"].deliver_webhook(headers, body)
        return 200

    async with mock_session(deliver) as (app, control):
        await control.post("/_mock/reset", json={"seed": 26})
        # The mock's own clock controls every delay, retry and token expiry.
        from mock_paypal.clock import MockClock

        app.state.store.clock = MockClock(MOCK_START)
        response = await control.post(
            "/_mock/seed",
            json={
                "balances": {"available": "100.00", "withheld": "0.00", "currency_code": "USD"},
                "registered_receivers": [resources["emails"][k] for k in "RLD"],
                "captures": [
                    {
                        "id": e["capture_id"],
                        "order_id": e["order_id"],
                        "amount": "2.00",
                        "create_time": e["captured_at"],
                    }
                    for e in resources["captures"].values()
                ],
                "config": {"payout_processing_seconds": 1, "balance_refresh_seconds": 1},
            },
        )
        response.raise_for_status()
        dispute = await control.post(
            "/_mock/disputes",
            json={
                "capture_id": resources["captures"]["D"]["capture_id"],
                "amount": "2.00",
                "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
            },
        )
        dispute.raise_for_status()
        resources["dispute_id"] = dispute.json()["dispute_id"]
        token = await control.post(
            "/v1/oauth2/token",
            auth=("mock-c26", "mock-only"),
            data={"grant_type": "client_credentials"},
        )
        token.raise_for_status()
        control.headers["Authorization"] = "Bearer " + token.json()["access_token"]
        webhook = await control.post(
            "/v1/notifications/webhooks",
            json={"url": "http://localhost:8098/webhooks/paypal", "event_types": [{"name": "*"}]},
        )
        webhook.raise_for_status()
        paypal = PayPalSettings(
            str(control.base_url).rstrip("/"), "mock-c26", "mock-only", webhook.json()["id"]
        )
        store = Checkpoint(
            output,
            target="mock",
            identity={
                "database": database_url,
                "case_set": "C26-v1",
                "execution_hash": execution_fingerprint(),
            },
            redactor=Redactor(secrets=(database_url,)),
        )
        try:

            async def advance():
                reply = await control.post("/_mock/clock/advance", json={"seconds": 60})
                reply.raise_for_status()

            rows = await execute(
                database_url,
                paypal,
                resources,
                store,
                target="mock",
                clock=lambda: app.state.store.clock.now,
                transport=httpx.ASGITransport(app=app),
                advance=advance,
                poll_limit=poll_limit,
                holder=holder,
            )
            write_integration_results(rows, output)
            return rows
        finally:
            store.close()


async def run_live(args, settings):
    target_guard("sandbox", False, settings)
    require(args.resources is not None, "C26_OWNER_RESOURCE_FILE_REQUIRED")
    resources = resource_manifest(json.loads(args.resources.read_text(encoding="utf-8")))
    tunnel = urlsplit(args.tunnel_url or "")
    require(
        tunnel.scheme == "https"
        and tunnel.hostname
        and not tunnel.username
        and not tunnel.password
        and not tunnel.query
        and not tunnel.fragment
        and tunnel.path in {"", "/"},
        "C26_HTTPS_TUNNEL_REQUIRED",
    )
    require(bool(settings.paypal.webhook_id), "C26_WEBHOOK_REQUIRED")
    redactor = Redactor(
        secrets=(
            settings.database_url,
            settings.owner_token,
            settings.paypal.client_id,
            settings.paypal.client_secret,
        ),
        email_aliases={email: k.lower() + "@redacted" for k, email in resources["emails"].items()},
    )
    relay = Relay("C26", args.relay_dir)
    manual = ManualSteps(redactor, input_fn=relay.input, output_fn=relay.output)
    store = Checkpoint(
        args.output,
        target="sandbox",
        identity={
            "database": settings.database_url,
            "paypal": settings.paypal.base_url,
            "client": settings.paypal.client_id,
            "webhook": settings.paypal.webhook_id,
            "resources": resources,
            "case_set": "C26-v1",
            "execution_hash": execution_fingerprint(),
        },
        redactor=redactor,
        output=relay.output,
    )
    try:
        async with PayPalHTTP(settings.paypal) as reader:
            await check_webhook(
                reader, settings.paypal.webhook_id, args.tunnel_url.rstrip("/") + "/webhooks/paypal"
            )

        async def advance():
            await asyncio.sleep(5)

        rows = await execute(
            settings.database_url,
            settings.paypal,
            resources,
            store,
            target="sandbox",
            clock=lambda: datetime.now(UTC),
            transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
            advance=advance,
            manual=manual,
            poll_limit=args.poll_limit,
        )
        write_integration_results(rows, args.output)
        relay.output("C26 complete: five GET/shield checks passed. Global invariants unavailable.")
        return rows
    finally:
        store.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["mock", "sandbox"], required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resources", type=Path)
    parser.add_argument("--tunnel-url")
    parser.add_argument("--relay-dir", type=Path, default=Path("recordings/sandbox/raw/relay"))
    parser.add_argument("--poll-limit", type=int, default=120)
    args = parser.parse_args(argv)
    try:
        require(
            (args.target == "mock" and args.dry_run)
            or (args.target == "sandbox" and not args.dry_run),
            "C26_EXPLICIT_TARGET_REQUIRED",
        )
        with asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop
            if sys.platform == "win32"
            else asyncio.new_event_loop
        ) as runner:
            if args.target == "mock":
                from eval.evaluate import disposable_database

                with disposable_database(
                    os.environ.get("COUNTERSIGN_TEST_DATABASE_URL", "")
                ) as url:
                    rows = runner.run(run_mock(url, args.output, poll_limit=args.poll_limit))
            else:
                settings = load_settings()  # Process settings only; never dotenv.
                target_guard("sandbox", False, settings)
                rows = runner.run(run_live(args, settings))
        print(f"C26: {len(rows)} checks completed; ground={args.target} (no provider calls).")
        return 0
    except Exception:  # noqa: BLE001 -- resource and driver failures must never disclose private values.
        print("C26 stopped; inspect redacted checkpoint/relay logs. No raw exception is printed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
