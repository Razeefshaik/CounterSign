"""D8: open disputes as the sandbox buyer and record what PayPal does (verify #3 and #5).

API creation needs the disputes/create scope, which only a PayPal account manager adds,
plus buyer consent (F5). It is tried only when D1 saw that scope; the Resolution Center
route is the expected path. Dispute helpers shared with D9 and D10 live in this module.

Capture IDs come from D2's saved data (data["captures"][role], a string or a dict with
"capture_id"); when D2 has none, the operator is asked for each one.
"""

import base64
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..recorder import timestamp
from ..runner import CheckContext, Step

ROLES = {
    "inr_400": {
        "label": "$400 order for Maker A",
        "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        "ui_choice": "item not received",
    },
    "snad_60": {
        "label": "$60 order for Maker B",
        "reason": "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
        "ui_choice": "item significantly not as described",
    },
    "spare_25": {
        "label": "$25 spare order",
        "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        "ui_choice": "item not received",
    },
}
RESOLUTION_CENTER = "https://www.sandbox.paypal.com/disputes/dashboard/"
POLL_SECONDS = 60
FIND_MAX_POLLS = 60  # one hour per dispute, then recorded as not found
HOLD_INTERVAL_SECONDS = 300
HOLD_OBSERVE_SECONDS = 3 * 3600
STAGED = "Staged sandbox action for CounterSign day one checks."
RESOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


class DisputeCheckError(RuntimeError):
    """A dispute call failed; the message names only the step and HTTP status."""


def now(ctx: CheckContext) -> str:
    return timestamp(ctx.clock())


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def resource_id(value) -> str:
    """Only PayPal-style IDs go into request paths."""
    if not isinstance(value, str) or not RESOURCE_ID.fullmatch(value):
        raise DisputeCheckError("A PayPal resource ID had an unexpected format.")
    return value


def payload(response) -> dict:
    try:
        value = response.json()
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def rels(detail: dict) -> list[str]:
    links = detail.get("links") if isinstance(detail.get("links"), list) else []
    return sorted({link["rel"] for link in links if isinstance(link, dict) and link.get("rel")})


def has_rel(rel_names, name: str) -> bool:
    """HATEOAS rel spelling is [pending D8b]; accept provide_evidence or provide-evidence."""
    wanted = name.replace("-", "_")
    return any(rel.replace("-", "_") == wanted for rel in rel_names)


def summarize(detail: dict) -> dict:
    return {
        "status": detail.get("status"),
        "dispute_life_cycle_stage": detail.get("dispute_life_cycle_stage"),
        "reason": detail.get("reason"),
        "dispute_amount": detail.get("dispute_amount"),
        "seller_response_due_date": detail.get("seller_response_due_date"),
        "dispute_outcome": detail.get("dispute_outcome"),
        "rels": rels(detail),
    }


def minor(value) -> int | None:
    """Convert a PayPal amount string to cents; None when absent or malformed."""
    try:
        return int(Decimal(str(value)) * 100)
    except (InvalidOperation, TypeError, ValueError):
        return None


async def get_dispute(ctx: CheckContext, dispute_id: str, *, step: str) -> dict:
    response = await ctx.client.request(
        "GET", f"/v1/customer/disputes/{resource_id(dispute_id)}", step=step
    )
    if response.status_code != 200:
        raise DisputeCheckError(f"{step} returned HTTP {response.status_code}.")
    return payload(response)


async def poll_dispute(
    ctx: CheckContext, dispute_id: str, *, done, step: str, max_polls: int
) -> tuple[dict, list[dict]]:
    """GET until done(detail) or max_polls; return the last detail and each observed change."""
    history: list[dict] = []
    detail: dict = {}
    for poll in range(max_polls):
        if poll:
            await ctx.sleep(POLL_SECONDS)
        detail = await get_dispute(ctx, dispute_id, step=step)
        state = {
            "status": detail.get("status"),
            "dispute_life_cycle_stage": detail.get("dispute_life_cycle_stage"),
        }
        if not history or {k: history[-1][k] for k in state} != state:
            history.append({"at": now(ctx), **state, "rels": rels(detail)})
        if done(detail):
            break
    return detail, history


