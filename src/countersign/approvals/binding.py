"""C48: the internal and external agent share exactly one locked proposal path.

Caller owns the transaction. No approval or execution is performed here.
"""

from uuid import UUID

from countersign.approvals.service import RUN_REQUEST_LIFETIME, load_context
from countersign.db import Repositories, cas_transition
from countersign.fsm.incident import INCIDENT, IncidentEvent
from countersign.fsm.proposal import PROPOSAL, VERDICT_EVENT, ProposalEvent
from countersign.ledger import journal
from countersign.ledger.reservations import create_reservation
from countersign.money import Money
from countersign.shield.engine import Verdict, evaluate
from countersign.shield.snapshot import NON_FINANCIAL, PAYING, Action


async def bind_agent_proposal(
    conn, observed, proposed, *, model_id, clock, behavior, lifetime, source=None
):
    await journal.serialize_on(conn, "countersign:owner-decisions")
    repos = Repositories(conn)

    async def audit(**values):
        if source is not None:
            values["details"] = {**values["details"], "source": source}
        await repos.audit_log.insert(**values)

    incident = await repos.incidents.get(observed["id"], for_update=True)
    if (
        incident is None
        or incident["version"] != observed["version"]
        or incident["state"] not in {"OPEN", "INVESTIGATING"}
    ):
        return
    cursor = await conn.execute(
        "SELECT 1 FROM proposals WHERE incident_id=%s AND "
        "(state IN ('CREATED','ALLOWED','NEEDS_OWNER','APPROVED','EXECUTING') OR "
        "raw_output#>>'{agent_context,incident_version}'=%s) LIMIT 1",
        (incident["id"], str(incident["version"])),
    )
    if await cursor.fetchone():
        return
    if incident["state"] == "OPEN":
        await cas_transition(
            conn,
            INCIDENT,
            "incidents",
            incident["id"],
            incident["state"],
            incident["version"],
            IncidentEvent.INVESTIGATION_STARTED,
        )
        incident = await repos.incidents.get(incident["id"])
    document = proposed.to_dict()
    raw = {**proposed.raw_output, "flags": list(proposed.flags)}
    # Commander supplies the bounded trace, independently of model JSON.
    # No tool results or private provider conversation are persisted here.
    trace = raw.get("agent_trace")
    if isinstance(trace, dict):
        raw["agent_trace"] = {
            "model": model_id,
            "rounds": trace["rounds"],
            "calls": trace["calls"][:16],
        }
    row = await repos.proposals.insert(
        incident_id=incident["id"],
        action=document["action"],
        params=document["params"],
        evidence_refs=document["evidence_refs"],
        rationale=document["rationale"],
        expected_effect=document["expected_effect"],
        model=model_id,
        raw_output=raw,
        state="CREATED",
        created_at=clock(),
    )
    proposal, snap, payload = await load_context(conn, row, now=clock(), behavior=behavior)
    decision = evaluate(proposal, snap)
    details = {"rule_results": decision.rule_results(), "operation_payload": payload}
    if decision.verdict is Verdict.NEEDS_OWNER:
        binding = {
            "payload_hash": proposal.payload_hash,
            "record_versions": snap.record_versions(),
            "expires_at": (
                snap.now
                + (
                    RUN_REQUEST_LIFETIME
                    if proposal.action is Action.SCHEDULE_PAYOUT_RUN
                    else lifetime
                )
            ).isoformat(),
        }
        details["approval_request"] = binding
        raw["approval_request"] = binding
    row = await repos.proposals.update(
        row["id"],
        verdict=str(decision.verdict),
        verdict_rules=[str(rule) for rule in decision.rules],
        verdict_details=details,
    )
    state = await cas_transition(
        conn,
        PROPOSAL,
        "proposals",
        row["id"],
        row["state"],
        row["version"],
        VERDICT_EVENT[str(decision.verdict)],
    )
    if decision.verdict is Verdict.NEEDS_OWNER:
        await cas_transition(
            conn,
            INCIDENT,
            "incidents",
            incident["id"],
            incident["state"],
            incident["version"],
            IncidentEvent.OWNER_NEEDED,
        )
    elif decision.verdict is Verdict.ALLOW and proposal.action in NON_FINANCIAL:
        for event in (ProposalEvent.EXECUTION_STARTED, ProposalEvent.EXECUTION_SUCCEEDED):
            current = await repos.proposals.get(row["id"])
            state = await cas_transition(
                conn,
                PROPOSAL,
                "proposals",
                row["id"],
                state,
                current["version"],
                event,
            )
        event = {
            Action.WAIT: IncidentEvent.WAIT_STARTED,
            Action.REQUEST_OWNER: IncidentEvent.OWNER_NEEDED,
        }.get(proposal.action)
        if event is not None:
            await cas_transition(
                conn,
                INCIDENT,
                "incidents",
                incident["id"],
                incident["state"],
                incident["version"],
                event,
            )
        # A stored draft is not a sent message; a recorded owner question
        # is not an approval. These local steps never create operations.
        await audit(
            actor="agent",
            kind="STATE_TRANSITION",
            proposal_id=row["id"],
            details={
                "action": str(proposal.action),
                "recorded_only": True,
                "delivered": False,
                "state": str(state),
            },
        )
    if decision.verdict is not Verdict.BLOCK and proposal.action in PAYING:
        run_id = row["params"].get("run_id")
        own_cash = [
            r
            for r in snap.reservations
            if r.state == "ACTIVE"
            and r.kind in {"PROPOSAL_CASH", "RUN_CASH"}
            and run_id is not None
            and r.run_id == run_id
        ]
        if not own_cash:
            amount = sum(
                (item.amount for item in snap.payouts), Money.zero(snap.cash.unreserved.currency)
            )
            if amount.minor > 0:
                await create_reservation(
                    conn,
                    "PROPOSAL_CASH",
                    amount,
                    proposal_id=row["id"],
                    run_id=UUID(run_id) if run_id else None,
                )
    current_incident = await repos.incidents.get(incident["id"])
    raw["agent_context"] = {"incident_version": current_incident["version"]}
    await repos.proposals.update(row["id"], raw_output=raw)
    await audit(
        actor="agent",
        kind="PROPOSAL_CREATED",
        proposal_id=row["id"],
        details={
            "incident_id": str(incident["id"]),
            "action": document["action"],
            "model": model_id,
        },
    )
    await audit(
        actor="shield",
        kind="SHIELD_VERDICT",
        proposal_id=row["id"],
        verdict=str(decision.verdict),
        details={"rule_results": decision.rule_results(), "at_execution": False},
    )
    if any(flag["type"] == "PROMPT_INJECTION" for flag in proposed.flags):
        await audit(
            actor="agent",
            kind="INJECTION_FLAGGED",
            proposal_id=row["id"],
            details={"types": sorted({flag["type"] for flag in proposed.flags})},
        )
    return {
        "id": str(row["id"]),
        "verdict": str(decision.verdict),
        "rule_results": decision.rule_results(),
    }
