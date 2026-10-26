"""Runtime application composition. Importing this module never reads credentials."""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from countersign.api import dashboard, owner, runs, webhooks
from countersign.config import (
    Settings,
    load_hosted_settings,
    load_settings,
    require_hosted_demo,
)
from countersign.db import Database
from countersign.executor import Executor
from countersign.inbox import Inbox
from countersign.paypal.http import PayPalHTTP
from countersign.paypal.read import PayPalRead
from countersign.paypal.webhook_verify import WebhookVerifier
from countersign.reconcile import Reconciler
from countersign.worker import load_worker_settings, run_workers


class _DashboardFixtures:
    """M15 is pending: financial documents stay fixtures; metadata reflects runtime."""

    def __init__(self, settings):
        self.source = dashboard.FixtureSource()
        self.settings = settings

    async def list(self, *args, **kwargs):
        return await self.source.list(*args, **kwargs)

    async def get(self, *args, **kwargs):
        return await self.source.get(*args, **kwargs)

    async def singleton(self, resource, params):
        doc = await self.source.singleton(resource, params)
        if resource == "meta":
            doc.update(
                mode=self.settings.mode,
                paypal_env="mock" if self.settings.paypal.is_mock else "sandbox",
                simulated=self.settings.paypal.is_mock,
                simulation_label="Simulated: mock PayPal" if self.settings.paypal.is_mock else None,
            )
        return doc


def create_app(
    *,
    settings: Settings | None = None,
    database=None,
    http=None,
    reader=None,
    verifier=None,
    executor=None,
    reconciler=None,
    worker_settings=None,
    start_worker: bool = True,
    static_dir: Path | None = None,
) -> FastAPI:
    """Injected dependencies belong to the caller; only internally opened ones close.

    Routers mount at lifespan startup, after runtime configuration is validated.
    Test clients must enter app.router.lifespan_context(app), just like ASGI servers.
    """

    @asynccontextmanager
    async def runtime_lifespan(app):
        runtime = settings if settings is not None else load_settings()
        async with AsyncExitStack() as stack:
            db = database
            if db is None:
                db = await stack.enter_async_context(Database(runtime))
            transport = http
            if transport is None and (
                reader is None or verifier is None or (start_worker and executor is None)
            ):
                transport = await stack.enter_async_context(PayPalHTTP(runtime.paypal))
            resource_reader = reader if reader is not None else PayPalRead(transport)
            inbox = Inbox(
                db,
                resource_reader,
                verifier if verifier is not None else WebhookVerifier(transport),
            )
            app.state.settings = runtime
            app.state.database = db
            app.state.inbox = inbox
            app.state.dashboard_source = "fixtures"
            configured = execution = reconciliation = None
            if start_worker:
                configured = (
                    worker_settings if worker_settings is not None else load_worker_settings()
                )
                execution = executor if executor is not None else Executor.from_http(db, transport)
                reconciliation = (
                    reconciler if reconciler is not None else Reconciler(db, resource_reader)
                )
                app.state.executor = execution
                app.state.reconciler = reconciliation
            original_routes = list(app.router.routes)
            app.include_router(webhooks.build_router(inbox))
            app.include_router(owner.build_router(db, runtime), prefix="/api/v1")
            app.include_router(runs.build_router(db, runtime), prefix="/api/v1")
            app.include_router(
                dashboard.build_router(_DashboardFixtures(runtime)), prefix="/api/v1"
            )
            if static_dir is not None:
                # Mounted last so every API and webhook route above keeps priority.
                app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
            app.openapi_schema = None
            stop = asyncio.Event()
            task = None
            if start_worker:
                task = asyncio.create_task(
                    run_workers(db, inbox, reconciliation, execution, stop, settings=configured)
                )
            try:
                yield
            finally:
                stop.set()
                if task is not None:
                    await task
                app.router.routes[:] = original_routes
                app.openapi_schema = None

    @asynccontextmanager
    async def lifespan(app):
        try:
            async with runtime_lifespan(app):
                yield
        except Exception:  # noqa: BLE001 - lifecycle errors cannot expose private driver values.
            raise RuntimeError("CounterSign application lifecycle failed") from None

    result = FastAPI(title="CounterSign", lifespan=lifespan)

    @result.exception_handler(HTTPException)
    async def http_error(request, exc):
        messages = {
            404: ("NOT_FOUND", "Resource not found"),
            405: ("METHOD_NOT_ALLOWED", "Method not allowed"),
        }
        code, message = messages.get(
            exc.status_code, ("REQUEST_FAILED", "Request could not be completed")
        )
        return JSONResponse(
            {"error": {"code": code, "message": message}}, status_code=exc.status_code
        )

    @result.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            {"error": {"code": "INVALID_REQUEST", "message": "Invalid request"}}, status_code=422
        )

    @result.middleware("http")
    async def fixture_source_header(request, call_next):
        response = await call_next(request)
        # All GET /api/v1 resources mounted by this card are the dashboard router.
        if request.method == "GET" and request.url.path.startswith("/api/v1/"):
            response.headers["X-CounterSign-Dashboard-Source"] = "fixtures"
        return response

    return result


app = create_app()

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


def create_hosted_app(**overrides) -> FastAPI:
    """C27 public demo: demo mode on the loopback mock only, no background workers.

    Settings are validated when the server calls this factory, so a live or
    sandbox configuration stops the hosted process before it serves anything.
    Only the staged demo CLI ticks the engine, as with demo_week --serve.
    """
    if overrides.get("settings") is None:
        overrides["settings"] = load_hosted_settings()
    require_hosted_demo(overrides["settings"])
    overrides.setdefault("static_dir", WEB_DIR)
    return create_app(start_worker=False, **overrides)


def main():
    """Launch with a psycopg-compatible loop on Windows without import-time policy changes."""
    import sys

    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000, loop="asyncio"))
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(server.serve())
    else:
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