async def post_action(
    ctx: CheckContext, dispute_id: str, action: str, *, step: str, body=None, files=None
):
    """POST a dispute action; the caller checkpoints before calling."""
    response = await ctx.client.request(
        "POST",
        f"/v1/customer/disputes/{resource_id(dispute_id)}/{action}",
        step=step,
        json=body if files is None else None,
        files=files,
    )
    data = payload(response)
    return {"status": response.status_code, "error_name": data.get("name")}


async def balance_snapshot(ctx: CheckContext, *, step: str) -> dict:
    response = await ctx.client.request(
        "GET", "/v1/reporting/balances", step=step, params={"currency_code": "USD"}
    )
    data = payload(response)
    snapshot = {"fetched_at": now(ctx), "status": response.status_code}
    for balance in data.get("balances") or []:
        if isinstance(balance, dict) and balance.get("currency") == "USD":
            for name in ("available_balance", "withheld_balance", "total_balance"):
                amount = balance.get(name) if isinstance(balance.get(name), dict) else {}
                snapshot[name.removesuffix("_balance")] = amount.get("value")
    snapshot["last_refresh_time"] = data.get("last_refresh_time")
    snapshot["as_of_time"] = data.get("as_of_time")
    return snapshot


def webhook_deliveries(ctx: CheckContext, event_type: str, dispute_ids) -> list[dict]:
    """Read C04 catcher copies (recordings/sandbox/webhooks/*.json) for these disputes."""
    wanted = set(dispute_ids)
    found = []
    for path in sorted(Path(ctx.root, "webhooks").glob("*.json")):
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        body = event.get("body") if isinstance(event.get("body"), dict) else {}
        resource = body.get("resource") if isinstance(body.get("resource"), dict) else {}
        if event.get("event_type") != event_type or resource.get("dispute_id") not in wanted:
            continue
        delivery = {
            "event_id": event.get("event_id"),
            "dispute_id": resource.get("dispute_id"),
            "create_time": event.get("create_time"),
            "received_at": event.get("received_at"),
            "file": path.relative_to(ctx.root).as_posix(),
        }
        try:
            delay = parse_time(delivery["received_at"]) - parse_time(delivery["create_time"])
            delivery["delay_seconds"] = round(delay.total_seconds(), 3)
        except (AttributeError, TypeError, ValueError):
            delivery["delay_seconds"] = None
        found.append(delivery)
    return found


def evidence_files(ctx: CheckContext) -> list[str]:
    return [
        path.relative_to(ctx.root).as_posix()
        for path in sorted(ctx.check_dir.glob("[0-9]*_*.json"))
    ]


def write_findings(ctx: CheckContext, finding: dict) -> dict:
    finding = {
        **finding,
        "observed_at": ctx.state["started_at"],
        "evidence_files": evidence_files(ctx),
    }
    ctx.write_json(ctx.check_dir / "findings.json", finding)
    ctx.note(f"{finding['check_id']}: {finding['verdict']}")
    return finding


def matched_field(detail: dict, capture_id: str) -> str | None:
    """Name the disputed_transactions field that carries our capture ID ([pending D8b])."""
    for transaction in detail.get("disputed_transactions") or []:
        if isinstance(transaction, dict):
            for key, value in transaction.items():
                if value == capture_id:
                    return key
    return None


