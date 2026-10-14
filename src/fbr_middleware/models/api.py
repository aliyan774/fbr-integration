"""SAP-facing request and response models (docs/middleware-flow.md section 0)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from .fbr import NTN_CNIC_PATTERN, InvoicePayload


class SubmissionStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    REJECTED_LOCAL = "REJECTED_LOCAL"
    REJECTED_FBR = "REJECTED_FBR"
    FAILED_UPSTREAM = "FAILED_UPSTREAM"
    AUTH_ERROR = "AUTH_ERROR"
    UNKNOWN = "UNKNOWN"
    VALIDATED = "VALIDATED"  # dry run only: PRAL validate passed, nothing posted, nothing stored


#: HTTP status returned to SAP for a fresh (non-replayed) result.
HTTP_STATUS_BY_RESULT: dict[SubmissionStatus, int] = {
    SubmissionStatus.SUBMITTED: 201,
    SubmissionStatus.REJECTED_LOCAL: 422,
    SubmissionStatus.REJECTED_FBR: 422,
    SubmissionStatus.FAILED_UPSTREAM: 502,
    SubmissionStatus.AUTH_ERROR: 502,
    SubmissionStatus.UNKNOWN: 504,
    SubmissionStatus.VALIDATED: 200,
}

#: Statuses that are replayed (HTTP 200, replayed=true) instead of calling PRAL again.
#: Rejections and upstream failures are re-processed on resubmit (state machine, flow doc section 6).
#: UNKNOWN is replayed because a blind re-post could create a duplicate FBR invoice; an operator
#: must reconcile it first (admin endpoint: TODO).
FINAL_STATUSES: frozenset[SubmissionStatus] = frozenset(
    {SubmissionStatus.SUBMITTED, SubmissionStatus.UNKNOWN}
)

#: HTTP status returned to SAP while the middleware is processing (not exposed, DB only).
STATUS_RECEIVED = "RECEIVED"


class InvoiceSubmission(BaseModel):
    """POST /api/v1/{env}/invoices body (env = dev | prd)."""

    model_config = ConfigDict(extra="forbid")

    erpInvoiceNo: str = Field(min_length=1, max_length=50, description="SAP billing document; idempotency key")
    callbackRef: str | None = Field(default=None, max_length=100)
    # `invoice` is declared before `sellerNtn` so the sellerNtn validator can compare against it.
    invoice: InvoicePayload
    sellerNtn: str = Field(pattern=NTN_CNIC_PATTERN, description="must equal invoice.sellerNTNCNIC")

    @field_validator("erpInvoiceNo")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("erpInvoiceNo must be a non-empty string")
        return v

    @field_validator("sellerNtn")
    @classmethod
    def _matches_invoice_seller(cls, v: str, info: ValidationInfo) -> str:
        invoice = info.data.get("invoice")
        if invoice is not None and v != invoice.sellerNTNCNIC:
            raise ValueError("sellerNtn must equal invoice.sellerNTNCNIC")
        return v


class LocalError(BaseModel):
    field: str
    code: str
    message: str


class ErrorDescription(BaseModel):
    code: str
    description: str


class InvoiceEnvelope(BaseModel):
    """Response of POST /api/v1/{env}/invoices and GET /api/v1/{env}/invoices/{erpInvoiceNo}.

    The fields of the "Response parameters" table plus ``environment`` ("dev" | "prd").
    """

    trackingId: str
    environment: str  # "dev" | "prd"
    erpInvoiceNo: str
    sellerNtn: str
    callbackRef: str | None
    status: SubmissionStatus
    replayed: bool
    fbrInvoiceNo: str | None
    dated: str | None
    pralHttpStatus: int | None
    pralResponse: dict[str, Any] | None
    localErrors: list[LocalError]
    errorDescriptions: list[ErrorDescription]
    attempts: int
    receivedAt: datetime
    completedAt: datetime
    elapsedMs: int

    @property
    def http_status(self) -> int:
        if self.replayed:
            return 200
        return HTTP_STATUS_BY_RESULT[self.status]
