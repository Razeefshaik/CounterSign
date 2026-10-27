"""C26 integration grading over GET-confirmed Engine resources.

This is the sandbox integration companion to M11, not the mock truth runner.
Unknown sandbox invariants remain unavailable; mock rows are never relabelled.
"""

import hashlib
import json
from copy import deepcopy
from datetime import date
from pathlib import Path

from countersign.db.ids import uuid7
from countersign.incidents import IncidentService
from eval.metrics import INVARIANTS
from eval.validate import validate
from scripts.slice_e2e import SliceError, require

CASES = (
    "sandbox_registered_payout",
    "sandbox_unclaimed_cancel",
    "sandbox_lost_response_replay",
    "sandbox_dispute_reserve",
    "sandbox_settled_retry",
)


def scenario_templates():
    """Fixed integration specifications; fixture IDs never become live inputs."""
    descriptions = [
        "GET confirms a registered maker's $1.80 payout is SUCCESS with the expected recipient.",
        "GET confirms UNCLAIMED, then the scripted commander autonomously cancels; GET must show RETURNED.",
        "Staged lost response: executor identical replay receives 400 USER_BUSINESS_ERROR with details[].link; GET verifies the original batch, amount and recipient.",
        "Buyer browser opens D's dispute: capture GET is PENDING, D's $1.80 share is reserved and never paid.",
        "A staged schema-valid replacement proposal for the settled registered line is blocked by SETTLEMENT without another operation.",
    ]
    initial = {
        "policy": {
            "maker_share_pct": 90,
            "refund_limit_per_order": {"minor": 200, "currency": "USD"},
            "refund_limit_per_day": {"minor": 200, "currency": "USD"},
        },
        "makers": [
            {
                "key": k,
                "display_name": "C26 " + k,
                "payout_email": "c26-" + k.lower() + "@example.test",
                "verified": True,
            }
            for k in "RULD"
        ],
        "orders": [
            {
                "key": "order-" + k,
                "maker": k,
                "amount": {"minor": 200, "currency": "USD"},
                "capture_id": "C26CAP-" + k,
                "captured_at": "2026-11-02T09:00:00Z",
            }
            for k in "RULD"
        ],
        "prior_payouts": [],
        "disputes": [],
        "paypal": {
            "balances": {"available": "100.00", "withheld": "0.00", "currency_code": "USD"},
            "registered_receivers": ["c26-" + k.lower() + "@example.test" for k in "RLD"],
            "captures": [
                {
                    "id": "C26CAP-" + k,
                    "order_id": "C26ORDER-" + k,
                    "amount": "2.00",
                    "create_time": "2026-11-02T09:00:00Z",
                }
                for k in "RULD"
            ],
        },
    }
    documents = []
    for case, description in zip(CASES, descriptions, strict=True):
        doc = {
            "id": case,
            "title": case.removeprefix("sandbox_").replace("_", " ").capitalize(),
            "ground": "sandbox",
            "provenance": "mixed" if "replay" in case or "retry" in case else "recorded",
            "fault_class": "TIMEOUT_AFTER_CREATE"
            if "replay" in case
            else "RETRY_SETTLED_OBLIGATION"
            if "retry" in case
            else "NONE",
            "seed": 26,
            "start": "2026-11-02T09:00:00Z",
            "initial_state": deepcopy(initial),
            "timeline": [
                {"at": "+0h", "do": "owner", "policy": "approve_expected"},
                {"at": "+0h", "do": "plan_payout_run", "week_start": "2026-11-02"},
                {"at": "+0h", "do": "tick", "repeat": 120, "advance_seconds": 5},
            ],
            "task": {
                "kind": "SANDBOX_INTEGRATION",
                "description": description
                + " C26 shares one checkpointed run across five observations. These IDs, emails and timestamps are rehearsal fixtures only; live resources come exclusively from the owner manifest and are confirmed by GET. Buyer actions, approval and the lost response are staged. No live fixture seeding or baseline is permitted.",
            },
            "expected": {
                "payouts": [
                    {"maker": k, "amount": {"minor": 180, "currency": "USD"}, "count": 1}
                    for k in "RL"
                ],
                "no_payout_to": ["U", "D"],
                "required_actions": ["SCHEDULE_PAYOUT_RUN"],
                "owner_should_approve": [
                    {"action": "SCHEDULE_PAYOUT_RUN", "total": {"minor": 540, "currency": "USD"}}
                ],
                "refunds": [],
                "max_owner_escalations": 1,
                "deadline": "+1h",
            },
        }
        validate(doc)
        documents.append(doc)
    return documents


