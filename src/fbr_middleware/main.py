"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__
from .api import health, invoices
from .clients.pral import PralClient
from .config import get_settings
from .db.session import init_db, make_engine, make_session_factory
from .logging_setup import setup_logging
from .services.concurrency import ConcurrencyGate
from .services.submit import SubmissionService

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.LOG_DIR, settings.LOG_LEVEL)

    engine = make_engine(settings.DATABASE_URL)
    await init_db(engine)
    session_factory = make_session_factory(engine)

    http = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.FBR_TIMEOUT_SECONDS, connect=min(10.0, settings.FBR_TIMEOUT_SECONDS)),
        limits=httpx.Limits(max_connections=settings.MAX_INFLIGHT_TOTAL + 5),
    )
    pral = PralClient(settings, http)
    gate = ConcurrencyGate(settings.MAX_INFLIGHT_TOTAL, settings.MAX_INFLIGHT_PER_SELLER)

    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.http = http
    app.state.pral_client = pral
    app.state.gate = gate
    app.state.submission_service = SubmissionService(settings, session_factory, pral, gate)

    # TODO: reference data sync scheduler (REFERENCE_SYNC_CRON) - flow doc section 4.
    # TODO: QR generation once PRAL confirms the payload - flow doc section 2.
    # TODO: admin endpoints (list/reconcile UNKNOWN, force reference sync) - work breakdown M4.1.

    log.info("fbr-middleware %s started", __version__)
    for env in settings.environments():
        log.info(
            "environment %s (%s): /api/v1/%s/invoices -> %s | %s | scenarioId sent=%s",
            env.name, env.label, env.name, env.validate_url, env.post_url, env.send_scenario_id,
        )
    try:
        yield
    finally:
        await http.aclose()
        await engine.dispose()
        log.info("fbr-middleware stopped")


def _field_path(loc: tuple) -> str:
    return ".".join(str(p) for p in loc if p != "body")


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """422 with one entry per field. Nothing is stored for shape violations."""
    detail = [
        {
            "field": _field_path(tuple(e.get("loc", ()))),
            "message": e.get("msg", ""),
            "type": e.get("type", ""),
        }
        for e in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


def create_app() -> FastAPI:
    app = FastAPI(
        title="FBR Digital Invoicing Middleware",
        version=__version__,
        description="Synchronous SAP -> PRAL pass-through (validateinvoicedata, then postinvoicedata).",
        lifespan=lifespan,
    )
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.include_router(invoices.router)
    app.include_router(health.router)
    return app


app = create_app()
