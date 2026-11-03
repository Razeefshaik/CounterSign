"""Gateway policy, independent of MCP transport. The shield remains authoritative."""

import asyncio
import os
import re
from datetime import UTC, date, datetime
from uuid import UUID

from psycopg import sql

from countersign.agent.params import ACTION_PARAMS, ProposalError
from countersign.agent.proposal import StructuredProposal
from countersign.agent.safety import SafetyError, injection_flags, sanitize
from countersign.api.playground import _staged_now, playground_allowed
from countersign.approvals import ApprovalError, ApprovalService
from countersign.approvals.binding import bind_agent_proposal
from countersign.db import Repositories
from countersign.db.ids import uuid7
from countersign.ledger.journal import serialize_on

ALLOWED_TOOLS = frozenset(
    {
        "list_incidents",
        "get_incident",
        "list_makers",
        "get_cash",
        "get_proposal",
        "list_actions",
        "propose",
        "propose_refund",
        "propose_payout_run",
        "propose_cancel_unclaimed",
        "propose_evidence",
    }
)
_ZERO = "00000000-0000-0000-0000-000000000000"
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class GatewayError(ValueError):
    """A fixed public code, never dependency or caller text."""


def gateway_mode(*, hosted=False):
    if hosted:
        return "preview"
    mode = os.environ.get("COUNTERSIGN_GATEWAY_MODE", "preview")
    if mode not in {"preview", "record"}:
        raise GatewayError("GATEWAY_MODE_INVALID")
    if mode == "record" and any(
        os.environ.get(key)
        for key in (
            "COUNTERSIGN_HOSTED_STATE",
            "COUNTERSIGN_DEMO_ROOT",
            "RENDER",
            "K_SERVICE",
        )
    ):
        raise GatewayError("GATEWAY_RECORD_HOSTED_REFUSED")
    return mode


def client_label(name):
    # Initialize metadata is attribution, never authentication or authority.
    cleaned = sanitize(name if isinstance(name, str) else "unknown")
    return re.sub(r"[^A-Za-z0-9_.-]", "_", cleaned)[:64] or "unknown"


def identifier(value):
    if not isinstance(value, str):
        raise GatewayError("INVALID_REQUEST")
    try:
        return UUID(value)
    except ValueError:
        raise GatewayError("INVALID_REQUEST") from None


