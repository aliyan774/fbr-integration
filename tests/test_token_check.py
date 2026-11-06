"""GET /api/v1/{env}/token-check: verifies the PRAL token via the reference API, stores nothing."""

from __future__ import annotations

import httpx

from .conftest import API_KEY, PRAL_BASE, TOKEN, auth_headers

PROVINCES = [
    {"stateProvinceCode": 2, "stateProvinceDesc": "BALOCHISTAN"},
    {"stateProvinceCode": 7, "stateProvinceDesc": "PUNJAB"},
    {"stateProvinceCode": 8, "stateProvinceDesc": "SINDH"},
]
FAULT = {"fault": {"code": 900901, "message": "Invalid Credentials", "description": "Access failure for API: /pdi/v1"}}


async def test_token_ok(client, pral):
    route = pral.get(f"{PRAL_BASE}/pdi/v1/provinces").mock(return_value=httpx.Response(200, json=PROVINCES))
    r = await client.get("/api/v1/dev/token-check", headers=auth_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tokenStatus"] == "OK" and body["environment"] == "dev" and body["pralHttpStatus"] == 200
    assert body["provinces"] == ["BALOCHISTAN", "PUNJAB", "SINDH"]
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert route.call_count == 1


async def test_token_rejected(client, pral):
    pral.get(f"{PRAL_BASE}/pdi/v1/provinces").mock(return_value=httpx.Response(401, json=FAULT))
    r = await client.get("/api/v1/dev/token-check", headers=auth_headers())
    assert r.status_code == 502
    assert r.json()["tokenStatus"] == "AUTH_ERROR"
    assert r.json()["pralResponse"] == FAULT


async def test_token_check_upstream_down(client, pral):
    pral.get(f"{PRAL_BASE}/pdi/v1/provinces").mock(side_effect=httpx.ConnectError("down"))
    r = await client.get("/api/v1/dev/token-check", headers=auth_headers())
    assert r.status_code == 502 and r.json()["tokenStatus"] == "FAILED_UPSTREAM"


async def test_token_check_needs_both_headers_and_known_env(client, pral):
    assert (await client.get("/api/v1/dev/token-check", headers={"X-API-Key": API_KEY})).status_code == 401
    assert (await client.get("/api/v1/dev/token-check", headers={"Authorization": f"Bearer {TOKEN}"})).status_code == 401
    assert (await client.get("/api/v1/qa/token-check", headers=auth_headers())).status_code == 404
    assert len(pral.calls) == 0