async def find_dispute(
    ctx: CheckContext, capture_id: str, *, submitted_at: str, skip: set[str], step: str
) -> dict:
    """Poll the dispute list every minute until a dispute on capture_id appears."""
    since = timestamp(parse_time(submitted_at) - timedelta(minutes=10))
    checked = set(skip)
    for poll in range(FIND_MAX_POLLS):
        if poll:
            await ctx.sleep(POLL_SECONDS)
        response = await ctx.client.request(
            "GET",
            "/v1/customer/disputes",
            step=f"{step}_list",
            params={"start_time": since, "page_size": 50},
        )
        if response.status_code != 200:
            continue
        for item in payload(response).get("items") or []:
            dispute_id = item.get("dispute_id") if isinstance(item, dict) else None
            if not dispute_id or dispute_id in checked:
                continue
            checked.add(dispute_id)
            detail = await get_dispute(ctx, dispute_id, step=f"{step}_detail")
            field = matched_field(detail, capture_id)
            if field:
                seen = now(ctx)
                return {
                    "dispute_id": dispute_id,
                    "first_seen_at": seen,
                    "seconds_to_appear": round(
                        (parse_time(seen) - parse_time(submitted_at)).total_seconds(), 3
                    ),
                    "matched_field": field,
                    "polls": poll + 1,
                }
    return {"not_found": True, "polls": FIND_MAX_POLLS}


def d2_capture(d2: dict, role: str) -> str | None:
    entry = (d2.get("captures") or {}).get(role)
    value = entry.get("capture_id") if isinstance(entry, dict) else entry
    return value if isinstance(value, str) and RESOURCE_ID.fullmatch(value) else None


def auth_assertion(client_id: str, buyer_email: str) -> str:
    """Unsigned JWT naming the sandbox buyer, as PayPal's dispute set-up page documents."""

    def part(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part({'iss': client_id, 'email': buyer_email})}."


async def load_inputs(ctx: CheckContext) -> dict:
    d1 = ctx.load_check_data("D1")
    d2 = ctx.load_check_data("D2")
    captures = dict(ctx.data.get("captures") or {})
    for role, info in ROLES.items():
        if role not in captures:
            captures[role] = d2_capture(d2, role) or await ctx.manual.ask_id(
                f"D2 has no capture ID for the {info['label']}. Enter its capture ID."
            )
            ctx.checkpoint(captures=captures)
    baseline = ctx.data.get("baseline_balance") or await balance_snapshot(
        ctx, step="baseline_balance"
    )
    return {
        "captures": captures,
        "create_scope": bool((d1.get("permissions") or {}).get("disputes_create")),
        "baseline_balance": baseline,
    }


async def api_route(ctx: CheckContext) -> dict:
    if not ctx.data["create_scope"]:
        ctx.note("D8 API route: disputes/create scope absent, so the API route is not tried.")
        return {
            "api_route": {
                "verdict": "BLOCKED",
                "reason": "disputes/create scope absent; a PayPal account manager must add it (F5)",
            }
        }
    if ctx.data.get("api_attempted_at"):
        # Interrupted after the request was sent: never send a second create; find_disputes
        # looks for it instead of asking the buyer to open another one.
        disputes = dict(ctx.data.get("disputes") or {})
        disputes["spare_25"] = {"route": "API", "submitted_at": ctx.data["api_attempted_at"]}
        return {
            "api_route": {"verdict": "UNKNOWN", "reason": "interrupted after the request"},
            "disputes": disputes,
        }
    settings = ctx.client.settings
    if not settings.buyer_email:
        return {"api_route": {"verdict": "BLOCKED", "reason": "SBX_BUYER_EMAIL is not set"}}
    await ctx.manual.confirm(
        "D8 API route: as buyer-1, complete Log in with PayPal consent for this app with the "
        "disputes/create scope. Label: staged sandbox action."
    )
    buyer_transaction = await ctx.manual.ask_id(
        "Enter buyer-1's transaction ID for the $25 spare payment (buyer activity page)."
    )
    assertion = auth_assertion(settings.client_id, settings.buyer_email)
    ctx.recorder.redactor.add_secret(assertion)
    body = {
        "disputed_transactions": [{"buyer_transaction_id": buyer_transaction}],
        "reason": ROLES["spare_25"]["reason"],
        "dispute_amount": {"currency_code": "USD", "value": "25.00"},
    }
    submitted = now(ctx)
    ctx.checkpoint(api_attempted_at=submitted)
    response = await ctx.client.request(
        "POST",
        "/v1/customer/disputes",
        step="api_create_dispute",
        headers={"PayPal-Auth-Assertion": assertion},
        files={"input": (None, json.dumps(body), "application/json")},
    )
    data = payload(response)
    if response.status_code != 201:
        return {
            "api_route": {
                "verdict": "BLOCKED",
                "status": response.status_code,
                "error_name": data.get("name"),
            }
        }
    links = data.get("links") if isinstance(data.get("links"), list) else []
    href = next((link.get("href", "") for link in links if isinstance(link, dict)), "")
    dispute_id = data.get("dispute_id") or href.rstrip("/").rsplit("/", 1)[-1]
    disputes = dict(ctx.data.get("disputes") or {})
    disputes["spare_25"] = {
        "route": "API",
        "submitted_at": submitted,
        "dispute_id": resource_id(dispute_id),
        "seconds_to_appear": 0,
        "matched_field": None,
    }
    return {"api_route": {"verdict": "CONFIRMED", "status": 201}, "disputes": disputes}


