"""POST /api/v1/{env}/invoices/validate: dry run, never posts, never stores."""

from __future__ import annotations

import httpx

from .conftest import API_KEY, DEV_URL, POST_OK, PRD_URL, VALIDATE_ITEM_INVALID, VALIDATE_OK, auth_headers, sample_submission


async def test_validate_only_returns_200_validated_and_stores_nothing(client, pral, validate_url, post_url):
    v = pral.post(validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_OK))
    p = pral.post(post_url).mock(return_value=httpx.Response(200, json=POST_OK))

    r = await client.post(f"{DEV_URL}/validate", json=sample_submission("INV-DRY"), headers=auth_headers())

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "VALIDATED"
    assert body["fbrInvoiceNo"] is None
    assert body["pralResponse"] == VALIDATE_OK
    assert body["attempts"] == 1
    assert body["environment"] == "dev"
    assert r.headers["X-Tracking-Id"] == body["trackingId"]
    assert v.call_count == 1 and p.call_count == 0
    # nothing stored: lookup is 404 and a real submit afterwards is not a replay
    g = await client.get(f"{DEV_URL}/INV-DRY", headers={"X-API-Key": API_KEY})
    assert g.status_code == 404
    s = await client.post(DEV_URL, json=sample_submission("INV-DRY"), headers=auth_headers())
    assert s.status_code == 201 and s.json()["replayed"] is False


async def test_validate_only_rejected_fbr(client, pral, validate_url, post_url):
    pral.post(validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_ITEM_INVALID))
    p = pral.post(post_url).mock(return_value=httpx.Response(200, json=POST_OK))
    r = await client.post(f"{DEV_URL}/validate", json=sample_submission("INV-DRY2"), headers=auth_headers())
    assert r.status_code == 422
    assert r.json()["status"] == "REJECTED_FBR"
    assert r.json()["errorDescriptions"][0]["code"] == "0046"
    assert p.call_count == 0
    assert (await client.get(f"{DEV_URL}/INV-DRY2", headers={"X-API-Key": API_KEY})).status_code == 404


async def test_validate_only_auth_and_shape(client, pral, validate_url):
    pral.post(validate_url).mock(return_value=httpx.Response(401, json={"message": "Unauthorized"}))
    r = await client.post(f"{DEV_URL}/validate", json=sample_submission(), headers=auth_headers())
    assert r.status_code == 502 and r.json()["status"] == "AUTH_ERROR"
    r = await client.post(f"{DEV_URL}/validate", json=sample_submission(), headers={"X-API-Key": API_KEY})
    assert r.status_code == 401
    bad = sample_submission()
    bad["invoice"]["items"][0]["hsCode"] = "bad"
    r = await client.post(f"{DEV_URL}/validate", json=bad, headers=auth_headers())
    assert r.status_code == 422 and "hsCode" in r.text
    assert len(pral.calls) == 1  # only the first call reached PRAL


async def test_validate_only_prd_strips_scenario_id(client, pral, prd_env):
    v = pral.post(prd_env.validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_OK))
    body = sample_submission("INV-DRY-PRD")
    body["invoice"].pop("scenarioId")
    r = await client.post(f"{PRD_URL}/validate", json=body, headers=auth_headers())
    assert r.status_code == 200 and r.json()["status"] == "VALIDATED"
    assert b"scenarioId" not in v.calls.last.request.content