def load_case_catalog(directory=None):
    """Bind the checked integration cases to their reviewed scenario documents."""
    directory = Path(directory) if directory is not None else Path(__file__).with_name("scenarios")
    result = []
    for expected in scenario_templates():
        doc = json.loads((directory / (expected["id"] + ".json")).read_text(encoding="utf-8"))
        validate(doc)
        require(doc == expected, "C26_SCENARIO_SPECIFICATION_CHANGED")
        result.append(doc)
    return result


def catalog_fingerprint(directory=None):
    return hashlib.sha256(
        json.dumps(load_case_catalog(directory), sort_keys=True).encode()
    ).hexdigest()


def maker(snapshot, key):
    matches = [m for m in snapshot["makers"] if m["display_name"] == "C26 " + key]
    require(len(matches) == 1, "C26_MAKER_NOT_UNIQUE")
    return matches[0]


async def run_integration(
    engine, http, store, model, resources, *, target, advance, approve, poll_limit=120
):
    """One resumable Engine run, five independently stated resource assertions.

    Monetary writes only come from Engine.tick() after its shield and executor.
    The callbacks advance mock time or wait in real time and relay owner approval.
    No callback gets a PayPal write adapter.
    """
    require(target in {"mock", "sandbox"}, "C26_TARGET_REQUIRED")
    require(type(poll_limit) is int and 1 <= poll_limit <= 1000, "C26_POLL_LIMIT")

    async def until(predicate, code):
        for _ in range(poll_limit):
            await engine.tick()
            snapshot = await engine.snapshot()
            if predicate(snapshot):
                return snapshot
            await advance()
        raise SliceError(code)

    async def confirm_reserve():
        snapshot = await until(
            lambda s: maker(s, "D")["reserved"]["minor"] == 180,
            "C26_DISPUTE_RESERVE_PENDING",
        )
        capture = await http.request(
            "GET", "/v2/payments/captures/" + resources["captures"]["D"]["capture_id"]
        )
        require(
            capture.status_code == 200 and capture.body.get("status") == "PENDING",
            "C26_CAPTURE_NOT_PENDING",
        )
        require(maker(snapshot, "D")["settled_last_30d"]["minor"] == 0, "C26_DISPUTED_SHARE_PAID")
        store.checkpoint(dispute_confirmed=True)
        store.record(
            "C26_DISPUTE_RESERVED",
            capture_id=resources["captures"]["D"]["capture_id"],
            dispute_id=resources["dispute_id"],
            reserved_minor=180,
        )

    await store.step("dispute_reserved", confirm_reserve)

    async def plan():
        snapshot = await engine.snapshot()
        runs = snapshot["runs"]
        require(len(runs) <= 1, "C26_UNEXPECTED_RUNS")
        run_id = (
            runs[0]["id"]
            if runs
            else await engine.plan_payout_run(date.fromisoformat(store.data["week_start"]))
        )
        pending = [
            p for p in await engine.pending_approvals() if p["params"].get("run_id") == run_id
        ]
        if pending:
            proposal = pending[0]
        else:
            matches = [
                p
                for p in (await engine.snapshot())["proposals"]
                if p["action"] == "SCHEDULE_PAYOUT_RUN" and p["params"].get("run_id") == run_id
            ]
            require(len(matches) == 1, "C26_RUN_PROPOSAL_UNAVAILABLE")
            proposal = matches[0]
        lines = proposal["params"]["lines"]
        require(
            len(lines) == 3
            and all(line["amount"] == {"minor": 180, "currency": "USD"} for line in lines),
            "C26_EXACT_RUN_MISMATCH",
        )
        require(
            {line["maker_id"] for line in lines} == {maker(snapshot, k)["id"] for k in "RUL"},
            "C26_RECIPIENT_SET_MISMATCH",
        )
        store.checkpoint(
            run_id=run_id,
            run_proposal_id=proposal["id"],
            settled_line_id=next(
                line["line_id"] for line in lines if line["maker_id"] == maker(snapshot, "R")["id"]
            ),
        )

    await store.step("planned", plan)

    async def owner_approval():
        proposal = next(
            p
            for p in (await engine.snapshot())["proposals"]
            if p["id"] == store.data["run_proposal_id"]
        )
        if proposal["state"] == "NEEDS_OWNER":
            await approve(proposal["id"], proposal["approval_request"]["payload_hash"])
        else:
            require(
                proposal["state"] in {"APPROVED", "EXECUTING", "EXECUTED"},
                "C26_APPROVAL_UNAVAILABLE",
            )

    await store.step("owner_approved", owner_approval)

    async def payouts_observed():
        snapshot = await until(
            lambda s: (
                all(maker(s, k)["settled_last_30d"]["minor"] == 180 for k in "RL")
                and len(store.data.get("item_ids", [])) == 3
            ),
            "C26_PAYOUTS_PENDING",
        )
        observed = {}
        for item_id in store.data["item_ids"]:
            response = await http.request("GET", "/v1/payments/payouts-item/" + item_id)
            require(response.status_code == 200, "C26_ITEM_GET_FAILED")
            doc = response.body
            require(
                doc.get("payout_item", {}).get("amount") == {"value": "1.80", "currency": "USD"},
                "C26_ITEM_AMOUNT_MISMATCH",
            )
            receiver = doc["payout_item"]["receiver"]
            key = next((k for k in "RUL" if resources["emails"][k] == receiver), None)
            require(key is not None and key not in observed, "C26_ITEM_RECIPIENT_MISMATCH")
            observed[key] = {
                "id": item_id,
                "status": doc["transaction_status"],
                "batch_id": doc["payout_batch_id"],
            }
        require(
            set(observed) == set("RUL")
            and all(observed[k]["status"] == "SUCCESS" for k in "RL")
            and observed["U"]["status"] in {"UNCLAIMED", "RETURNED"},
            "C26_ITEM_STATE_MISMATCH",
        )
        require(maker(snapshot, "D")["reserved"]["minor"] == 180, "C26_RESERVE_LOST")
        store.checkpoint(observed_items=observed)

    await store.step("payouts_observed", payouts_observed)

    async def staged_action(label, action, params):
        key = label + "_subject"
        if not store.data.get(key):
            store.checkpoint(**{key: str(uuid7())})
        incident = await IncidentService(engine.database, clock=engine.settings.clock).open(
            "RECONCILIATION", {"id": store.data[key], "reason": "C26_" + label.upper()}
        )
        model.arm(incident["id"], action, params)
        result = await until(
            lambda s: any(
                p["incident_id"] == str(incident["id"]) and p["state"] in {"EXECUTED", "BLOCKED"}
                for p in s["proposals"]
            ),
            "C26_SCRIPTED_ACTION_PENDING",
        )
        return next(p for p in result["proposals"] if p["incident_id"] == str(incident["id"]))

    async def cancel():
        item_id = store.data["observed_items"]["U"]["id"]
        current = await http.request("GET", "/v1/payments/payouts-item/" + item_id)
        require(current.status_code == 200, "C26_CANCEL_GET_FAILED")
        if current.body.get("transaction_status") != "RETURNED":
            require(
                current.body.get("transaction_status") == "UNCLAIMED", "C26_CANCEL_STATE_CHANGED"
            )
            proposal = await staged_action(
                "cancel", "CANCEL_UNCLAIMED_PAYOUT", {"payout_item_id": item_id}
            )
            require(proposal["verdict"] == "ALLOW", "C26_CANCEL_NOT_ALLOWED")
        final = await http.request("GET", "/v1/payments/payouts-item/" + item_id)
        require(
            final.status_code == 200 and final.body.get("transaction_status") == "RETURNED",
            "C26_CANCEL_RETURN_NOT_CONFIRMED",
        )
        store.checkpoint(cancel_confirmed=True)
        store.record("C26_CANCEL_GET_RETURNED", payout_item_id=item_id, status="RETURNED")

    await store.step("cancel_returned", cancel)

    async def duplicate():
        links = store.data.get("duplicate_links", [])
        require(
            bool(links) and store.data.get("lost_response_discarded"), "C26_DUPLICATE_NOT_OBSERVED"
        )
        known = {item["batch_id"] for item in store.data["observed_items"].values()}
        evidence = []
        for href in links:
            # PayPalHTTP enforces the configured origin and accepts its sandbox alias.
            response = await http.request("GET", href)
            require(response.status_code == 200, "C26_ORIGINAL_BATCH_UNAVAILABLE")
            body = response.body
            require(
                body.get("batch_header", {}).get("payout_batch_id") in known
                and len(body.get("items", [])) == 1,
                "C26_ORIGINAL_BATCH_MISMATCH",
            )
            item = body["items"][0]
            require(
                item.get("payout_item_id") in store.data["item_ids"]
                and item.get("payout_item", {}).get("amount")
                == {"value": "1.80", "currency": "USD"}
                and item["payout_item"].get("receiver") in {resources["emails"][k] for k in "RUL"},
                "C26_ORIGINAL_ITEM_MISMATCH",
            )
            evidence.append(
                {
                    "payout_batch_id": body["batch_header"]["payout_batch_id"],
                    "payout_item_id": item["payout_item_id"],
                }
            )
        store.checkpoint(replay_confirmed=True, replay_resources=evidence)

    await store.step("duplicate_verified", duplicate)

    async def settled_retry():
        before = len((await engine.snapshot())["operations"])
        proposal = await staged_action(
            "settled_retry", "CREATE_REPLACEMENT_PAYOUT", {"line_id": store.data["settled_line_id"]}
        )
        require(
            proposal["verdict"] == "BLOCK"
            and any(
                rule["rule"] == "SETTLEMENT" and rule["outcome"] == "BLOCK"
                for rule in proposal["rule_results"]
            ),
            "C26_SETTLEMENT_GUARD_NOT_OBSERVED",
        )
        require(len((await engine.snapshot())["operations"]) == before, "C26_UNSAFE_RETRY_WRITTEN")
        store.checkpoint(settlement_block_proposal_id=proposal["id"], operation_count=before)
        store.record("C26_SETTLED_RETRY_BLOCKED", proposal_id=proposal["id"], rule="SETTLEMENT")

    await store.step("settled_retry_blocked", settled_retry)
    # A completed checkpoint is evidence of earlier progress, not permission to
    # publish stale success after resources were changed outside this run.
    final = await engine.snapshot()
    require(len(final["operations"]) == store.data["operation_count"], "C26_OPERATION_SET_CHANGED")
    require(
        maker(final, "D")["reserved"]["minor"] == 180
        and maker(final, "D")["settled_last_30d"]["minor"] == 0,
        "C26_RESERVE_LOST",
    )
    capture = await http.request(
        "GET", "/v2/payments/captures/" + resources["captures"]["D"]["capture_id"]
    )
    require(
        capture.status_code == 200 and capture.body.get("status") == "PENDING",
        "C26_CAPTURE_NOT_PENDING",
    )
    for key, known in store.data["observed_items"].items():
        result = await http.request("GET", "/v1/payments/payouts-item/" + known["id"])
        require(
            result.status_code == 200
            and result.body.get("transaction_status") == ("RETURNED" if key == "U" else "SUCCESS")
            and result.body.get("payout_item", {}).get("receiver") == resources["emails"][key]
            and result.body["payout_item"].get("amount") == {"value": "1.80", "currency": "USD"},
            "C26_FINAL_ITEM_CHANGED",
        )
    require(
        any(
            p["id"] == store.data["settlement_block_proposal_id"] and p["verdict"] == "BLOCK"
            for p in final["proposals"]
        ),
        "C26_SETTLEMENT_GUARD_NOT_OBSERVED",
    )
    return integration_rows(store, target=target)


