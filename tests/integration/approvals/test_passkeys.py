"""C51: real P-256 software authenticator vectors, isolated PostgreSQL, no providers."""

import asyncio
import base64
import hashlib
import json
import secrets
from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID

import cbor2
import httpx
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from psycopg.types.json import Jsonb

from countersign.api.owner import build_router
from countersign.approvals import ApprovalService
from countersign.approvals.passkeys import PasskeyError, Passkeys
from countersign.config import PayPalSettings, Settings
from countersign.db import Database, Repositories
from countersign.executor import Executor

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
RP, ORIGIN = "localhost", "http://localhost:8194"


def encode(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class Authenticator:
    """Minimal WebAuthn none-attestation authenticator, with real ECDSA signatures."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.id = secrets.token_bytes(32)

    def response(self, challenge, *, register=False, count=1, rp=RP, origin=ORIGIN, flags=5,
                 cross_origin=False):
        client = json.dumps({"type": "webauthn.create" if register else "webauthn.get",
                             "challenge": challenge, "origin": origin,
                             "crossOrigin": cross_origin}).encode()
        auth = hashlib.sha256(rp.encode()).digest() + bytes([flags | (64 if register else 0)])
        auth += (0 if register else count).to_bytes(4, "big")
        response = {"clientDataJSON": encode(client)}
        if register:
            numbers = self.key.public_key().public_numbers()
            cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"),
                                -3: numbers.y.to_bytes(32, "big")})
            auth += bytes(16) + len(self.id).to_bytes(2, "big") + self.id + cose
            response["attestationObject"] = encode(cbor2.dumps({
                "fmt": "none", "attStmt": {}, "authData": auth}))
            response["transports"] = ["internal"]
        else:
            response.update(authenticatorData=encode(auth), signature=encode(self.key.sign(
                auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))))
        return {"id": encode(self.id), "rawId": encode(self.id), "type": "public-key",
                "response": response}


@pytest_asyncio.fixture
async def world(test_db_url):
    settings = Settings("postgresql://fake:fake@localhost/fake", passkey_rp_id=RP, passkey_origin=ORIGIN)
    async with Database(test_db_url) as database:
        now = datetime.now(UTC)
        async with database.tx() as conn:
            repo = Repositories(conn)
            maker = await repo.makers.insert(display_name="Test maker",
                payout_email="maker@example.test", payout_email_verified_at=now)
            await repo.obligations.insert(maker_id=maker["id"], kind="ADJUSTMENT",
                amount_minor=900, currency="USD", due_date=date(2026, 11, 2), state="OPEN")
            await repo.balance_snapshots.insert(fetched_at=now, last_refresh_time=now,
                currency="USD", available_minor=10000, withheld_minor=0, raw={})
        run = await ApprovalService(database).plan_run(date(2026, 11, 2))
        async with database.tx() as conn:
            proposal = await Repositories(conn).proposals.get(run["proposal_id"])
        app = FastAPI()
        app.include_router(build_router(database, settings), prefix="/api/v1")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
            yield database, Passkeys(database, settings), proposal, client


def payload(proposal):
    return proposal["verdict_details"]["approval_request"]["payload_hash"]


async def registered(service):
    auth = Authenticator()
    options = await service.register_options(RP, ORIGIN)
    await service.register(UUID(options["challenge_id"]),
        auth.response(options["publicKey"]["challenge"], register=True), "Test device", RP, ORIGIN)
    return auth


async def challenge(service, proposal):
    options = await service.options(proposal["id"], payload(proposal), RP, ORIGIN)
    return UUID(options["challenge_id"]), options["publicKey"]["challenge"]


async def test_success_uses_existing_approval_and_is_atomic_single_use(world):
    db, service, proposal, client = world
    auth = await registered(service)
    identifier, nonce = await challenge(service, proposal)
    result = await client.post(f"/api/v1/owner/proposals/{proposal['id']}/passkey/approve", json={
        "payload_hash": payload(proposal), "challenge_id": str(identifier),
        "credential": auth.response(nonce)})
    assert result.status_code == 201, result.text
    approval = result.json()["approval"]
    assert approval["approved_by"].startswith("owner:passkey:") and approval["used_at"] is None
    with pytest.raises(PasskeyError, match="PASSKEY_CHALLENGE_USED"):
        await service.approve(proposal["id"], payload(proposal), identifier,
                              auth.response(nonce, count=2), RP, ORIGIN)
    async with db.tx() as conn:
        row = await Repositories(conn).proposals.get(proposal["id"])
        assert row["state"] == "APPROVED"
        approvals = await Repositories(conn).approvals.find({"proposal_id": proposal["id"]})
        assert len(approvals) == 1
        audit = await (await conn.execute("SELECT details FROM audit_log WHERE kind='OWNER_COUNTERSIGNED'" )).fetchone()
        assert audit["details"]["sign_count"] == 1 and audit["details"]["user_verified"] is True
        assert "public_key" not in audit["details"] and "assertion" not in audit["details"]
        key = await (await conn.execute("SELECT sign_count,transports FROM owner_passkeys")).fetchone()
        assert key == {"sign_count": 1, "transports": ["internal"]}


@pytest.mark.parametrize("wrong", ["proposal", "hash", "expired", "used", "origin", "rp", "count",
                                   "signature", "uv", "presence", "unknown_key", "cross_origin"])
async def test_bound_challenge_refuses_without_any_approval(world, wrong):
    db, service, proposal, _ = world
    auth = await registered(service)
    identifier, nonce = await challenge(service, proposal)
    target = proposal["id"]
    if wrong == "proposal":
        # An actual second eligible proposal: a missing-target check cannot mask
        # an absent challenge/proposal binding check.
        async with db.tx() as conn:
            other = await Repositories(conn).proposals.insert(**{
                key: proposal[key] for key in ("action", "params", "evidence_refs", "rationale",
                    "expected_effect", "model", "raw_output", "verdict", "verdict_rules",
                    "verdict_details", "state")
            })
            target = other["id"]
    credential = auth.response(nonce, origin="https://wrong.example" if wrong == "origin" else ORIGIN,
        rp="wrong.example" if wrong == "rp" else RP, count=0 if wrong == "count" else 1,
        flags=1 if wrong == "uv" else 4 if wrong == "presence" else 5,
        cross_origin=wrong == "cross_origin")
    if wrong == "signature":
        credential["response"]["signature"] = encode(bytes(64))
    if wrong == "unknown_key":
        credential = Authenticator().response(nonce)
    async with db.tx() as conn:
        if wrong == "hash":
            details = proposal["verdict_details"]
            details["approval_request"]["payload_hash"] = "sha256:" + "0" * 64
            await conn.execute("UPDATE proposals SET verdict_details=%s WHERE id=%s",
                               (Jsonb(details), proposal["id"]))
        if wrong == "expired":
            await conn.execute("UPDATE passkey_challenges SET expires_at=now()-interval '1 second'")
        if wrong == "used":
            await conn.execute("UPDATE passkey_challenges SET used_at=now() WHERE id=%s", (identifier,))
    expected = {"proposal": "PASSKEY_HASH_MISMATCH", "hash": "PASSKEY_HASH_MISMATCH",
                "expired": "PASSKEY_CHALLENGE_EXPIRED", "used": "PASSKEY_CHALLENGE_USED"}
    # The original hash remains in the client document even if the stored hash changed.
    original = "sha256:" + proposal["raw_output"]["approval_request"]["payload_hash"].split(":")[1]
    with pytest.raises(PasskeyError, match=expected.get(wrong, "PASSKEY_INVALID")):
        await service.approve(target, original, identifier, credential, RP, ORIGIN)
    async with db.tx() as conn:
        assert not await Repositories(conn).approvals.find({"proposal_id": proposal["id"]})
        assert (await (await conn.execute("SELECT sign_count FROM owner_passkeys")).fetchone())["sign_count"] == 0


async def test_existing_version_check_refuses_and_rolls_back_signature(world):
    db, service, proposal, _ = world
    auth = await registered(service)
    identifier, nonce = await challenge(service, proposal)
    async with db.tx() as conn:
        await conn.execute("UPDATE makers SET version=version+1")
    from countersign.approvals import ApprovalError
    with pytest.raises(ApprovalError, match="STALE_PROPOSAL"):
        await service.approve(proposal["id"], payload(proposal), identifier,
                              auth.response(nonce), RP, ORIGIN)
    async with db.tx() as conn:
        row = await (await conn.execute("SELECT used_at FROM passkey_challenges WHERE id=%s", (identifier,))).fetchone()
        assert row["used_at"] is None
        assert not await Repositories(conn).approvals.find({"proposal_id": proposal["id"]})


async def test_registration_endpoints_and_plain_approve(world):
    _, _, proposal, client = world
    options = (await client.post("/api/v1/owner/passkeys/register/options")).json()
    auth = Authenticator()
    body = {"challenge_id": options["challenge_id"], "credential": auth.response(
        options["publicKey"]["challenge"], register=True)}
    response = await client.post("/api/v1/owner/passkeys/register/verify", json=body)
    assert response.status_code == 201
    assert (await client.post("/api/v1/owner/passkeys/register/verify", json=body)).json()["error"]["code"] == "PASSKEY_CHALLENGE_USED"
    response = await client.post(f"/api/v1/proposals/{proposal['id']}/approve",
                                 json={"payload_hash": payload(proposal)})
    assert response.status_code == 201 and response.json()["approval"]["approved_by"] == "owner"


async def test_options_validate_current_state_and_hash(world):
    _, service, proposal, _ = world
    with pytest.raises(PasskeyError, match="PASSKEY_HASH_MISMATCH"):
        await service.options(proposal["id"], "sha256:" + "0" * 64, RP, ORIGIN)
    assert await service.options(proposal["id"], payload(proposal), RP, ORIGIN) == {"registered": False}


async def test_executor_still_rechecks_passkey_approval(world):
    db, service, proposal, _ = world
    auth = await registered(service)
    identifier, nonce = await challenge(service, proposal)
    await service.approve(proposal["id"], payload(proposal), identifier,
                          auth.response(nonce), RP, ORIGIN)
    async with db.tx() as conn:
        await conn.execute("UPDATE makers SET version=version+1")
    # No writer or reader methods: an attempted provider call would fail this test.
    writer = SimpleNamespace(http=SimpleNamespace(settings=PayPalSettings()))
    executor = Executor(db, writer, SimpleNamespace())
    result = await executor.execute(proposal["id"])
    assert result["proposal_state"] == "BLOCKED_AT_EXECUTION"
    async with db.tx() as conn:
        assert not await Repositories(conn).payout_attempts.find()


@pytest.mark.parametrize("purpose", ["register", "approve"])
async def test_live_guard_blocks_passkey_routes(world, purpose):
    db, _, proposal, _ = world
    settings = Settings("postgresql://fake:fake@localhost/fake", mode="live",
        paypal=PayPalSettings(base_url="https://api-m.sandbox.paypal.com"), owner_token="test-only",
        passkey_rp_id=RP, passkey_origin=ORIGIN)
    app = FastAPI()
    app.include_router(build_router(db, settings), prefix="/api/v1")
    path = "/api/v1/owner/passkeys/register/options" if purpose == "register" else (
        f"/api/v1/owner/proposals/{proposal['id']}/passkey/options")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
        for token in [None, "wrong"]:
            response = await client.post(path, headers={"X-Owner-Token": token} if token else {},
                                         json={"payload_hash": payload(proposal)})
            assert response.status_code == 401


async def test_registration_rejects_bad_origin_without_saving_key(world):
    db, service, _, _ = world
    options = await service.register_options(RP, ORIGIN)
    with pytest.raises(PasskeyError, match="PASSKEY_INVALID"):
        await service.register(UUID(options["challenge_id"]), Authenticator().response(
            options["publicKey"]["challenge"], register=True, origin="https://wrong.example"),
            "Test", RP, ORIGIN)
    async with db.tx() as conn:
        assert not await (await conn.execute("SELECT * FROM owner_passkeys")).fetchall()


async def test_concurrent_assertions_approve_once(world):
    db, service, proposal, _ = world
    auth = await registered(service)
    identifier, nonce = await challenge(service, proposal)
    assertion = auth.response(nonce)
    results = await asyncio.gather(*[
        service.approve(proposal["id"], payload(proposal), identifier, assertion, RP, ORIGIN)
        for _ in range(2)
    ], return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, PasskeyError) and r.code == "PASSKEY_CHALLENGE_USED" for r in results) == 1
    async with db.tx() as conn:
        assert len(await Repositories(conn).approvals.find({"proposal_id": proposal["id"]})) == 1


async def test_host_defaults_are_hosted_only_and_reject_cross_origin(world):
    from starlette.requests import Request

    from countersign.api.passkeys import relying_party
    settings = Settings("postgresql://fake:fake@localhost/fake")
    app = FastAPI()
    scope = {"type": "http", "scheme": "http", "server": ("test", 80), "path": "/",
             "headers": [(b"host", b"demo.example"), (b"x-forwarded-proto", b"https")], "app": app}
    with pytest.raises(PasskeyError):
        relying_party(Request(scope), settings)
    app.state.passkey_hosted_demo = True
    assert relying_party(Request(scope), settings) == ("demo.example", "https://demo.example")
    scope["headers"].append((b"origin", b"https://wrong.example"))
    with pytest.raises(PasskeyError):
        relying_party(Request(scope), settings)


async def test_migration_downgrade_and_reupgrade(schema_factory):
    database = schema_factory()
    with database.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM owner_passkeys").fetchone()["n"] == 0
    database.migrate("0006", downgrade=True)
    with database.connect() as conn:
        assert conn.execute("SELECT to_regclass('owner_passkeys') AS t").fetchone()["t"] is None
    database.migrate()
