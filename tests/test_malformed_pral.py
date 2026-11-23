"""PRAL answers some rejections with malformed JSON, and can answer with no JSON at all.

Captured from the sandbox on 2026-11-23: header-level rejections arrive as HTTP 200 with a
trailing comma before the closing brace.
"""

from __future__ import annotations

import httpx

from fbr_middleware.clients.pral import loads_lenient, strip_trailing_commas

from .conftest import API_KEY, DEV_URL, POST_OK, VALIDATE_OK, auth_headers, sample_submission

# byte-for-byte what the sandbox sent for an empty invoice
PRAL_MALFORMED = (
    '{\n\t\t\t\t\t\t\t"dated": "2026-11-23 11:12:51",\n\t\t\t\t\t\t\t"validationResponse": '
    '{"statusCode":"01","status":"Invalid","errorCode":"0012","error":"Provided buyer registration type is not valid. '
    'Please refer to relevant reference API in the technical  document for DI API for valid buyer registration types ",'
    '"invoiceStatuses":null},\n\t\t\t\t\t\t\t"sourceInvoiceNo":"",\n\t\t\t\t\t\t\t}'
)


def test_loads_lenient_repairs_pral_trailing_comma():
    body, repaired = loads_lenient(PRAL_MALFORMED)
    assert repaired is True
    assert body["validationResponse"]["errorCode"] == "0012"
    assert body["validationResponse"]["invoiceStatuses"] is None
    assert body["sourceInvoiceNo"] == ""


def test_loads_lenient_leaves_valid_json_and_strings_alone():
    assert loads_lenient('{"a": 1}') == ({"a": 1}, False)
    # commas followed by a brace INSIDE a string must survive
    body, repaired = loads_lenient('{"error": "bad value ,} here", "items": [1, 2, ],}')
    assert repaired is True and body == {"error": "bad value ,} here", "items": [1, 2]}
    assert strip_trailing_commas('{"a": "x\\",}"}') == '{"a": "x\\",}"}'
    assert loads_lenient("") == (None, False)
    assert loads_lenient("<html>Bad Gateway</html>") == (None, False)


async def test_validate_with_malformed_pral_json_still_reports_the_error(client, pral, validate_url):
    pral.post(validate_url).mock(
        return_value=httpx.Response(200, text=PRAL_MALFORMED, headers={"Content-Type": "application/json"})
    )
    r = await client.post(f"{DEV_URL}/validate", json=sample_submission("61000009"), headers=auth_headers())
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["status"] == "REJECTED_FBR"
    assert body["pralResponse"]["validationResponse"]["errorCode"] == "0012"
    assert body["errorDescriptions"] == [
        {"code": "0012", "description": "Buyer Registration type cannot be empty, please provide valid Buyer Registration type"}
    ]
    assert body["pralRawBody"] is None  # it was readable after repair


async def test_submit_with_malformed_pral_json_on_validate(client, pral, validate_url, post_url):
    pral.post(validate_url).mock(return_value=httpx.Response(200, text=PRAL_MALFORMED))
    p = pral.post(post_url).mock(return_value=httpx.Response(200, json=POST_OK))
    r = await client.post(DEV_URL, json=sample_submission("INV-MALF"), headers=auth_headers())
    assert r.status_code == 422 and r.json()["status"] == "REJECTED_FBR"
    assert [e["code"] for e in r.json()["errorDescriptions"]] == ["0012"]
    assert p.call_count == 0
    g = await client.get(f"{DEV_URL}/INV-MALF", headers={"X-API-Key": API_KEY})
    assert g.json()["pralResponse"]["validationResponse"]["errorCode"] == "0012"


async def test_unreadable_200_on_validate_is_failed_upstream_with_raw_body(client, pral, validate_url, post_url):
    pral.post(validate_url).mock(return_value=httpx.Response(200, text="<html>Service temporarily unavailable</html>"))
    p = pral.post(post_url).mock(return_value=httpx.Response(200, json=POST_OK))
    r = await client.post(f"{DEV_URL}/validate", json=sample_submission(), headers=auth_headers())
    assert r.status_code == 502
    body = r.json()
    assert body["status"] == "FAILED_UPSTREAM"  # not REJECTED_FBR: the data was never judged
    assert body["pralResponse"] is None
    assert body["pralRawBody"] == "<html>Service temporarily unavailable</html>"
    assert p.call_count == 0

    r2 = await client.post(DEV_URL, json=sample_submission("INV-HTML"), headers=auth_headers())
    assert r2.status_code == 502 and r2.json()["status"] == "FAILED_UPSTREAM"
    assert "Service temporarily unavailable" in r2.json()["pralRawBody"]


async def test_empty_200_on_validate_is_failed_upstream(client, pral, validate_url):
    pral.post(validate_url).mock(return_value=httpx.Response(200, text=""))
    r = await client.post(f"{DEV_URL}/validate", json=sample_submission(), headers=auth_headers())
    assert r.status_code == 502 and r.json()["status"] == "FAILED_UPSTREAM"
    assert r.json()["pralRawBody"] == ""


async def test_unreadable_200_on_post_is_unknown_never_resubmitted(client, pral, validate_url, post_url):
    pral.post(validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_OK))
    p = pral.post(post_url).mock(return_value=httpx.Response(200, text="garbage: not json"))
    r = await client.post(DEV_URL, json=sample_submission("INV-GARB"), headers=auth_headers())
    assert r.status_code == 504, r.text
    assert r.json()["status"] == "UNKNOWN"  # post was sent; a number may exist at FBR
    assert r.json()["pralRawBody"] == "garbage: not json"
    r2 = await client.post(DEV_URL, json=sample_submission("INV-GARB"), headers=auth_headers())
    assert r2.status_code == 200 and r2.json()["replayed"] is True
    assert p.call_count == 1