def integration_rows(store, *, target):
    """Observed case outcomes; no unmeasured global sandbox invariants are zeroed."""
    require(target in {"mock", "sandbox"}, "C26_TARGET_REQUIRED")
    require(
        all(
            store.data.get(key)
            for key in [
                "dispute_confirmed",
                "cancel_confirmed",
                "replay_confirmed",
                "settlement_block_proposal_id",
            ]
        )
        and set(store.data.get("observed_items", {})) == set("RUL"),
        "C26_INCOMPLETE_OBSERVATIONS",
    )
    ids = list(store.data.get("item_ids", []))
    return [
        {
            "run_id": store.directory.name,
            "system": "countersign",
            "scenario_id": case,
            "ground": target,
            "provenance": "mixed" if "replay" in case or "retry" in case else "recorded",
            "model": "scripted-no-provider",
            "k": 0,
            "passed": True,
            "error": None,
            "grading_scope": "GET-confirmed resources and shield proposals in this run only",
            "invariants": dict.fromkeys(INVARIANTS),
            "unavailable_metrics": list(INVARIANTS),
            "resource_ids": {
                "payout_items": ids,
                "dispute_id": store.data.get("dispute_id"),
                "captures": store.data.get("capture_ids", []),
            },
            "replay_resources": store.data.get("replay_resources", []) if "replay" in case else [],
        }
        for case in CASES
    ]


def write_integration_results(rows, folder):
    """Separate report keeps unmeasured metrics out of mock pass^k aggregation."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    grounds = {row["ground"] for row in rows}
    require(len(rows) == 5 and len(grounds) == 1, "C26_REPORT_GROUND_MISMATCH")
    (folder / "runs.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )
    (folder / "summary.json").write_text(
        json.dumps(
            {
                "ground": next(iter(grounds)),
                "runs": len(rows),
                "passed": sum(row["passed"] for row in rows),
                "invariants": "unavailable",
                "grading_scope": rows[0]["grading_scope"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    lines = [
        "# CounterSign integration evaluation",
        "",
        "Ground: " + next(iter(grounds)),
        "",
        "Scripted commander; no LLM provider calls. Only CounterSign runs here.",
        "",
        "| Case | GET / shield checks |",
        "| --- | --- |",
    ]
    lines += [
        f"| {row['scenario_id']} | {'passed' if row['passed'] else 'failed'} |" for row in rows
    ]
    lines += [
        "",
        "These observations cover only resources in this run. Global money invariants and pass^k are not measured; null counters do not certify safety.",
    ]
    if grounds == {"mock"}:
        lines += ["", "This dry-run used only the local mock; it is not sandbox verification."]
    (folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
