"""GET /health: liveness, DB reachability, in-flight count and the configured PRAL environments."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .. import __version__

router = APIRouter(tags=["ops"])


@router.get("/", summary="Root: answers 200 so an SM59 connection test (which calls '/') shows success", include_in_schema=False)
async def root() -> JSONResponse:
    return JSONResponse(
        {
            "service": "fbr-middleware",
            "version": __version__,
            "health": "/health",
            "submit": ["/api/v1/dev/invoices", "/api/v1/prd/invoices"],
        }
    )


@router.get("/health", summary="Liveness, DB check and in-flight count")
async def health(request: Request) -> JSONResponse:
    state = request.app.state
    settings = state.settings
    db_status = "ok"
    try:
        async with state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - only on a broken DB
        db_status = f"error: {type(exc).__name__}"

    body = {
        "status": "ok" if db_status == "ok" else "degraded",
        "version": __version__,
        "environments": {
            env.name: {
                "label": env.label,
                "submitUrl": f"/api/v1/{env.name}/invoices",
                "pralValidateUrl": env.validate_url,
                "pralPostUrl": env.post_url,
                "sendScenarioId": env.send_scenario_id,
            }
            for env in settings.environments()
        },
        "db": db_status,
        "inflight": state.gate.inflight,
        "maxInflightTotal": state.gate.max_total,
        "maxInflightPerSeller": state.gate.max_per_seller,
        # TODO: last successful PRAL call and last reference sync time (flow doc section 7).
        "lastReferenceSyncAt": None,
    }
    return JSONResponse(status_code=200 if db_status == "ok" else 503, content=body)