async def open_in_resolution_center(ctx: CheckContext) -> dict:
    disputes = dict(ctx.data.get("disputes") or {})
    for role, info in ROLES.items():
        if role in disputes:
            continue
        await ctx.manual.confirm(
            f"D8: log in to the sandbox as buyer-1 and open {RESOLUTION_CENTER}. Report a "
            f"problem on the {info['label']} (capture {ctx.data['captures'][role]}) and "
            f"choose '{info['ui_choice']}'. Label: staged sandbox action. Press Enter right "
            "after submitting."
        )
        # Confirmation time is an upper bound on the submit time.
        disputes[role] = {"route": "RESOLUTION_CENTER", "submitted_at": now(ctx)}
        ctx.checkpoint(disputes=disputes)
    return {"disputes": disputes}


async def find_disputes(ctx: CheckContext) -> dict:
    disputes = {role: dict(entry) for role, entry in ctx.data["disputes"].items()}
    for role, entry in disputes.items():
        if entry.get("dispute_id") or entry.get("not_found"):
            continue
        known = {other["dispute_id"] for other in disputes.values() if other.get("dispute_id")}
        entry.update(
            await find_dispute(
                ctx,
                ctx.data["captures"][role],
                submitted_at=entry["submitted_at"],
                skip=known,
                step=f"find_{role}",
            )
        )
        ctx.checkpoint(disputes=disputes)
    return {"disputes": disputes}


async def inspect_disputes(ctx: CheckContext) -> dict:
    disputes = {role: dict(entry) for role, entry in ctx.data["disputes"].items()}
    for role, entry in disputes.items():
        if entry.get("dispute_id"):
            detail = await get_dispute(ctx, entry["dispute_id"], step=f"inspect_{role}")
            entry["initial"] = summarize(detail)
            entry["disputed_transactions"] = detail.get("disputed_transactions")
    return {"disputes": disputes}


async def capture_status(ctx: CheckContext, capture_id: str, *, step: str):
    response = await ctx.client.request(
        "GET", f"/v2/payments/captures/{resource_id(capture_id)}", step=step
    )
    return payload(response).get("status") if response.status_code == 200 else None


async def observe_hold(ctx: CheckContext) -> dict:
    """Capture status and balances every 5 minutes for 3 hours; resumes where it stopped."""
    observations = list(ctx.data.get("hold_observations") or [])
    total = HOLD_OBSERVE_SECONDS // HOLD_INTERVAL_SECONDS + 1
    disputed = [role for role, entry in ctx.data["disputes"].items() if entry.get("dispute_id")]
    while len(observations) < total:
        if observations:
            await ctx.sleep(HOLD_INTERVAL_SECONDS)
        observation = {
            "at": now(ctx),
            "balance": await balance_snapshot(ctx, step="hold_balance"),
            "captures": {
                role: await capture_status(
                    ctx, ctx.data["captures"][role], step=f"hold_capture_{role}"
                )
                for role in disputed
            },
        }
        observations.append(observation)
        ctx.checkpoint(hold_observations=observations)
    return {"hold_observations": observations}


