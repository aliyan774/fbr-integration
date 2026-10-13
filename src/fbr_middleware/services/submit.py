"""Submission orchestration: steps 3-6 of the submission contract (flow doc section 0).

    idempotency replay -> pre-validate -> PRAL validate -> PRAL post -> persist -> envelope
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..clients.pral import AttemptRecord, AuthError, PralClient, PralResult, TimeoutAfterSend, UpstreamError
from ..config import FbrEnvironment, Settings
from ..db.models import Attempt, Invoice
from ..models.api import (
    FINAL_STATUSES,
    STATUS_RECEIVED,
    InvoiceEnvelope,
    InvoiceSubmission,
    LocalError,
    SubmissionStatus,
)
from ..models.fbr import PralResponse, dumps_pral
from .concurrency import ConcurrencyGate
from .errors import descriptions_for
from .prevalidate import prepare_payload, prevalidate

log = logging.getLogger(__name__)


@dataclass
class SubmitResult:
    envelope: InvoiceEnvelope
    http_status: int


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def envelope_from_row(row: Invoice, replayed: bool) -> InvoiceEnvelope:
    status = SubmissionStatus(row.status) if row.status != STATUS_RECEIVED else SubmissionStatus.UNKNOWN
    received = _aware(row.received_at) or _utcnow()
    completed = _aware(row.completed_at) or received
    return InvoiceEnvelope(
        trackingId=row.tracking_id,
        environment=row.env,
        erpInvoiceNo=row.erp_invoice_no,
        sellerNtn=row.seller_ntn,
        callbackRef=row.callback_ref,
        status=status,
        replayed=replayed,
        fbrInvoiceNo=row.fbr_invoice_no,
        dated=row.dated,
        pralHttpStatus=row.pral_http_status,
        pralResponse=row.pral_response,
        localErrors=[LocalError(**e) for e in (row.local_errors or [])],
        errorDescriptions=descriptions_for(row.pral_response),
        attempts=row.attempts or 0,
        receivedAt=received,
        completedAt=completed,
        elapsedMs=row.elapsed_ms or 0,
    )


class SubmissionService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        pral: PralClient,
        gate: ConcurrencyGate,
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.pral = pral
        self.gate = gate

    # ------------------------------------------------------------------ public

    async def get(self, env: FbrEnvironment, erp_invoice_no: str) -> InvoiceEnvelope | None:
        async with self.session_factory() as session:
            row = await session.scalar(
                select(Invoice).where(Invoice.env == env.name, Invoice.erp_invoice_no == erp_invoice_no)
            )
        return envelope_from_row(row, replayed=False) if row else None

    async def submit(self, env: FbrEnvironment, submission: InvoiceSubmission, token: str) -> SubmitResult:
        """Process one submission against ``env``. ``token`` lives only on this call stack and is never stored."""
        received_at = _utcnow()
        t0 = time.perf_counter()
        seller = submission.sellerNtn

        # PRAL calls are serialised per (environment, seller NTN).
        async with self.gate.acquire(f"{env.name}:{seller}"):
            # 3. idempotency, scoped to the environment
            async with self.session_factory() as session:
                row = await session.scalar(
                    select(Invoice).where(
                        Invoice.env == env.name, Invoice.erp_invoice_no == submission.erpInvoiceNo
                    )
                )
                if row is not None and row.status in {s.value for s in FINAL_STATUSES}:
                    log.info("erpInvoiceNo=%s replayed status=%s trackingId=%s",
                             row.erp_invoice_no, row.status, row.tracking_id)
                    return SubmitResult(envelope_from_row(row, replayed=True), 200)

                tracking_id = str(uuid.uuid4())
                if row is None:
                    row = Invoice(env=env.name, erp_invoice_no=submission.erpInvoiceNo)
                    session.add(row)
                row.tracking_id = tracking_id
                row.seller_ntn = seller
                row.callback_ref = submission.callbackRef
                row.status = STATUS_RECEIVED
                row.payload = submission.model_dump(mode="json")
                row.fbr_invoice_no = None
                row.dated = None
                row.pral_http_status = None
                row.pral_response = None
                row.local_errors = []
                row.attempts = row.attempts or 0
                row.received_at = received_at
                row.completed_at = None
                row.elapsed_ms = None
                await session.commit()
                invoice_id = row.id
                prior_attempts = row.attempts

            log.info("env=%s erpInvoiceNo=%s seller=%s trackingId=%s received",
                     env.name, submission.erpInvoiceNo, seller, tracking_id)

            # 4. local pre-validation
            local_errors = prevalidate(submission.invoice)
            if local_errors:
                log.info("trackingId=%s REJECTED_LOCAL %s", tracking_id, [e.field for e in local_errors])
                return await self._finish(
                    invoice_id, SubmissionStatus.REJECTED_LOCAL, received_at, t0,
                    local_errors=local_errors, attempts=[], prior_attempts=prior_attempts,
                )

            # 5. PRAL validate, then post
            payload_json = dumps_pral(prepare_payload(submission.invoice, env))
            attempts: list[AttemptRecord] = []
            status: SubmissionStatus
            result: PralResult | None = None
            try:
                result = await self.pral.validate_invoice(env, payload_json, token, attempts)
                parsed = PralResponse.try_parse(result.body)
                if parsed is None or not parsed.is_success():
                    status = SubmissionStatus.REJECTED_FBR
                else:
                    result = await self.pral.post_invoice(env, payload_json, token, attempts)
                    parsed = PralResponse.try_parse(result.body)
                    if parsed is not None and parsed.is_success() and parsed.invoiceNumber:
                        status = SubmissionStatus.SUBMITTED
                    else:
                        status = SubmissionStatus.REJECTED_FBR
            except AuthError as exc:
                status, result = SubmissionStatus.AUTH_ERROR, exc.result
            except TimeoutAfterSend:
                # PRAL did not answer the post; the validate answer stays in the attempts table only.
                status, result = SubmissionStatus.UNKNOWN, None
            except UpstreamError as exc:
                status, result = SubmissionStatus.FAILED_UPSTREAM, exc.result

            log.info("trackingId=%s %s after %d PRAL call(s)", tracking_id, status.value, len(attempts))
            # 6. persist and answer
            return await self._finish(
                invoice_id, status, received_at, t0,
                local_errors=[], attempts=attempts, prior_attempts=prior_attempts, result=result,
            )

    # ------------------------------------------------------------------ internal

    async def _finish(
        self,
        invoice_id: int,
        status: SubmissionStatus,
        received_at: datetime,
        t0: float,
        *,
        local_errors: list[LocalError],
        attempts: list[AttemptRecord],
        prior_attempts: int,
        result: PralResult | None = None,
    ) -> SubmitResult:
        completed_at = _utcnow()
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        body: Any | None = result.body if result is not None else None
        parsed = PralResponse.try_parse(body)

        async with self.session_factory() as session:
            row = await session.get(Invoice, invoice_id)
            assert row is not None
            row.status = status.value
            row.local_errors = [e.model_dump() for e in local_errors]
            row.pral_http_status = result.http_status if result is not None else None
            row.pral_response = body if isinstance(body, (dict, list)) else None
            row.dated = parsed.dated if parsed else None
            row.fbr_invoice_no = parsed.invoiceNumber if (parsed and status == SubmissionStatus.SUBMITTED) else None
            row.attempts = prior_attempts + len(attempts)
            row.completed_at = completed_at
            row.elapsed_ms = elapsed_ms
            for rec in attempts:
                session.add(
                    Attempt(
                        invoice_id=row.id,
                        tracking_id=row.tracking_id,
                        call=rec.call,
                        attempt_no=rec.attempt_no,
                        url=rec.url,
                        request_body=rec.request_body,
                        response_status=rec.http_status,
                        response_body=rec.response_body if isinstance(rec.response_body, (dict, list)) else None,
                        response_text=rec.response_text,
                        latency_ms=rec.latency_ms,
                        outcome=rec.outcome,
                        error=rec.error,
                        started_at=rec.started_at,
                    )
                )
            await session.commit()
            envelope = envelope_from_row(row, replayed=False)
        return SubmitResult(envelope, envelope.http_status)
