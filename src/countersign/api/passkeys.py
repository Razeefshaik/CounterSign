"""C51 owner-guarded passkey HTTP interface; no provider calls or raw-error responses."""

import json
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from countersign.api.owner import OwnerBody, OwnerRoute, decision_clock, owner_guard, public_result
from countersign.approvals.passkeys import PasskeyError, Passkeys


class HashBody(OwnerBody):
    payload_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class RegistrationBody(OwnerBody):
    challenge_id: UUID = Field(strict=False)
    credential: dict
    label: str = Field(default="Owner passkey", min_length=1, max_length=80)


class AssertionBody(HashBody):
    challenge_id: UUID = Field(strict=False)
    credential: dict


def relying_party(request, settings):
    rp_id, origin = settings.passkey_rp_id, settings.passkey_origin
    hosted = (getattr(request.app.state, "passkey_hosted_demo", False)
              and settings.mode == "demo" and settings.paypal.is_mock)
    if not rp_id and not origin and hosted:
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        rp_id = request.url.hostname
        origin = f"{scheme}://{request.url.netloc}"
    try:
        parsed = urlsplit(origin)
        valid = (
            rp_id and parsed.hostname and not parsed.username and not parsed.password
            and parsed.path == "" and not parsed.query and not parsed.fragment
            and (parsed.scheme == "https" or
                 (parsed.scheme == "http" and parsed.hostname == "localhost"))
            and (parsed.hostname == rp_id or parsed.hostname.endswith("." + rp_id))
            and request.headers.get("origin", origin) == origin
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise PasskeyError()
    return rp_id, origin


def bounded(credential):
    if len(json.dumps(credential)) > 65536:
        raise PasskeyError()
    return credential


def build_router(database, settings):
    router = APIRouter(route_class=OwnerRoute, dependencies=[Depends(owner_guard(settings))])
    service = Passkeys(database, settings)

    @router.post("/owner/passkeys/register/options")
    async def register_options(request: Request):
        async def run():
            return await service.register_options(*relying_party(request, settings))
        return await public_result(run())

    @router.post("/owner/passkeys/register/verify", status_code=201)
    async def register(body: RegistrationBody, request: Request):
        async def run():
            return await service.register(body.challenge_id, bounded(body.credential), body.label,
                                          *relying_party(request, settings))
        return await public_result(run())

    @router.post("/owner/proposals/{id}/passkey/options")
    async def options(id: UUID, body: HashBody, request: Request):
        async def run():
            return await service.options(id, body.payload_hash, *relying_party(request, settings))
        return await public_result(run())

    @router.post("/owner/proposals/{id}/passkey/approve", status_code=201)
    async def approve(id: UUID, body: AssertionBody, request: Request):
        async def run():
            # The challenge keeps real time; the approval is judged at the decision clock.
            return await service.approve(id, body.payload_hash, body.challenge_id,
                                         bounded(body.credential), *relying_party(request, settings),
                                         decided_at=await decision_clock(settings))
        return await public_result(run())

    return router
