"""Stable engine facade for evaluation and the demo bar (CONTRACTS section 4).

The facade composes the existing owner, inbox, reconciliation and executor services.
It never imports the write adapter, loads environment files or starts background work
on import. Scenario seeding imports confirmed historical facts; it sends no writes.
"""

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from countersign.approvals import ApprovalError, ApprovalService
from countersign.config import PayPalSettings
from countersign.db import Database, Repositories, cas_transition
from countersign.db.ids import uuid7
from countersign.db.repositories import TABLES
from countersign.executor import Executor, ExecutorError
from countersign.fsm.attempt import PAYPAL_STATUSES
from countersign.fsm.obligation import OBLIGATION, ObligationEvent
from countersign.inbox import Inbox, InboxError
from countersign.ledger import journal
from countersign.money import Money
from countersign.paypal.http import PayPalHTTP, canonical_payload_hash, resource_id
from countersign.paypal.read import PayPalRead
from countersign.paypal.webhook_verify import WebhookVerifier
from countersign.reconcile import Reconciler
from countersign.reconcile.attempts import reconcile_attempts
from countersign.reconcile.balances import reconcile_balances
from countersign.reconcile.disputes import ingest_dispute
from countersign.worker import worker_once


class EngineError(RuntimeError):
    """A fixed, value-free error at the public engine boundary."""


@dataclass(frozen=True, repr=False)
class EngineSettings:
    database_url: str = field(repr=False)
    paypal_base_url: str = field(repr=False)
    paypal_client_id: str = field(repr=False)
    paypal_client_secret: str = field(repr=False)
    webhook_id: str = field(repr=False)
    clock: Callable[[], datetime] = field(repr=False)
    llm: object | None = field(default=None, repr=False)

    def __post_init__(self):
        try:
            configured = conninfo_to_dict(self.database_url)
            if not configured.get("dbname") or not callable(self.clock):
                raise ValueError
            PayPalSettings(
                self.paypal_base_url,
                self.paypal_client_id,
                self.paypal_client_secret,
                self.webhook_id,
            )
        except Exception:  # noqa: BLE001 - never include a private DSN or credential in errors.
            raise EngineError("INVALID_ENGINE_SETTINGS") from None
        if self.llm is not None:
            raise EngineError("AGENT_NOT_IMPLEMENTED")

    def __repr__(self):
        return "EngineSettings(values=<REDACTED>)"


