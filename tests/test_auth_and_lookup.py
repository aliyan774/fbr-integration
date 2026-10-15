"""Header authentication, GET lookup, health."""

from __future__ import annotations

import httpx

from .conftest import API_KEY, POST_OK, VALIDATE_OK, auth_headers, sample_submission


async def test_missing_api_key_returns_401_and_stores_nothing(client, pral):
    r = await client.post("/api/v1/dev/invoices", json=sample_submission("INV-NOKEY"),
                          headers={"Authorization": "Bearer tok"})
    assert r.status_code == 401
    assert "X-API-Key" in r.json()["detail"]
    assert len(pral.calls) == 0
    g = await client.get("/api/v1/dev/invoices/INV-NOKEY", headers={"X-API-Key": API_KEY})
    assert g.status_code == 404


async def test_wrong_api_key_returns_401(client, pral):
    r = await client.post("/api/v1/dev/invoices", json=sample_submission(), headers=auth_headers(api_key="nope"))
    assert r.status_code == 401


async def test_second_configured_key_is_accepted(client, pral, validate_url, post_url):
    pral.post(validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_OK))
    pral.post(post_url).mock(return_value=httpx.Response(200, json=POST_OK))
    r = await client.post("/api/v1/dev/invoices", json=sample_submission(), headers=auth_headers(api_key="other-system-key"))
    assert r.status_code == 201


async def test_missing_bearer_returns_401_and_stores_nothing(client, pral):
    r = await client.post("/api/v1/dev/invoices", json=sample_submission("INV-NOTOK"), headers={"X-API-Key": API_KEY})
    assert r.status_code == 401
    assert "Bearer" in r.json()["detail"]
    g = await client.get("/api/v1/dev/invoices/INV-NOTOK", headers={"X-API-Key": API_KEY})
    assert g.status_code == 404


async def test_malformed_authorization_returns_401(client, pral):
    for value in ("Basic abc", "Bearer", "Bearer a b", "token-only"):
        r = await client.post("/api/v1/dev/invoices", json=sample_submission(),
                              headers={"X-API-Key": API_KEY, "Authorization": value})
        assert r.status_code == 401, value


async def test_auth_checked_before_body_shape(client, pral):
    # invalid body AND missing headers -> 401 wins, no 422 detail leaks
    r = await client.post("/api/v1/dev/invoices", json={"garbage": True})
    assert r.status_code == 401


async def test_get_returns_stored_envelope_and_404_for_unknown(client, pral, validate_url, post_url):
    pral.post(validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_OK))
    pral.post(post_url).mock(return_value=httpx.Response(200, json=POST_OK))
    posted = await client.post("/api/v1/dev/invoices", json=sample_submission("INV-GET"), headers=auth_headers())
    assert posted.status_code == 201

    g = await client.get("/api/v1/dev/invoices/INV-GET", headers={"X-API-Key": API_KEY})
    assert g.status_code == 200
    body = g.json()
    assert body["status"] == "SUBMITTED"
    assert body["fbrInvoiceNo"] == POST_OK["invoiceNumber"]
    assert body["pralResponse"] == POST_OK
    assert body["trackingId"] == posted.json()["trackingId"]
    assert g.headers["X-Tracking-Id"] == body["trackingId"]
    assert body["replayed"] is False

    assert (await client.get("/api/v1/dev/invoices/does-not-exist", headers={"X-API-Key": API_KEY})).status_code == 404
    assert (await client.get("/api/v1/dev/invoices/INV-GET")).status_code == 401  # GET needs the API key


async def test_root_answers_200_for_sm59_connection_test(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert r.json()["service"] == "fbr-middleware"
    assert r.json()["health"] == "/health"


async def test_health_reports_db_and_inflight(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["inflight"] == 0
    assert set(body["environments"]) == {"dev", "prd"}
    assert body["environments"]["dev"]["submitUrl"] == "/api/v1/dev/invoices"
    assert body["environments"]["dev"]["pralPostUrl"].endswith("/postinvoicedata_sb")
    assert body["environments"]["prd"]["pralPostUrl"].endswith("/postinvoicedata")
    assert body["environments"]["prd"]["sendScenarioId"] is False
    assert body["maxInflightTotal"] == 5
