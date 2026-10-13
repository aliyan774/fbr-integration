"""The PRAL token must never reach logs or the database."""

from __future__ import annotations

import logging

import httpx

from fbr_middleware.logging_setup import RedactAuthorizationFilter, current_log_file, redact

from .conftest import POST_OK, TOKEN, VALIDATE_OK, auth_headers, sample_submission


def test_redact_function_covers_header_shapes():
    assert redact(f"Authorization: Bearer {TOKEN}") == "Authorization: ***REDACTED***"
    assert TOKEN not in redact(f"headers={{'authorization': 'Bearer {TOKEN}', 'x': 1}}")
    assert TOKEN not in redact(f"Authorization={TOKEN}")
    assert TOKEN not in redact(f"sending Bearer {TOKEN} to PRAL")
    assert redact("nothing here") == "nothing here"


def test_filter_rewrites_record_message():
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "auth=%s", (f"Bearer {TOKEN}",), None)
    assert RedactAuthorizationFilter().filter(rec) is True
    assert TOKEN not in rec.getMessage()
    assert "***REDACTED***" in rec.getMessage()


async def test_token_absent_from_log_file_after_submission(client, pral, validate_url, post_url, env):
    pral.post(validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_OK))
    pral.post(post_url).mock(return_value=httpx.Response(200, json=POST_OK))

    log = logging.getLogger("fbr_middleware.test")
    log.info("forwarding Authorization: Bearer %s", TOKEN)  # simulate a careless log line
    log.warning("headers=%s", {"Authorization": f"Bearer {TOKEN}", "X-API-Key": "k"})

    r = await client.post("/api/v1/dev/invoices", json=sample_submission("INV-LOG"), headers=auth_headers())
    assert r.status_code == 201

    for h in logging.getLogger().handlers:
        h.flush()
    log_file = current_log_file()
    assert log_file is not None and log_file.exists()
    text = log_file.read_text(encoding="utf-8")
    assert "INV-LOG" in text  # we did log the submission
    assert TOKEN not in text
    assert "***REDACTED***" in text
    assert TOKEN not in r.text