def _time(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise EngineError("INVALID_CLOCK")
    return value.astimezone(UTC)


def _identifier(value):
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise EngineError("INVALID_IDENTIFIER") from None


def _json(value):
    """Make datetimes/UUIDs JSON-compatible without exposing arbitrary object reprs."""
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _time(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    return value


class Engine:
    """Use as an async context manager or call aclose() after its last method.

    Injected dependencies belong to their caller. Internally created resources
    open lazily and close together. No method resets or seeds the PayPal account.
    """

    def __init__(
        self,
        settings: EngineSettings,
        *,
        database=None,
        http=None,
        transport=None,
        reader=None,
        verifier=None,
        executor=None,
    ):
        self.settings = settings
        self._database, self._http = database, http
        self._transport, self._reader, self._verifier = transport, reader, verifier
        self._executor = executor
        self._stack = AsyncExitStack()
        self._ready = False
        self._closed = False
        self._open_lock = asyncio.Lock()

    def __repr__(self):
        return "Engine(values=<REDACTED>)"

    @property
    def database(self):
        """The active database dependency; ownership remains with its creator."""
        return self._database

    async def __aenter__(self):
        async with self._public():
            return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def aclose(self):
        async with self._open_lock:
            self._closed = True
            self._ready = False
            try:
                await self._stack.aclose()
            except Exception:  # noqa: BLE001 - owned driver cleanup may carry connection values.
                raise EngineError("ENGINE_CLOSE_FAILED") from None

    async def _open(self):
        async with self._open_lock:
            if self._closed:
                raise EngineError("ENGINE_CLOSED")
            if self._ready:
                return
            try:
                if self._database is None:
                    self._database = await self._stack.enter_async_context(
                        Database(self.settings.database_url)
                    )
                if self._http is None and (
                    self._reader is None or self._verifier is None or self._executor is None
                ):
                    paypal = PayPalSettings(
                        self.settings.paypal_base_url,
                        self.settings.paypal_client_id,
                        self.settings.paypal_client_secret,
                        self.settings.webhook_id,
                    )
                    self._http = await self._stack.enter_async_context(
                        PayPalHTTP(paypal, transport=self._transport)
                    )
                self._reader = self._reader if self._reader is not None else PayPalRead(self._http)
                self._verifier = (
                    self._verifier
                    if self._verifier is not None
                    else WebhookVerifier(self._http, self.settings.webhook_id)
                )
                if self._executor is None:
                    factory = getattr(Executor, "from_http", None)
                    if factory is None:
                        raise EngineError("EXECUTOR_FACTORY_UNAVAILABLE")
                    self._executor = factory(self._database, self._http, clock=self.settings.clock)
                self._inbox = Inbox(self._database, self._reader, self._verifier)
                self._reconciler = Reconciler(
                    self._database, self._reader, clock=self.settings.clock
                )
                self._approvals = ApprovalService(self._database, clock=self.settings.clock)
                self._ready = True
            except Exception:
                await self._stack.aclose()
                self._closed = True
                raise

    @asynccontextmanager
    async def _public(self):
        try:
            await self._open()
            yield
        except EngineError:
            raise
        except ApprovalError as exc:
            raise EngineError(exc.code) from None
        except ExecutorError as exc:
            code = exc.args[0] if exc.args else "EXECUTOR_REFUSED"
            if not isinstance(code, str) or not re.fullmatch(r"[A-Z_]+", code):
                code = "EXECUTOR_REFUSED"
            raise EngineError(code) from None
        except Exception:  # noqa: BLE001 - private transport and database values stop at this boundary.
            raise EngineError("ENGINE_OPERATION_FAILED") from None

    async def reset(self) -> None:
        """Empty only fixed application tables in a verified _test or _eval database."""
        try:
            configured = conninfo_to_dict(self.settings.database_url).get("dbname", "")
        except Exception:  # noqa: BLE001 - bad DSNs must not become exception text.
            raise EngineError("RESET_DATABASE_REFUSED") from None
        if not configured.endswith(("_test", "_eval")):
            raise EngineError("RESET_DATABASE_REFUSED")
        async with self._public(), self._database.tx() as conn:
            actual = conn.info.dbname
            if actual != configured or not actual.endswith(("_test", "_eval")):
                raise EngineError("RESET_DATABASE_REFUSED")
            await journal.serialize_on(conn, "countersign:owner-decisions")
            cursor = await conn.execute("SELECT current_schema() AS schema")
            schema = (await cursor.fetchone())["schema"]
            if not schema or schema in {"pg_catalog", "information_schema"}:
                raise EngineError("RESET_SCHEMA_REFUSED")
            tables = sql.SQL(", ").join(sql.Identifier(schema, table) for table in TABLES)
            # No CASCADE: an external dependency refuses reset instead of being deleted.
            await conn.execute(sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY").format(tables))

    async def deliver_webhook(self, headers: dict[str, str], raw_body: bytes) -> int:
        try:
            async with self._public():
                try:
                    await self._inbox.receive(raw_body, headers)
                except InboxError:
                    return 400
            return 200
        except EngineError:
            return 503

    async def tick(self) -> dict:
        async with self._public():
            return await worker_once(self._database, self._inbox, self._reconciler, self._executor)

    async def plan_payout_run(self, week_start: date) -> str:
        if type(week_start) is not date:
            raise EngineError("INVALID_WEEK_START")
        async with self._public():
            return str((await self._approvals.plan_run(week_start))["id"])

    async def pending_approvals(self) -> list[dict]:
        async with self._public(), self._database.tx() as conn:
            cursor = await conn.execute(
                "SELECT doc FROM v_dash_proposals WHERE state='NEEDS_OWNER' "
                "ORDER BY created_at DESC,id DESC"
            )
            return [row["doc"] for row in await cursor.fetchall()]

    async def approve(
        self, proposal_id: str, payload_hash: str, approved_by: str = "eval-owner"
    ) -> dict:
        async with self._public():
            return _json(
                await self._approvals.approve(_identifier(proposal_id), payload_hash, approved_by)
            )

    async def reject(self, proposal_id: str, reason: str) -> dict:
        async with self._public():
            identifier = _identifier(proposal_id)
            await self._approvals.reject(identifier, reason)
            async with self._database.tx() as conn:
                cursor = await conn.execute(
                    "SELECT doc FROM v_dash_proposals WHERE id=%s", (identifier,)
                )
                return (await cursor.fetchone())["doc"]

    async def snapshot(self) -> dict:
        async with self._public(), self._database.tx() as conn:
            await journal.serialize_on(conn, "countersign:owner-decisions")
            result = {}
            for name, view in (
                ("incidents", "v_dash_incidents"),
                ("proposals", "v_dash_proposals"),
                ("runs", "v_dash_runs"),
                ("makers", "v_dash_makers"),
                ("disputes", "v_dash_disputes"),
                ("reservations", "v_dash_reservations"),
            ):
                cursor = await conn.execute(
                    sql.SQL("SELECT doc FROM {} ORDER BY id").format(sql.Identifier(view))
                )
                result[name] = [row["doc"] for row in await cursor.fetchall()]
            # Historical imports have no proposal or invented owner approval. Their
            # confirmed operation timestamp supplies the run's public creation time.
            for run in result["runs"]:
                if run.get("created_at") is None:
                    cursor = await conn.execute(
                        "SELECT min(o.created_at) AS created_at FROM operations o "
                        "JOIN payout_lines l ON l.id=o.target_id WHERE l.run_id=%s "
                        "AND o.payload->>'historical_import'='true'",
                        (UUID(run["id"]),),
                    )
                    observed = (await cursor.fetchone())["created_at"]
                    if observed is not None:
                        run["created_at"] = _json(observed)
            cursor = await conn.execute("SELECT doc FROM v_dash_cash")
            cash = await cursor.fetchone()
            cursor = await conn.execute(
                "SELECT * FROM balance_snapshots WHERE currency='USD' "
                "ORDER BY fetched_at DESC,id DESC LIMIT 1"
            )
            latest = await cursor.fetchone()
            available = latest is not None and all(
                latest[key] is not None
                for key in (
                    "available_minor",
                    "withheld_minor",
                    "total_minor",
                    "last_refresh_time",
                    "as_of_time",
                )
            )
            cursor = await conn.execute(
                "SELECT 1 FROM policies WHERE starts_with(key,'reconcile.refund:') "
                "AND value->>'pending'='true' AND value->>'currency'='USD' LIMIT 1"
            )
            blocked = await cursor.fetchone() is not None
            result["cash"] = cash["doc"] if cash and available and not blocked else None
            cursor = await conn.execute(
                "SELECT dash_redact(jsonb_build_object('id',id,'kind',kind,'state',state,"
                "'paypal_refs',jsonb_strip_nulls(jsonb_build_object('payout_batch_id',"
                "coalesce(response->>'payout_batch_id',response#>>'{batch_header,payout_batch_id}'),"
                "'payout_item_id',response->>'payout_item_id','debug_id',paypal_debug_id)))) AS doc "
                "FROM operations ORDER BY created_at,id"
            )
            result["operations"] = [row["doc"] for row in await cursor.fetchall()]
            return result

    async def seed(self, state: dict) -> None:
        """Import scenario facts atomically into an empty marketplace; never overwrite payees."""
        async with self._public():
            if not isinstance(state, dict) or "paypal" in state:
                raise EngineError("INVALID_SEED_STATE")
            digest = hashlib.sha256(
                json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            ).hexdigest()
            now = _time(self.settings.clock())
            async with self._database.tx() as conn:
                await journal.serialize_on(conn, "countersign:owner-decisions")
                repos = Repositories(conn)
                marker = await repos.policies.get("engine.seed")
                if marker:
                    if marker["value"]["hash"] != digest:
                        raise EngineError("SEED_STATE_CONFLICT")
                    return
                for table in TABLES:
                    if table != "webhook_inbox" and await repos[table].find(limit=1):
                        raise EngineError("SEED_REQUIRES_EMPTY_MARKETPLACE")
                makers, obligations = await self._seed_marketplace(conn, state, now)
                for prior in state.get("prior_payouts", []):
                    await self._seed_prior(conn, prior, makers, obligations, now)
                for dispute in state.get("disputes", []):
                    identifier = resource_id(
                        dispute.get("paypal_dispute_id")
                        or dispute.get("dispute_id")
                        or dispute.get("id")
                    )
                    response = await self._reader.get_dispute(identifier)
                    if (
                        not 200 <= response.status_code < 300
                        or response.body.get("dispute_id") != identifier
                    ):
                        raise EngineError("SEED_DISPUTE_UNCONFIRMED")
                    result = await ingest_dispute(
                        conn, response.body, now=_time(self.settings.clock())
                    )
                    if result.errors or result.skipped:
                        raise EngineError("SEED_DISPUTE_UNMATCHED")
                    if dispute.get("order") is not None:
                        expected = obligations[dispute["order"]]["order_id"]
                        current = await repos.disputes.find({"paypal_dispute_id": identifier})
                        if not current or current[0]["order_id"] != expected:
                            raise EngineError("SEED_DISPUTE_ORDER_MISMATCH")
                await reconcile_balances(conn, self._reader, now=now, clock=self.settings.clock)
                await repos.policies.insert(key="engine.seed", value={"hash": digest})

    async def _seed_marketplace(self, conn, state, now):
        repos = Repositories(conn)
        policy = state.get("policy", {})
        share = policy.get("maker_share_pct", 90)
        if type(share) is not int or not 0 < share <= 100:
            raise EngineError("INVALID_SEED_POLICY")
        for key, value in {"maker_share_pct": share, **policy}.items():
            await repos.policies.insert(key=key, value=value)
        due = now.date() + timedelta(days=(4 - now.weekday()) % 7)
        makers, obligations = {}, {}
        for item in state.get("makers", []):
            key = item["key"]
            if key in makers:
                raise EngineError("DUPLICATE_SEED_MAKER")
            makers[key] = await repos.makers.insert(
                display_name=item["display_name"],
                payout_email=item.get("payout_email"),
                payout_email_verified_at=now if item.get("verified") else None,
            )
        for item in state.get("orders", []):
            key = item["key"]
            if key in obligations:
                raise EngineError("DUPLICATE_SEED_ORDER")
            maker = makers[item["maker"]]
            amount = Money(**item["amount"])
            captured = item.get("captured_at")
            captured = _time(datetime.fromisoformat(captured)) if captured is not None else None
            order = await repos.orders.insert(
                maker_id=maker["id"],
                buyer_ref=key,
                amount_minor=amount.minor,
                currency=amount.currency,
                paypal_capture_id=item.get("capture_id"),
                paypal_order_id=item.get("paypal_order_id"),
                captured_at=captured,
                tracking=item.get("tracking"),
            )
            owed = amount.share_pct(share)
            obligations[key] = await repos.obligations.insert(
                maker_id=maker["id"],
                order_id=order["id"],
                kind="ORDER_SHARE",
                amount_minor=owed.minor,
                currency=owed.currency,
                due_date=due,
                state="OPEN",
            )
        return makers, obligations

    async def _seed_prior(self, conn, prior, makers, obligations, now):
        """Bootstrap a GET-confirmed historical payout, then reuse C15 for its financial state."""
        repos = Repositories(conn)
        maker = makers[prior["maker"]]
        amount = Money(**prior["amount"])
        if amount.minor <= 0 or prior.get("status") not in PAYPAL_STATUSES:
            raise EngineError("INVALID_SEED_PAYOUT")
        keys = prior.get("order_keys")
        if keys is None:
            if any(row["maker_id"] == maker["id"] for row in obligations.values()):
                raise EngineError("SEED_PRIOR_LINK_REQUIRED")
            keys = []
        if not isinstance(keys, list) or len(keys) != len(set(keys)):
            raise EngineError("INVALID_SEED_PAYOUT_LINKS")
        shares = [obligations[key] for key in keys]
        if shares and (
            any(
                row["maker_id"] != maker["id"] or row["currency"].strip() != amount.currency
                for row in shares
            )
            or sum(row["amount_minor"] for row in shares) != amount.minor
        ):
            raise EngineError("SEED_PAYOUT_AMOUNT_MISMATCH")
        item_id = resource_id(prior["payout_item_id"])
        response = await self._reader.get_payout_item(item_id)
        if not 200 <= response.status_code < 300 or not isinstance(response.body, dict):
            raise EngineError("SEED_PAYOUT_UNCONFIRMED")
        detail = response.body
        item = detail.get("payout_item", {})
        if (
            detail.get("payout_item_id") != item_id
            or detail.get("transaction_status") != prior["status"]
            or Money.from_paypal(item.get("amount")) != amount
            or item.get("receiver") != maker["payout_email"]
        ):
            raise EngineError("SEED_PAYOUT_IDENTITY_MISMATCH")
        batch_id = resource_id(detail["payout_batch_id"])
        batch = await self._reader.get_payout_batch(batch_id)
        if not 200 <= batch.status_code < 300 or not isinstance(batch.body, dict):
            raise EngineError("SEED_PAYOUT_UNCONFIRMED")
        header = batch.body["batch_header"]
        sender = resource_id(header["sender_batch_header"]["sender_batch_id"])
        if header["payout_batch_id"] != batch_id:
            raise EngineError("SEED_PAYOUT_IDENTITY_MISMATCH")
        prepared = _time(datetime.fromisoformat(header["time_created"]))
        # Historical request IDs are not exposed by GET. This non-replayable import
        # marker is explicitly not claimed to be the original request identifier.
        request = resource_id(prior.get("request_id") or "import-" + str(uuid7()))
        body = {"sender_batch_header": header["sender_batch_header"], "items": [item]}
        payload = {
            "body": body,
            "request_id": request,
            "historical_import": True,
            "import_only": True,
            "original_request_id_known": "request_id" in prior,
        }
        week = prepared.date() - timedelta(days=prepared.weekday())
        runs = await repos.payout_runs.find({"week_start": week})
        run = runs[0] if runs else await repos.payout_runs.insert(week_start=week, state="DONE")
        line = await repos.payout_lines.insert(
            run_id=run["id"],
            maker_id=maker["id"],
            amount_minor=amount.minor,
            currency=amount.currency,
            state="PAYING",
        )
        if not shares:
            shares = [
                await repos.obligations.insert(
                    maker_id=maker["id"],
                    kind="ADJUSTMENT",
                    amount_minor=amount.minor,
                    currency=amount.currency,
                    due_date=week,
                    state="OPEN",
                )
            ]
        for obligation in shares:
            if obligation["state"] != "OPEN":
                raise EngineError("SEED_PAYOUT_LINK_CONFLICT")
            current = await repos.obligations.get(obligation["id"], for_update=True)
            if current["state"] != "OPEN":
                raise EngineError("SEED_PAYOUT_LINK_CONFLICT")
            await repos.payout_line_obligations.insert(
                line_id=line["id"],
                obligation_id=obligation["id"],
                amount_minor=obligation["amount_minor"],
            )
            scheduled = await cas_transition(
                conn,
                OBLIGATION,
                "obligations",
                current["id"],
                current["state"],
                current["version"],
                ObligationEvent.LINE_APPROVED,
            )
            await cas_transition(
                conn,
                OBLIGATION,
                "obligations",
                current["id"],
                scheduled,
                current["version"] + 1,
                ObligationEvent.ATTEMPT_PREPARED,
            )
        operation = await repos.operations.insert(
            kind="PAYOUT_CREATE",
            target_type="payout_line",
            target_id=line["id"],
            payload=payload,
            payload_hash=bytes.fromhex(canonical_payload_hash(payload)[7:]),
            state="SUCCEEDED",
            response={"batch_header": {"payout_batch_id": batch_id}},
            created_at=prepared,
            submitted_at=prepared,
            completed_at=None,  # GET does not expose the original create response time.
        )
        await repos.payout_attempts.insert(
            line_id=line["id"],
            attempt_no=1,
            operation_id=operation["id"],
            sender_batch_id=sender,
            request_id=request,
            sender_item_id=resource_id(item["sender_item_id"]),
            payee_email=maker["payout_email"],
            amount_minor=amount.minor,
            currency=amount.currency,
            state="SUBMITTED",
            paypal_batch_id=batch_id,
            paypal_item_id=item_id,
            prepared_at=prepared,
        )
        await journal.post_journal(
            conn,
            journal.payout_submitted(amount),
            operation_id=operation["id"],
            occurred_at=prepared,
        )
        result = await reconcile_attempts(conn, self._reader, now=now)
        if result.errors:
            raise EngineError("SEED_PAYOUT_RECONCILIATION_FAILED")
