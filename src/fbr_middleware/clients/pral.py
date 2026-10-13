"""Async httpx client for PRAL validateinvoicedata / postinvoicedata.

Rules (docs/middleware-flow.md section 3):

* Bearer token comes from the caller per call; it is set on the request and never logged or stored.
* HTTP 5xx: retry with 2 s / 4 s / 8 s backoff up to FBR_MAX_RETRIES_5XX attempts -> UpstreamError.
* HTTP 401, or errorCode 0401 / 0402 in a 200 body: AuthError, no retry.
* Timeout or connection drop after the request was sent: validate is retried (it creates nothing),
  post raises TimeoutAfterSend (PRAL may have issued a number; caller marks UNKNOWN, never re-posts).
* The PRAL body is returned untouched together with the HTTP status.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx

from ..config import FbrEnvironment, Settings

log = logging.getLogger(__name__)

AUTH_ERROR_CODES = frozenset({"0401", "0402"})

# Raised before any bytes reached PRAL: safe to retry, even for post.
_BEFORE_SEND_ERRORS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.ProxyError,
    httpx.UnsupportedProtocol,
)


@dataclass
class PralResult:
    call: str
    url: str
    http_status: int
    body: Any | None  # parsed JSON, or None if the body was not JSON
    text: str
    latency_ms: int

    @property
    def validation(self) -> dict[str, Any] | None:
        if isinstance(self.body, dict):
            vr = self.body.get("validationResponse")
            return vr if isinstance(vr, dict) else None
        return None


@dataclass
class AttemptRecord:
    """One HTTP try; persisted by the submission service. Never contains headers."""

    call: str
    attempt_no: int
    url: str
    request_body: str
    started_at: datetime
    latency_ms: int
    outcome: str
    http_status: int | None = None
    response_body: Any | None = None
    response_text: str | None = None
    error: str | None = None


class PralError(Exception):
    def __init__(self, message: str, result: PralResult | None = None):
        super().__init__(message)
        self.result = result


class AuthError(PralError):
    """HTTP 401 or errorCode 0401/0402: token invalid / not bound to this NTN. Never retried."""


class UpstreamError(PralError):
    """5xx after retries, unexpected HTTP status, or connection failure before send."""


class TimeoutAfterSend(PralError):
    """post request left the process but no answer arrived. Caller must mark UNKNOWN."""


SleepFn = Callable[[float], Awaitable[None]]


def _error_codes_in(body: Any) -> set[str]:
    codes: set[str] = set()
    if isinstance(body, dict):
        vr = body.get("validationResponse")
        if isinstance(vr, dict):
            if vr.get("errorCode"):
                codes.add(str(vr["errorCode"]))
            for s in vr.get("invoiceStatuses") or []:
                if isinstance(s, dict) and s.get("errorCode"):
                    codes.add(str(s["errorCode"]))
    return codes


class PralClient:
    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient,
        sleep: SleepFn | None = None,
    ):
        self.settings = settings
        self.http = http
        self.sleep: SleepFn = sleep or asyncio.sleep
        self.max_attempts = max(1, int(settings.FBR_MAX_RETRIES_5XX))

    @staticmethod
    def backoff_seconds(attempt_no: int) -> float:
        """2 s after the 1st failed attempt, 4 s after the 2nd, 8 s after the 3rd ..."""
        return float(2**attempt_no)

    async def validate_invoice(
        self, env: FbrEnvironment, payload_json: str, token: str, attempts: list[AttemptRecord] | None = None
    ) -> PralResult:
        return await self._call("validate", env.validate_url, payload_json, token, attempts)

    async def post_invoice(
        self, env: FbrEnvironment, payload_json: str, token: str, attempts: list[AttemptRecord] | None = None
    ) -> PralResult:
        return await self._call("post", env.post_url, payload_json, token, attempts)

    async def _call(
        self,
        call: str,
        url: str,
        payload_json: str,
        token: str,
        attempts: list[AttemptRecord] | None,
    ) -> PralResult:
        attempts = attempts if attempts is not None else []
        is_post = call == "post"
        body_bytes = payload_json.encode("utf-8")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        last_error: PralError | None = None

        for attempt_no in range(1, self.max_attempts + 1):
            started = datetime.now(timezone.utc)
            t0 = time.perf_counter()
            rec = AttemptRecord(
                call=call, attempt_no=attempt_no, url=url, request_body=payload_json,
                started_at=started, latency_ms=0, outcome="",
            )
            try:
                resp = await self.http.post(url, content=body_bytes, headers=headers)
            except _BEFORE_SEND_ERRORS as exc:
                rec.latency_ms = _ms(t0)
                rec.outcome = "connect_error"
                rec.error = _exc_text(exc)
                attempts.append(rec)
                log.warning("PRAL %s attempt %d/%d connect error: %s", call, attempt_no, self.max_attempts, rec.error)
                last_error = UpstreamError(f"PRAL {call}: connection failed: {rec.error}")
            except httpx.HTTPError as exc:
                # ReadTimeout, WriteTimeout, ReadError, WriteError, RemoteProtocolError, ... : the
                # request may have reached PRAL.
                rec.latency_ms = _ms(t0)
                rec.outcome = "timeout_after_send"
                rec.error = _exc_text(exc)
                attempts.append(rec)
                if is_post:
                    log.error("PRAL post: no answer after send (%s); result UNKNOWN", rec.error)
                    raise TimeoutAfterSend(f"PRAL post: no answer after send: {rec.error}") from exc
                log.warning("PRAL %s attempt %d/%d timed out: %s", call, attempt_no, self.max_attempts, rec.error)
                last_error = UpstreamError(f"PRAL {call}: timed out: {rec.error}")
            else:
                result = _to_result(call, url, resp, _ms(t0))
                rec.latency_ms = result.latency_ms
                rec.http_status = result.http_status
                rec.response_body = result.body
                rec.response_text = None if result.body is not None else result.text[:4000]

                if result.http_status == 401:
                    rec.outcome = "auth_error"
                    attempts.append(rec)
                    raise AuthError("PRAL returned HTTP 401", result)

                if result.http_status >= 500:
                    rec.outcome = "upstream_5xx"
                    attempts.append(rec)
                    log.warning("PRAL %s attempt %d/%d HTTP %d", call, attempt_no, self.max_attempts, result.http_status)
                    last_error = UpstreamError(f"PRAL {call}: HTTP {result.http_status}", result)
                elif result.http_status == 200:
                    if _error_codes_in(result.body) & AUTH_ERROR_CODES:
                        rec.outcome = "auth_error"
                        attempts.append(rec)
                        raise AuthError("PRAL error 0401/0402: token not authorised for this NTN", result)
                    rec.outcome = "ok"
                    attempts.append(rec)
                    return result
                else:
                    rec.outcome = "http_error"
                    attempts.append(rec)
                    raise UpstreamError(f"PRAL {call}: unexpected HTTP {result.http_status}", result)

            if attempt_no < self.max_attempts:
                await self.sleep(self.backoff_seconds(attempt_no))

        assert last_error is not None
        raise last_error


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def _exc_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _to_result(call: str, url: str, resp: httpx.Response, latency_ms: int) -> PralResult:
    text = resp.text
    body: Any | None
    try:
        body = json.loads(text) if text else None
    except ValueError:
        body = None
    return PralResult(call=call, url=url, http_status=resp.status_code, body=body, text=text, latency_ms=latency_ms)
