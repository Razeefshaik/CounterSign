"""C51: verify WebAuthn, then use the unchanged approval service in one transaction."""

import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    UserVerificationRequirement,
)

from countersign.approvals import ApprovalError, ApprovalService
from countersign.db import Repositories, uuid7
from countersign.ledger.journal import serialize_on


class PasskeyError(ApprovalError):
    def __init__(self, code="PASSKEY_INVALID"):
        super().__init__(code, 409)


def same_origin_context(credential):
    """This owner flow does not permit embedded/cross-origin ceremonies."""
    client = json.loads(base64url_to_bytes(credential["response"]["clientDataJSON"]))
    if client.get("crossOrigin", False) is not False or "topOrigin" in client:
        raise PasskeyError()


class TransactionDatabase:
    """Let approve own its normal savepoint on our already locked connection."""

    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def tx(self):
        async with self.conn.transaction():
            yield self.conn


class Passkeys:
    def __init__(self, database, settings, *, clock=None):
        self.database, self.settings = database, settings
        self.clock = clock or (lambda: datetime.now(UTC))

    async def _proposal(self, conn, proposal_id, payload_hash):
        row = await Repositories(conn).proposals.get(proposal_id, for_update=True)
        pending = ((row or {}).get("verdict_details") or {}).get("approval_request") or (
            (row or {}).get("raw_output") or {}
        ).get("approval_request", {})
        if not row or row["state"] != "NEEDS_OWNER" or pending.get("payload_hash") != payload_hash:
            raise PasskeyError("PASSKEY_HASH_MISMATCH")

    async def _issue(self, conn, purpose, rp_id, origin, proposal_id=None, payload_hash=None):
        identifier, challenge = uuid7(), secrets.token_bytes(32)
        await conn.execute(
            "INSERT INTO passkey_challenges "
            "(id,purpose,proposal_id,payload_hash,challenge,rp_id,origin,expires_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (identifier, purpose, proposal_id, payload_hash, challenge, rp_id, origin,
             self.clock() + timedelta(minutes=2)),
        )
        return identifier, challenge

    async def _challenge(self, conn, identifier, purpose, rp_id, origin):
        row = await (await conn.execute(
            "SELECT * FROM passkey_challenges WHERE id=%s FOR UPDATE", (identifier,)
        )).fetchone()
        if not row or row["purpose"] != purpose:
            raise PasskeyError()
        if row["used_at"] is not None:
            raise PasskeyError("PASSKEY_CHALLENGE_USED")
        if self.clock() >= row["expires_at"]:
            raise PasskeyError("PASSKEY_CHALLENGE_EXPIRED")
        if (row["rp_id"], row["origin"]) != (rp_id, origin):
            raise PasskeyError()
        return row

    async def register_options(self, rp_id, origin):
        async with self.database.tx() as conn:
            identifier, challenge = await self._issue(conn, "register", rp_id, origin)
            keys = await (await conn.execute("SELECT credential_id FROM owner_passkeys")).fetchall()
            options = generate_registration_options(
                rp_id=rp_id, rp_name="CounterSign", user_id=b"countersign-owner",
                user_name="owner", challenge=challenge, timeout=120000,
                exclude_credentials=[PublicKeyCredentialDescriptor(id=k["credential_id"])
                                     for k in keys],
                authenticator_selection=AuthenticatorSelectionCriteria(
                    user_verification=UserVerificationRequirement.REQUIRED),
            )
            return {"challenge_id": str(identifier), "publicKey": json.loads(options_to_json(options))}

    async def register(self, identifier, credential, label, rp_id, origin):
        async with self.database.tx() as conn:
            challenge = await self._challenge(conn, identifier, "register", rp_id, origin)
            try:
                same_origin_context(credential)
                verified = verify_registration_response(
                    credential=credential, expected_challenge=challenge["challenge"],
                    expected_rp_id=rp_id, expected_origin=origin, require_user_verification=True,
                )
            except Exception:  # noqa: BLE001 - verifier errors must never echo credential material.
                raise PasskeyError() from None
            exists = await (await conn.execute(
                "SELECT 1 FROM owner_passkeys WHERE credential_id=%s", (verified.credential_id,)
            )).fetchone()
            if exists:
                raise PasskeyError()
            transports = credential.get("response", {}).get("transports", [])
            transports = [t for t in transports if t in {"usb", "nfc", "ble", "internal", "hybrid"}]
            await conn.execute(
                "INSERT INTO owner_passkeys "
                "(id,credential_id,public_key,sign_count,transports,label) VALUES (%s,%s,%s,%s,%s,%s)",
                (uuid7(), verified.credential_id, verified.credential_public_key,
                 verified.sign_count, transports, label),
            )
            await conn.execute("UPDATE passkey_challenges SET used_at=%s WHERE id=%s",
                               (self.clock(), identifier))
            return {"registered": True}

    async def options(self, proposal_id, payload_hash, rp_id, origin):
        async with self.database.tx() as conn:
            await serialize_on(conn, "countersign:owner-decisions")
            await self._proposal(conn, proposal_id, payload_hash)
            keys = await (await conn.execute("SELECT credential_id FROM owner_passkeys")).fetchall()
            if not keys:
                return {"registered": False}
            identifier, challenge = await self._issue(
                conn, "approve", rp_id, origin, proposal_id, payload_hash)
            options = generate_authentication_options(
                rp_id=rp_id, challenge=challenge, timeout=120000,
                allow_credentials=[PublicKeyCredentialDescriptor(id=k["credential_id"]) for k in keys],
                user_verification=UserVerificationRequirement.REQUIRED,
            )
            return {"registered": True, "challenge_id": str(identifier),
                    "publicKey": json.loads(options_to_json(options))}

    async def approve(self, proposal_id, payload_hash, identifier, credential, rp_id, origin):
        async with self.database.tx() as conn:
            # Same order as every owner decision, before locking any binding rows.
            await serialize_on(conn, "countersign:owner-decisions")
            challenge = await self._challenge(conn, identifier, "approve", rp_id, origin)
            if (challenge["proposal_id"], challenge["payload_hash"]) != (proposal_id, payload_hash):
                raise PasskeyError("PASSKEY_HASH_MISMATCH")
            await self._proposal(conn, proposal_id, payload_hash)
            try:
                same_origin_context(credential)
                credential_id = base64url_to_bytes(credential["id"])
                key = await (await conn.execute(
                    "SELECT * FROM owner_passkeys WHERE credential_id=%s FOR UPDATE",
                    (credential_id,),
                )).fetchone()
                if key is None:
                    raise PasskeyError()
                verified = verify_authentication_response(
                    credential=credential, expected_challenge=challenge["challenge"],
                    expected_rp_id=rp_id, expected_origin=origin,
                    credential_public_key=key["public_key"],
                    credential_current_sign_count=key["sign_count"], require_user_verification=True,
                )
                # Counterless authenticators are deliberately refused for this workflow.
                if verified.new_sign_count <= key["sign_count"]:
                    raise PasskeyError()
            except Exception:  # noqa: BLE001 - verifier errors must never echo credential material.
                raise PasskeyError() from None
            now = self.clock()
            await conn.execute("UPDATE passkey_challenges SET used_at=%s WHERE id=%s",
                               (now, identifier))
            await conn.execute("UPDATE owner_passkeys SET sign_count=%s,last_used_at=%s WHERE id=%s",
                               (verified.new_sign_count, now, key["id"]))
            short = hashlib.sha256(credential_id).hexdigest()[:12]
            # No change to approve's state, expiry, versions, shield or single-use checks.
            approval = await ApprovalService(
                TransactionDatabase(conn), behavior=self.settings.paypal.behavior
            ).approve(proposal_id, payload_hash, approved_by="owner:passkey:" + short)
            await conn.execute(
                "INSERT INTO audit_log(actor,kind,proposal_id,details) VALUES ('owner',%s,%s,%s)",
                ("OWNER_COUNTERSIGNED", proposal_id, Jsonb({
                    "credential_short_id": short, "sign_count": verified.new_sign_count,
                    "user_verified": verified.user_verified, "user_present": True,
                    "signature_verified": True, "origin_verified": True, "rp_id_verified": True,
                })),
            )
            return {"approval": approval}