def withheld_minor(snapshot: dict) -> int | None:
    """Withheld cents from a successful balance read; None means no evidence."""
    if not isinstance(snapshot, dict) or snapshot.get("status") != 200:
        return None
    return minor(snapshot.get("withheld"))


def hold_result(ctx: CheckContext) -> dict:
    """True/False/PARTIAL only from valid reads; missing evidence is None and BLOCKED.

    A failed balance call or a missing withheld value is not evidence of "no hold".
    """
    observations = ctx.data["hold_observations"]
    amounts = [
        minor(((entry.get("initial") or {}).get("dispute_amount") or {}).get("value"))
        for entry in ctx.data["disputes"].values()
        if entry.get("dispute_id")
    ]
    expected = sum(amounts) if amounts and None not in amounts else None
    baseline = withheld_minor(ctx.data["baseline_balance"])
    valid = [
        (observation["at"], withheld)
        for observation in observations
        if (withheld := withheld_minor(observation["balance"])) is not None
    ]
    missing = []
    if baseline is None:
        missing.append("no valid baseline withheld balance")
    if not valid:
        missing.append("no valid withheld balance after the disputes")
    if expected is None:
        missing.append("no dispute amounts to compare with")
    result = {
        "valid_observations": len(valid),
        "total_observations": len(observations),
        "expected_withheld_increase_minor": expected,
        "largest_withheld_increase_minor": None,
        "first_full_hold_seen_at": None,
    }
    if missing:
        result.update(hold_observed=None, hold_verdict="BLOCKED", missing_evidence=missing)
    else:
        largest = max(withheld - baseline for _, withheld in valid)
        first_full = next((at for at, w in valid if w - baseline >= expected), None)
        if largest >= expected:
            observed, verdict = True, "CONFIRMED"
        elif largest <= 0:
            observed, verdict = False, "DIFFERENT"
        else:
            observed, verdict = "PARTIAL", "DIFFERENT"
        result.update(
            hold_observed=observed,
            hold_verdict=verdict,
            largest_withheld_increase_minor=largest,
            first_full_hold_seen_at=first_full,
        )
    return {
        **result,
        "capture_statuses_seen": sorted(
            {
                f"{role}:{status}"
                for observation in ctx.data["hold_observations"]
                for role, status in observation["captures"].items()
            }
        ),
    }


async def findings(ctx: CheckContext) -> dict:
    disputes = ctx.data["disputes"]
    found = {
        role: entry["dispute_id"] for role, entry in disputes.items() if entry.get("dispute_id")
    }
    hold = hold_result(ctx)
    if not found:
        creation = "BLOCKED"
    elif len(found) == len(ROLES):
        creation = "CONFIRMED"
    else:
        creation = "DIFFERENT"
    verdicts = {creation, hold["hold_verdict"]}
    if "BLOCKED" in verdicts:
        verdict = "BLOCKED"
    elif verdicts == {"CONFIRMED"}:
        verdict = "CONFIRMED"
    else:
        verdict = "DIFFERENT"
    return write_findings(
        ctx,
        {
            "check_id": "D8",
            "verdict": verdict,
            "creation_verdict": creation,
            "hold_verdict": hold["hold_verdict"],
            "api_route": ctx.data["api_route"],
            "disputes": disputes,
            "not_found": sorted(set(ROLES) - set(found)),
            "hold": hold,
            "created_webhooks": webhook_deliveries(ctx, "CUSTOMER.DISPUTE.CREATED", found.values()),
            "notes": [
                "submitted_at is when the operator confirmed, an upper bound on the submit time",
                "balance figures are as PayPal reported them; see D12 for refresh lag",
            ],
        },
    )


def get_steps() -> list[Step]:
    return [
        Step("load_inputs", load_inputs),
        Step("api_route", api_route),
        Step("open_in_resolution_center", open_in_resolution_center),
        Step("find_disputes", find_disputes),
        Step("inspect_disputes", inspect_disputes),
        Step("observe_hold", observe_hold),
        Step("findings", findings),
    ]