class Gateway:
    def __init__(self, database, settings, *, mode="preview", hosted=False, clock=None):
        self.database, self.settings = database, settings
        self.mode = "preview" if hosted else mode
        if self.mode not in {"preview", "record"}:
            raise GatewayError("GATEWAY_MODE_INVALID")
        self.clock = clock
        self.serial = asyncio.Lock()

    async def now(self):
        if self.clock is not None:
            return self.clock()
        if playground_allowed(self.settings):
            return await _staged_now(self.settings)
        return datetime.now(UTC)

    async def read(self, resource, *, id=None, limit=50, state=None, kind=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise GatewayError("INVALID_REQUEST")
        views = {
            "incidents": "v_dash_incidents",
            "makers": "v_dash_makers",
            "cash": "v_dash_cash",
            "proposals": "v_dash_proposals",
        }
        if resource not in views:
            raise GatewayError("INVALID_REQUEST")
        conditions, params = [], []
        if id is not None:
            conditions.append(sql.SQL("id=%s"))
            params.append(identifier(id))
        if state is not None:
            if state not in {
                "OPEN",
                "INVESTIGATING",
                "AWAITING_OWNER",
                "EXECUTING",
                "WAITING_EXTERNAL",
                "RESOLVED",
            }:
                raise GatewayError("INVALID_REQUEST")
            conditions.append(sql.SQL("state=%s"))
            params.append(state)
        if kind is not None:
            if kind not in {"DISPUTE", "PAYOUT_ITEM", "RUN_AT_RISK", "RECONCILIATION"}:
                raise GatewayError("INVALID_REQUEST")
            conditions.append(sql.SQL("kind=%s"))
            params.append(kind)
        condition = (
            sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions) if conditions else sql.SQL("")
        )
        async with self.database.tx() as conn:
            await conn.execute("SET TRANSACTION READ ONLY")
            await conn.execute("SET LOCAL ROLE countersign_agent_ro")
            result = await conn.execute(
                sql.SQL("SELECT doc FROM {}{} ORDER BY doc->>'id' LIMIT %s").format(
                    sql.Identifier(views[resource]), condition
                ),
                (*params, limit),
            )
            rows = [row["doc"] for row in await result.fetchall()]
        if resource == "proposals":
            # Status reads do not replay an untrusted agent's rationale or excerpts.
            fields = {
                "id",
                "incident_id",
                "action",
                "state",
                "verdict",
                "rule_results",
                "expected_effect",
                "created_at",
            }
            rows = [
                {
                    **{key: row[key] for key in fields if key in row},
                    "flags": [
                        {"type": flag["type"], "source": flag["source"]}
                        for flag in row.get("flags", [])
                    ],
                }
                for row in rows
            ]
        return sanitize({"items": rows})

    async def target(self, action, params):
        """Resolve convenience arguments from authoritative records, never caller SQL."""
        async with self.database.tx() as conn:
            await conn.execute("SET TRANSACTION READ ONLY")
            if action in {"ACCEPT_CLAIM", "PROVIDE_EVIDENCE"}:
                key = params.get("paypal_dispute_id")
                if not isinstance(key, str) or not _ID.fullmatch(key):
                    raise GatewayError("INVALID_REQUEST")
                result = await conn.execute(
                    "SELECT id FROM disputes WHERE paypal_dispute_id=%s", (key,)
                )
                row = await result.fetchone()
                if row is None:
                    raise GatewayError("TARGET_NOT_FOUND")
                return {"dispute_id": str(row["id"]), "paypal_dispute_id": key}
            if action == "CANCEL_UNCLAIMED_PAYOUT":
                key = params.get("payout_item_id")
                if not isinstance(key, str) or not _ID.fullmatch(key):
                    raise GatewayError("INVALID_REQUEST")
                result = await conn.execute(
                    "SELECT id FROM payout_attempts WHERE paypal_item_id=%s", (key,)
                )
                row = await result.fetchone()
                if row is None:
                    raise GatewayError("TARGET_NOT_FOUND")
                return {"attempt_id": str(row["id"]), "payout_item_id": key}
        return {}

    async def planned_run(self, week_start):
        try:
            if type(week_start) is not str:
                raise ValueError
            week = date.fromisoformat(week_start)
        except ValueError:
            raise GatewayError("INVALID_REQUEST") from None
        async with self.database.tx() as conn:
            await conn.execute("SET TRANSACTION READ ONLY")
            result = await conn.execute(
                "SELECT doc FROM v_dash_run_detail WHERE doc->>'week_start'=%s "
                "AND doc->>'state'<>'CANCELLED' ORDER BY doc->>'id' DESC LIMIT 1",
                (week.isoformat(),),
            )
            row = await result.fetchone()
        if row is None:
            raise GatewayError("RUN_NOT_FOUND")
        run = row["doc"]
        return {
            "run_id": run["id"],
            "lines": [
                {"line_id": line["id"], "maker_id": line["maker"]["id"], "amount": line["amount"]}
                for line in run["lines"]
            ],
            "replacements": [
                {
                    "line_id": line["line_id"],
                    "maker_id": line["maker"]["id"],
                    "amount": line["amount"],
                    "attempt_no": line["attempt_no"],
                }
                for line in run["replacements"]
            ],
        }

    async def effect(self, action, params):
        """Describe the observed full amount; this field grants no spending authority."""
        query = None
        if action == "ACCEPT_CLAIM":
            query = (
                "SELECT -amount_minor AS minor,currency FROM disputes WHERE paypal_dispute_id=%s",
                params["paypal_dispute_id"],
            )
        elif action == "CANCEL_UNCLAIMED_PAYOUT":
            query = (
                "SELECT amount_minor AS minor,currency FROM payout_attempts WHERE paypal_item_id=%s",
                params["payout_item_id"],
            )
        elif action == "CREATE_REPLACEMENT_PAYOUT":
            query = (
                "SELECT -amount_minor AS minor,currency FROM payout_lines WHERE id=%s",
                params["line_id"],
            )
        elif action == "SCHEDULE_PAYOUT_RUN":
            query = (
                (
                    "SELECT -(doc->'total'->>'minor')::bigint AS minor, "
                    "doc->'total'->>'currency' AS currency FROM v_dash_run_detail WHERE id=%s"
                ),
                params["run_id"],
            )
        if query is not None:
            async with self.database.tx() as conn:
                await conn.execute("SET TRANSACTION READ ONLY")
                row = await (await conn.execute(query[0], (query[1],))).fetchone()
            if row is not None and row["minor"] is not None:
                return {
                    "available_change": {"minor": row["minor"], "currency": row["currency"].strip()}
                }
        return {"available_change": {"minor": 0, "currency": "USD"}}

    async def propose(
        self, incident_id, action, params, rationale, evidence_refs, *, client="unknown"
    ):
        # Bound and validate the original JSON before any query or persistent write.
        document = {
            "incident_id": incident_id or _ZERO,
            "action": action,
            "params": params,
            "rationale": rationale,
            "evidence_refs": evidence_refs,
            "expected_effect": {"available_change": {"minor": 0, "currency": "USD"}},
        }
        try:
            flags = injection_flags(document, source="external_agent")
            flags = tuple({**flag, "source": "external_agent"} for flag in flags)
            proposed = StructuredProposal.parse(
                document, incident_id=document["incident_id"], flags=flags
            )
            if incident_id is not None:
                identifier(incident_id)
            # A queueing limit, not an unbounded wait for public requests.
            if self.serial.locked():
                raise GatewayError("GATEWAY_BUSY")
            async with self.serial:
                now = await self.now()
                document["expected_effect"] = await self.effect(action, proposed.params)
                proposed = StructuredProposal.parse(
                    document, incident_id=document["incident_id"], flags=flags
                )
                service = ApprovalService(
                    self.database, clock=lambda: now, behavior=self.settings.paypal.behavior
                )
                if self.mode == "preview":
                    result = await service.preview(proposed.action, proposed.params, proposed.flags)
                    return {
                        **sanitize(result),
                        "needs_owner": result["verdict"] == "NEEDS_OWNER",
                        "decision_url": None,
                        "written": False,
                    }
                async with self.database.tx() as conn:
                    await serialize_on(conn, "countersign:owner-decisions")
                    repos = Repositories(conn)
                    if incident_id is not None:
                        observed = await repos.incidents.get(
                            identifier(incident_id), for_update=True
                        )
                        if observed is None:
                            raise GatewayError("INCIDENT_NOT_FOUND")
                    else:
                        # External requests have their own incident; no internal incident is reopened.
                        target = await self.target(action, params)
                        observed = await repos.incidents.insert(
                            kind="RECONCILIATION",
                            subject={
                                "id": str(uuid7()),
                                "reason": "EXTERNAL_AGENT_PROPOSAL",
                                **target,
                            },
                            state="OPEN",
                            opened_at=now,
                        )
                        document["incident_id"] = str(observed["id"])
                        proposed = StructuredProposal.parse(
                            document,
                            incident_id=document["incident_id"],
                            flags=flags,
                        )
                    result = await bind_agent_proposal(
                        conn,
                        observed,
                        proposed,
                        model_id="external-agent:" + client_label(client),
                        clock=lambda: now,
                        behavior=self.settings.paypal.behavior,
                        lifetime=service.lifetime,
                        source="external_agent",
                    )
                    if result is None:
                        raise GatewayError("INCIDENT_NOT_READY")
                return {
                    "verdict": result["verdict"],
                    "rule_results": sanitize(result["rule_results"]),
                    "needs_owner": result["verdict"] == "NEEDS_OWNER",
                    "decision_url": "#decision/" + result["id"],
                    "written": True,
                }
        except (ProposalError, SafetyError, RecursionError, TypeError, KeyError):
            raise GatewayError("INVALID_PROPOSAL") from None
        except ApprovalError:
            raise GatewayError("PROPOSAL_UNAVAILABLE") from None


def list_actions():
    return {
        "items": [
            {
                "action": str(action),
                "required": list(spec.required),
                "optional": list(spec.optional),
                "description": spec.description,
            }
            for action, spec in ACTION_PARAMS.items()
        ]
    }
