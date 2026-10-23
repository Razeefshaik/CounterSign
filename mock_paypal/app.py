"""Local mock PayPal HTTP server. Run with python -m mock_paypal."""

import asyncio
import base64
import binascii
import os
from datetime import datetime
from urllib.parse import parse_qs
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.exceptions import HTTPException

from mock_paypal.clock import timestamp
from mock_paypal.config import MockConfig
from mock_paypal.faults import FaultMiddleware
from mock_paypal.faults import router as fault_router
from mock_paypal.payout_routes import router as payout_router
from mock_paypal.payouts import PayoutBehavior, PayoutError, PayoutService
from mock_paypal.store import DEFAULT_SCOPE, TOKEN_LIFETIME_SECONDS, MockStore


class ControlBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ResetBody(ControlBody):
    seed: int = 17


class AdvanceBody(ControlBody):
    seconds: int = Field(ge=0)


class SetBody(ControlBody):
    now: str


def error(
    request: Request, status: int, name: str, message: str, *, details=None, links=None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "name": name,
            "message": message,
            "debug_id": request.state.debug_id,
            "information_link": "https://developer.paypal.com/api/rest/responses/",
            "details": details or [],
            "links": links or [],
        },
    )


def create_app(
    *,
    control_enabled: bool | None = None,
    oauth_scope: str = DEFAULT_SCOPE,
    payout_behavior: PayoutBehavior | None = None,
    negative_values: dict | None = None,
) -> FastAPI:
    app = FastAPI(
        title="CounterSign mock PayPal", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.store = MockStore()
    app.state.control_lock = asyncio.Lock()
    app.state.payout_service = PayoutService(
        app.state.store, payout_behavior or PayoutBehavior()
    )
    app.include_router(payout_router)
    app.include_router(fault_router)
    enabled = (
        os.environ.get("MOCK_CONTROL") == "1"
        if control_enabled is None
        else control_enabled
    )

    @app.middleware("http")
    async def protocol(request: Request, call_next):
        request.state.debug_id = uuid4().hex[:13]
        path = request.url.path
        if path.startswith("/_mock/") and not enabled:
            response = error(
                request, 404, "RESOURCE_NOT_FOUND", "Control API is disabled"
            )
        elif path != "/v1/oauth2/token" and not path.startswith("/_mock/"):
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not app.state.store.valid_token(token):
                response = JSONResponse(
                    status_code=401,
                    content={
                        "error": "invalid_token",
                        "error_description": "A valid mock bearer token is required",
                    },
                )
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)
        response.headers["paypal-debug-id"] = request.state.debug_id
        return response

    @app.exception_handler(PayoutError)
    async def payout_error(request: Request, exc: PayoutError):
        return error(
            request,
            exc.status,
            exc.name,
            exc.message,
            details=exc.details,
            links=exc.links,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        names = {404: "RESOURCE_NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
        return error(
            request,
            exc.status_code,
            names.get(exc.status_code, "INVALID_REQUEST"),
            "Requested resource or method is unavailable",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Do not echo rejected input; callers could accidentally submit secrets.
        return error(
            request, 422, "INVALID_REQUEST", "Invalid request body or parameters"
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        response = error(request, 500, "INTERNAL_SERVER_ERROR", "Mock server error")
        response.headers["paypal-debug-id"] = request.state.debug_id
        return response

    @app.post("/v1/oauth2/token")
    async def token(request: Request):
        scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
            client, separator, secret = decoded.partition(":")
            valid = scheme.lower() == "basic" and separator and client and secret
        except (ValueError, UnicodeError, binascii.Error):
            valid = False
        if not valid:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "invalid_client",
                    "error_description": "Mock Basic auth is required",
                },
            )
        content_type = (
            request.headers.get("content-type", "").split(";")[0].strip().lower()
        )
        if content_type != "application/x-www-form-urlencoded":
            return error(
                request, 400, "INVALID_REQUEST", "Use form-encoded client_credentials"
            )
        try:
            form = parse_qs((await request.body()).decode("utf-8"))
        except UnicodeError:
            return error(request, 400, "INVALID_REQUEST", "Invalid form encoding")
        if form.get("grant_type") != ["client_credentials"]:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "unsupported_grant_type",
                    "error_description": "Use client_credentials",
                },
            )
        store = app.state.store
        return {
            "scope": oauth_scope,
            "access_token": store.issue_token(),
            "token_type": "Bearer",
            "app_id": "APP-MOCK",
            "expires_in": TOKEN_LIFETIME_SECONDS,
            "nonce": timestamp(store.clock.now) + store.new_id("NONCE"),
        }

    @app.post("/_mock/reset")
    async def reset(body: ResetBody):
        async with app.state.control_lock:
            app.state.store.reset(body.seed)
            return {"seed": body.seed, "now": timestamp(app.state.store.clock.now)}

    @app.get("/_mock/clock")
    async def clock():
        return {"now": timestamp(app.state.store.clock.now)}

    @app.post("/_mock/clock/advance")
    async def advance(body: AdvanceBody, request: Request):
        async with app.state.control_lock:
            try:
                await app.state.store.clock.advance(body.seconds)
            except (OverflowError, ValueError):
                return error(request, 422, "INVALID_REQUEST", "Invalid clock advance")
            return {"now": timestamp(app.state.store.clock.now)}

    @app.post("/_mock/clock/set")
    async def set_clock(body: SetBody, request: Request):
        async with app.state.control_lock:
            try:
                target = datetime.fromisoformat(body.now)
                await app.state.store.clock.set(target)
            except (OverflowError, ValueError):
                return error(
                    request,
                    422,
                    "INVALID_REQUEST",
                    "Use a timezone-aware time at or after the current clock",
                )
            return {"now": timestamp(app.state.store.clock.now)}

    @app.get("/_mock/config")
    async def get_config():
        return app.state.store.config.model_dump()

    @app.post("/_mock/config")
    async def config(body: dict, request: Request):
        async with app.state.control_lock:
            try:
                updated = MockConfig.model_validate(
                    {**app.state.store.config.model_dump(), **body}
                )
            except ValidationError:
                return error(
                    request, 422, "INVALID_REQUEST", "Invalid mock configuration"
                )
            app.state.store.config = updated
            app.state.payout_service.reprice_unclaimed()
            return updated.model_dump()

    app.add_middleware(
        FaultMiddleware, store=app.state.store, negative_values=negative_values
    )
    return app


app = create_app()
