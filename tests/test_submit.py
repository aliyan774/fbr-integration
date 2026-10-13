"""End-to-end submission outcomes with PRAL mocked by respx."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from fbr_middleware.db.models import Attempt, Invoice

from .conftest import (
    DEV_URL,
    PRD_URL,
    POST_HEADER_INVALID,
    POST_ITEM_INVALID,
    POST_OK,
    TOKEN,
    VALIDATE_ITEM_INVALID,
    VALIDATE_OK,
    auth_headers,
    sample_submission,
    _make_client,
)


async def test_happy_path_returns_201_with_pral_body_verbatim(client, pral, validate_url, post_url):
    v = pral.post(validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_OK))
    p = pral.post(post_url).mock(return_value=httpx.Response(200, json=POST_OK))

    r = await client.post("/api/v1/dev/invoices", json=sample_submission(), headers=auth_headers())

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "SUBMITTED"
    assert body["replayed"] is False
    assert body["fbrInvoiceNo"] == POST_OK["invoiceNumber"]
    assert body["dated"] == POST_OK["dated"]
    assert body["pralHttpStatus"] == 200
    assert body["pralResponse"] == POST_OK
    assert body["errorDescriptions"] == []
    assert body["localErrors"] == []
    assert body["attempts"] == 2
    assert body["callbackRef"] == "1000/2025"
    assert body["erpInvoiceNo"] == "INV-0001"
    assert body["sellerNtn"] == "0786909"
    assert r.headers["X-Tracking-Id"] == body["trackingId"]
    assert body["elapsedMs"] >= 0
    assert body["environment"] == "dev"
    assert set(body) == {
        "trackingId", "environment", "erpInvoiceNo", "sellerNtn", "callbackRef", "status", "replayed", "fbrInvoiceNo",
        "dated", "pralHttpStatus", "pralResponse", "localErrors", "errorDescriptions", "attempts",
        "receivedAt", "completedAt", "elapsedMs",
    }

    # Both PRAL calls carry the SAP-supplied bearer and the same body
    assert v.call_count == 1 and p.call_count == 1
    sent = p.calls.last.request
    assert sent.headers["Authorization"] == f"Bearer {TOKEN}"
    assert sent.headers["Content-Type"] == "application/json"
    sent_text = sent.content.decode()
    sent_json = json.loads(sent_text)
    assert sent_json["scenarioId"] == "SN001"
    assert sent_json["items"][0]["quantity"] == 1.0
    # decimals serialised with 2 places (4 for quantity), optional keys always present
    assert '"quantity": 1.0000' in sent_text
    assert '"valueSalesExcludingST": 1000.00' in sent_text
    assert '"fixedNotifiedValueOrRetailPrice": 0.00' in sent_text
    for key in ("extraTax", "furtherTax", "fedPayable", "discount", "sroScheduleNo", "sroItemSerialNo"):
        assert key in sent_json["items"][0]
    assert v.calls.last.request.content == sent.content


async def test_item_level_rejection_on_validate_returns_422(client, pral, validate_url, post_url):
    pral.post(validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_ITEM_INVALID))
    p = pral.post(post_url).mock(return_value=httpx.Response(200, json=POST_OK))

    r = await client.post("/api/v1/dev/invoices", json=sample_submission(), headers=auth_headers())

    assert r.status_code == 422, r.text
    body = r.json()
    assert body["status"] == "REJECTED_FBR"
    assert body["fbrInvoiceNo"] is None
    assert body["pralResponse"] == VALIDATE_ITEM_INVALID
    assert body["attempts"] == 1
    assert p.call_count == 0  # post never attempted after a failed validate
    assert body["errorDescriptions"] == [
        {"code": "0046", "description": "Rate cannot be empty, please provide valid rate as per selected Sales Type."}
    ]


async def test_header_level_rejection_on_post_returns_422(client, pral, validate_url, post_url):
    pral.post(validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_OK))
    pral.post(post_url).mock(return_value=httpx.Response(200, json=POST_HEADER_INVALID))

    r = await client.post("/api/v1/dev/invoices", json=sample_submission(), headers=auth_headers())

    assert r.status_code == 422, r.text
    body = r.json()
    assert body["status"] == "REJECTED_FBR"
    assert body["pralResponse"] == POST_HEADER_INVALID
    assert body["attempts"] == 2
    assert [e["code"] for e in body["errorDescriptions"]] == ["0052"]
    assert "HS Code" in body["errorDescriptions"][0]["description"]


async def test_item_level_rejection_on_post(client, pral, validate_url, post_url):
    pral.post(validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_OK))
    pral.post(post_url).mock(return_value=httpx.Response(200, json=POST_ITEM_INVALID))

    r = await client.post("/api/v1/dev/invoices", json=sample_submission(), headers=auth_headers())
    assert r.status_code == 422
    assert r.json()["status"] == "REJECTED_FBR"
    assert r.json()["pralResponse"]["validationResponse"]["status"] == "invalid"  # verbatim, lowercase kept
    assert [e["code"] for e in r.json()["errorDescriptions"]] == ["0046"]


async def test_post_timeout_returns_504_unknown_and_is_stored(app_and_client, pral, validate_url, post_url):
    client, app = app_and_client
    pral.post(validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_OK))
    p = pral.post(post_url).mock(side_effect=httpx.ReadTimeout("no answer"))

    r = await client.post("/api/v1/dev/invoices", json=sample_submission("INV-TO"), headers=auth_headers())

    assert r.status_code == 504, r.text
    body = r.json()
    assert body["status"] == "UNKNOWN"
    assert body["pralResponse"] is None and body["pralHttpStatus"] is None
    assert body["attempts"] == 2
    assert p.call_count == 1  # never blind-retried
    assert app.state.pral_client.sleep.calls == []

    # stored for reconciliation; GET returns it
    g = await client.get("/api/v1/dev/invoices/INV-TO", headers={"X-API-Key": "test-api-key"})
    assert g.status_code == 200
    assert g.json()["status"] == "UNKNOWN"
    assert g.json()["trackingId"] == body["trackingId"]

    # a resubmit of an UNKNOWN is replayed, never re-posted
    r2 = await client.post("/api/v1/dev/invoices", json=sample_submission("INV-TO"), headers=auth_headers())
    assert r2.status_code == 200 and r2.json()["replayed"] is True
    assert p.call_count == 1


async def test_validate_timeout_is_retried_then_failed_upstream(app_and_client, pral, validate_url, post_url):
    client, app = app_and_client
    v = pral.post(validate_url).mock(side_effect=httpx.ReadTimeout("slow"))
    p = pral.post(post_url).mock(return_value=httpx.Response(200, json=POST_OK))

    r = await client.post("/api/v1/dev/invoices", json=sample_submission(), headers=auth_headers())
    assert r.status_code == 502
    assert r.json()["status"] == "FAILED_UPSTREAM"
    assert v.call_count == 3 and p.call_count == 0
    assert app.state.pral_client.sleep.calls == [2.0, 4.0]


async def test_pral_401_returns_502_auth_error_without_retry(client, pral, validate_url, post_url):
    v = pral.post(validate_url).mock(return_value=httpx.Response(401, json={"message": "Unauthorized"}))
    p = pral.post(post_url).mock(return_value=httpx.Response(200, json=POST_OK))

    r = await client.post("/api/v1/dev/invoices", json=sample_submission(), headers=auth_headers())

    assert r.status_code == 502, r.text
    body = r.json()
    assert body["status"] == "AUTH_ERROR"
    assert body["pralHttpStatus"] == 401
    assert body["attempts"] == 1
    assert v.call_count == 1 and p.call_count == 0


async def test_pral_error_0401_in_body_is_auth_error(client, pral, validate_url):
    body_0401 = {
        "dated": "2025-05-13 13:09:05",
        "validationResponse": {
            "statusCode": "01", "status": "Invalid", "errorCode": "0401",
            "error": "The provided seller NTN/CNIC does not have a valid or authorized access token",
            "invoiceStatuses": None,
        },
    }
    pral.post(validate_url).mock(return_value=httpx.Response(200, json=body_0401))
    r = await client.post("/api/v1/dev/invoices", json=sample_submission(), headers=auth_headers())
    assert r.status_code == 502
    assert r.json()["status"] == "AUTH_ERROR"
    assert r.json()["pralResponse"] == body_0401
    assert r.json()["errorDescriptions"][0]["code"] == "0401"


async def test_pral_503_three_times_returns_502_failed_upstream_after_backoff(
    app_and_client, pral, validate_url, post_url
):
    client, app = app_and_client
    v = pral.post(validate_url).mock(return_value=httpx.Response(503, text="Service Unavailable"))
    p = pral.post(post_url).mock(return_value=httpx.Response(200, json=POST_OK))

    r = await client.post("/api/v1/dev/invoices", json=sample_submission(), headers=auth_headers())

    assert r.status_code == 502, r.text
    body = r.json()
    assert body["status"] == "FAILED_UPSTREAM"
    assert body["pralHttpStatus"] == 503
    assert body["pralResponse"] is None
    assert body["attempts"] == 3
    assert v.call_count == 3 and p.call_count == 0
    assert app.state.pral_client.sleep.calls == [2.0, 4.0]


async def test_5xx_on_post_recovers_on_retry(app_and_client, pral, validate_url, post_url):
    client, app = app_and_client
    pral.post(validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_OK))
    p = pral.post(post_url).mock(
        side_effect=[httpx.Response(502, text="bad gateway"), httpx.Response(200, json=POST_OK)]
    )
    r = await client.post("/api/v1/dev/invoices", json=sample_submission(), headers=auth_headers())
    assert r.status_code == 201
    assert r.json()["attempts"] == 3
    assert p.call_count == 2
    assert app.state.pral_client.sleep.calls == [2.0]


async def test_idempotent_resubmit_replays_without_calling_pral(client, pral, validate_url, post_url):
    v = pral.post(validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_OK))
    p = pral.post(post_url).mock(return_value=httpx.Response(200, json=POST_OK))

    first = await client.post("/api/v1/dev/invoices", json=sample_submission(), headers=auth_headers())
    assert first.status_code == 201
    second = await client.post("/api/v1/dev/invoices", json=sample_submission(), headers=auth_headers())

    assert second.status_code == 200, second.text
    body = second.json()
    assert body["replayed"] is True
    assert body["status"] == "SUBMITTED"
    assert body["fbrInvoiceNo"] == POST_OK["invoiceNumber"]
    assert body["trackingId"] == first.json()["trackingId"]
    assert second.headers["X-Tracking-Id"] == body["trackingId"]
    assert v.call_count == 1 and p.call_count == 1


async def test_rejected_invoice_is_reprocessed_on_resubmit(client, pral, validate_url, post_url):
    v = pral.post(validate_url).mock(
        side_effect=[httpx.Response(200, json=VALIDATE_ITEM_INVALID), httpx.Response(200, json=VALIDATE_OK)]
    )
    pral.post(post_url).mock(return_value=httpx.Response(200, json=POST_OK))

    first = await client.post("/api/v1/dev/invoices", json=sample_submission(), headers=auth_headers())
    assert first.status_code == 422 and first.json()["status"] == "REJECTED_FBR"
    second = await client.post("/api/v1/dev/invoices", json=sample_submission(), headers=auth_headers())
    assert second.status_code == 201 and second.json()["status"] == "SUBMITTED"
    assert second.json()["replayed"] is False
    assert second.json()["trackingId"] != first.json()["trackingId"]
    assert second.json()["attempts"] == 3  # cumulative over both submissions
    assert v.call_count == 2


async def test_prd_path_strips_scenario_id_and_uses_plain_urls(client, pral, prd_env, validate_url, post_url):
    assert prd_env.validate_url.endswith("/validateinvoicedata")
    assert prd_env.post_url.endswith("/postinvoicedata")
    assert validate_url.endswith("/validateinvoicedata_sb")
    v = pral.post(prd_env.validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_OK))
    p = pral.post(prd_env.post_url).mock(return_value=httpx.Response(200, json=POST_OK))
    dev_v = pral.post(validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_OK))

    r = await client.post(PRD_URL, json=sample_submission(), headers=auth_headers())
    assert r.status_code == 201, r.text
    assert r.json()["environment"] == "prd"
    sent = json.loads(p.calls.last.request.content)
    assert "scenarioId" not in sent
    assert "scenarioId" not in json.loads(v.calls.last.request.content)
    assert dev_v.call_count == 0  # sandbox URLs untouched

    # scenarioId is optional for prd
    body = sample_submission("INV-PRD-2")
    body["invoice"].pop("scenarioId")
    r = await client.post(PRD_URL, json=body, headers=auth_headers())
    assert r.status_code == 201, r.text


async def test_dev_path_requires_scenario_id_but_prd_does_not(client, pral, prd_env):
    pral.post(prd_env.validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_OK))
    pral.post(prd_env.post_url).mock(return_value=httpx.Response(200, json=POST_OK))
    body = sample_submission("INV-ENV")
    body["invoice"]["scenarioId"] = ""
    r_dev = await client.post(DEV_URL, json=body, headers=auth_headers())
    assert r_dev.status_code == 422 and r_dev.json()["detail"][0]["field"] == "invoice.scenarioId"
    r_prd = await client.post(PRD_URL, json=body, headers=auth_headers())
    assert r_prd.status_code == 201


async def test_unknown_environment_segment_is_404(client, pral):
    r = await client.post("/api/v1/qa/invoices", json=sample_submission(), headers=auth_headers())
    assert r.status_code == 404
    assert "qa" in r.json()["detail"] and "dev" in r.json()["detail"]
    assert (await client.get("/api/v1/qa/invoices/INV-0001", headers={"X-API-Key": "test-api-key"})).status_code == 404
    assert len(pral.calls) == 0


async def test_disabled_environment_is_404(env, monkeypatch, pral):
    from fbr_middleware.config import reset_settings_cache

    monkeypatch.setenv("FBR_ENABLED_ENVS", "dev")
    reset_settings_cache()
    async for c, _app in _make_client(env):
        r = await c.post(PRD_URL, json=sample_submission(), headers=auth_headers())
        assert r.status_code == 404
        assert "enabled" in r.json()["detail"]
        h = await c.get("/health")
        assert list(h.json()["environments"]) == ["dev"]


async def test_same_erp_invoice_no_is_independent_per_environment(client, pral, prd_env, validate_url, post_url):
    pral.post(validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_OK))
    pral.post(post_url).mock(return_value=httpx.Response(200, json=POST_OK))
    prd_v = pral.post(prd_env.validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_OK))
    prd_p = pral.post(prd_env.post_url).mock(return_value=httpx.Response(200, json=POST_OK))

    dev = await client.post(DEV_URL, json=sample_submission("INV-BOTH"), headers=auth_headers())
    assert dev.status_code == 201 and dev.json()["environment"] == "dev"
    prd = await client.post(PRD_URL, json=sample_submission("INV-BOTH"), headers=auth_headers())
    assert prd.status_code == 201 and prd.json()["replayed"] is False  # not a replay of the dev one
    assert prd.json()["environment"] == "prd"
    assert prd.json()["trackingId"] != dev.json()["trackingId"]
    assert prd_v.call_count == 1 and prd_p.call_count == 1

    g_dev = await client.get(f"{DEV_URL}/INV-BOTH", headers={"X-API-Key": "test-api-key"})
    g_prd = await client.get(f"{PRD_URL}/INV-BOTH", headers={"X-API-Key": "test-api-key"})
    assert g_dev.json()["trackingId"] == dev.json()["trackingId"]
    assert g_prd.json()["trackingId"] == prd.json()["trackingId"]


async def test_rows_persisted_without_token(app_and_client, pral, validate_url, post_url):
    client, app = app_and_client
    pral.post(validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_OK))
    pral.post(post_url).mock(return_value=httpx.Response(200, json=POST_OK))
    r = await client.post("/api/v1/dev/invoices", json=sample_submission("INV-DB"), headers=auth_headers())
    assert r.status_code == 201

    async with app.state.session_factory() as session:
        inv = await session.scalar(select(Invoice).where(Invoice.erp_invoice_no == "INV-DB"))
        attempts = (await session.scalars(select(Attempt).where(Attempt.invoice_id == inv.id))).all()

    assert inv.status == "SUBMITTED"
    assert inv.fbr_invoice_no == POST_OK["invoiceNumber"]
    assert inv.pral_http_status == 200 and inv.pral_response == POST_OK
    assert inv.attempts == 2 and len(attempts) == 2
    assert [a.call for a in attempts] == ["validate", "post"]
    assert all(a.outcome == "ok" for a in attempts)
    assert all(a.response_status == 200 for a in attempts)
    assert json.loads(attempts[0].request_body)["sellerNTNCNIC"] == "0786909"
    # no token in any column of any row
    all_values = json.dumps(
        [{c.name: str(getattr(inv, c.name)) for c in Invoice.__table__.columns}]
        + [{c.name: str(getattr(a, c.name)) for c in Attempt.__table__.columns} for a in attempts]
    )
    assert TOKEN not in all_values
    assert "Authorization" not in all_values
    assert not any(c.name.lower() in {"token", "authorization", "bearer"} for c in Invoice.__table__.columns)
    assert not any(c.name.lower() in {"token", "authorization", "bearer"} for c in Attempt.__table__.columns)


@pytest.mark.parametrize("bad_status", [400, 404])
async def test_unexpected_4xx_is_failed_upstream_without_retry(client, pral, validate_url, bad_status):
    v = pral.post(validate_url).mock(return_value=httpx.Response(bad_status, text="nope"))
    r = await client.post("/api/v1/dev/invoices", json=sample_submission(), headers=auth_headers())
    assert r.status_code == 502
    assert r.json()["status"] == "FAILED_UPSTREAM"
    assert r.json()["pralHttpStatus"] == bad_status
    assert v.call_count == 1

