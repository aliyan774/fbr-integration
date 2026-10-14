"""POST /api/v1/{env}/invoices and GET /api/v1/{env}/invoices/{erpInvoiceNo}.

``env`` is ``dev`` (PRAL sandbox, ``_sb`` URLs, scenarioId required) or ``prd`` (PRAL production,
scenarioId stripped). SAP must send the Bearer token issued for that environment.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi.responses import JSONResponse

from ..config import ENV_NAMES, FbrEnvironment, Settings, get_settings
from ..models.api import InvoiceEnvelope, InvoiceSubmission
from ..security import require_api_key, require_bearer
from ..services.prevalidate import environment_errors
from ..services.submit import SubmissionService

router = APIRouter(prefix="/api/v1/{env}/invoices", tags=["invoices"])

TRACKING_HEADER = "X-Tracking-Id"


def resolve_env(
    env: str = Path(description="PRAL environment: dev (sandbox) or prd (production)"),
    settings: Settings = Depends(get_settings),
) -> FbrEnvironment:
    resolved = settings.environment(env)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown environment '{env}'. Use one of {list(ENV_NAMES)} "
            f"(enabled on this instance: {list(settings.enabled_envs)})",
        )
    return resolved


def _service(request: Request) -> SubmissionService:
    return request.app.state.submission_service


def _envelope_response(envelope: InvoiceEnvelope, http_status: int) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content=envelope.model_dump(mode="json"),
        headers={TRACKING_HEADER: envelope.trackingId},
    )


@router.post(
    "",
    response_model=InvoiceEnvelope,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a sale invoice or debit note to PRAL (validate, then post) in this request",
    responses={
        200: {"description": "Replayed idempotent result (replayed=true)"},
        201: {"description": "SUBMITTED"},
        401: {"description": "Missing/invalid X-API-Key or missing/malformed Authorization: Bearer"},
        404: {"description": "Unknown environment segment (use dev or prd)"},
        422: {"description": "Shape violation (nothing stored), REJECTED_LOCAL or REJECTED_FBR"},
        502: {"description": "FAILED_UPSTREAM or AUTH_ERROR"},
        504: {"description": "UNKNOWN: post sent, no answer. Do not resubmit blindly."},
    },
)
async def submit_invoice(
    request: Request,
    submission: InvoiceSubmission,
    _api_key: str = Depends(require_api_key),
    token: str = Depends(require_bearer),
    env: FbrEnvironment = Depends(resolve_env),
) -> JSONResponse:
    # Environment-dependent shape rule (scenarioId in dev): 422, nothing stored.
    env_errors = environment_errors(submission.invoice, env)
    if env_errors:
        return JSONResponse(
            status_code=422,
            content={"detail": [{"field": e.field, "message": e.message, "type": e.code} for e in env_errors]},
        )
    result = await _service(request).submit(env, submission, token)
    return _envelope_response(result.envelope, result.http_status)


@router.post(
    "/validate",
    response_model=InvoiceEnvelope,
    status_code=status.HTTP_200_OK,
    summary="Dry run: local rules + PRAL validateinvoicedata only. Nothing is posted or stored.",
    responses={
        200: {"description": "VALIDATED: PRAL accepted the payload; no FBR number was issued"},
        401: {"description": "Missing/invalid X-API-Key or missing/malformed Authorization: Bearer"},
        404: {"description": "Unknown environment segment (use dev or prd)"},
        422: {"description": "Shape violation, REJECTED_LOCAL or REJECTED_FBR"},
        502: {"description": "FAILED_UPSTREAM or AUTH_ERROR"},
    },
)
async def validate_invoice(
    request: Request,
    submission: InvoiceSubmission,
    _api_key: str = Depends(require_api_key),
    token: str = Depends(require_bearer),
    env: FbrEnvironment = Depends(resolve_env),
) -> JSONResponse:
    env_errors = environment_errors(submission.invoice, env)
    if env_errors:
        return JSONResponse(
            status_code=422,
            content={"detail": [{"field": e.field, "message": e.message, "type": e.code} for e in env_errors]},
        )
    result = await _service(request).validate_only(env, submission, token)
    return _envelope_response(result.envelope, result.http_status)


@router.get(
    "/{erp_invoice_no}",
    response_model=InvoiceEnvelope,
    summary="Stored result of an earlier submission (e.g. after an ABAP-side timeout)",
    responses={404: {"description": "No submission with this erpInvoiceNo in this environment"}},
)
async def get_invoice(
    request: Request,
    erp_invoice_no: str,
    _api_key: str = Depends(require_api_key),
    env: FbrEnvironment = Depends(resolve_env),
) -> JSONResponse:
    envelope = await _service(request).get(env, erp_invoice_no)
    if envelope is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown erpInvoiceNo")
    return _envelope_response(envelope, 200)
