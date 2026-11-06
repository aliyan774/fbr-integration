"""GET /api/v1/{env}/token-check: verify a PRAL Bearer token without touching any invoice.

Calls PRAL's reference API ``/pdi/v1/provinces`` with the token SAP supplied. That endpoint takes
the same Bearer token as the invoice APIs and needs no seller NTN, so it answers one question only:
does PRAL accept this token right now. Nothing is stored.
"""

from __future__ import annotations

import logging
import time

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..config import FbrEnvironment
from ..security import require_api_key, require_bearer
from .invoices import resolve_env

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/{env}", tags=["ops"])


@router.get(
    "/token-check",
    summary="Verify the PRAL Bearer token against PRAL's reference API (no invoice, nothing stored)",
    responses={
        200: {"description": "tokenStatus OK: PRAL accepted the token"},
        401: {"description": "Missing/invalid X-API-Key or missing/malformed Authorization: Bearer"},
        404: {"description": "Unknown environment segment"},
        502: {"description": "tokenStatus AUTH_ERROR (PRAL rejected the token) or FAILED_UPSTREAM (PRAL unreachable)"},
    },
)
async def token_check(
    request: Request,
    _api_key: str = Depends(require_api_key),
    token: str = Depends(require_bearer),
    env: FbrEnvironment = Depends(resolve_env),
) -> JSONResponse:
    pral = request.app.state.pral_client
    t0 = time.perf_counter()
    try:
        result = await pral.check_token(env, token)
    except httpx.HTTPError as exc:
        log.warning("token-check env=%s: PRAL unreachable: %s", env.name, type(exc).__name__)
        return JSONResponse(
            status_code=502,
            content={
                "environment": env.name,
                "tokenStatus": "FAILED_UPSTREAM",
                "pralHttpStatus": None,
                "pralResponse": None,
                "checkedAgainst": f"{env.base_url}/pdi/v1/provinces",
                "detail": f"{type(exc).__name__}: {exc}",
                "elapsedMs": int((time.perf_counter() - t0) * 1000),
            },
        )

    elapsed = int((time.perf_counter() - t0) * 1000)
    base = {
        "environment": env.name,
        "pralHttpStatus": result.http_status,
        "checkedAgainst": result.url,
        "elapsedMs": elapsed,
    }
    if result.http_status == 200 and isinstance(result.body, list):
        log.info("token-check env=%s: OK (%d provinces, %d ms)", env.name, len(result.body), elapsed)
        return JSONResponse(
            status_code=200,
            content={
                **base,
                "tokenStatus": "OK",
                "provinces": [p.get("stateProvinceDesc") for p in result.body if isinstance(p, dict)],
            },
        )
    status = "AUTH_ERROR" if result.http_status == 401 else "FAILED_UPSTREAM"
    log.info("token-check env=%s: %s (HTTP %d)", env.name, status, result.http_status)
    return JSONResponse(status_code=502, content={**base, "tokenStatus": status, "pralResponse": result.body})
