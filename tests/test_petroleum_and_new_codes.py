"""Findings from PRL's first real sandbox call (billing document 61000009, 2026-11-23).

- PRAL requires an item field ``petroleumLevyOn`` for sale type "Petroleum Products"; it is not in
  spec v1.12, so the middleware used to reject it as an unknown key.
- PRAL sends error codes newer than the v1.12 catalogue (0129, 0204) and errors with no code.
"""

from __future__ import annotations

import copy
import json

import httpx

from fbr_middleware.models.fbr import InvoicePayload, dumps_pral
from fbr_middleware.services.errors import descriptions_for

from .conftest import DEV_URL, SAMPLE_INVOICE, VALIDATE_OK, auth_headers, sample_submission

PRAL_0129 = {
    "dated": "2026-11-23 11:20:55",
    "validationResponse": {
        "statusCode": "01", "status": "Invalid", "error": "",
        "invoiceStatuses": [{
            "itemSNo": "1", "statusCode": "01", "status": "Invalid", "invoiceNo": 0, "errorCode": "0129",
            "error": "Petroleum Levy On field cannot be empty or null. Please provide a valid Petroleum Levy On value to proceed.",
        }],
    },
    "sourceInvoiceNo": "",
}
PRAL_NO_CODE = {
    "validationResponse": {
        "statusCode": "01", "status": "Invalid", "error": "",
        "invoiceStatuses": [{"itemSNo": "1", "statusCode": "01", "status": "Invalid", "errorCode": "",
                             "error": "Please Provide Petroleum Levy on"}],
    },
}


def test_unknown_code_uses_prals_own_text():
    d = descriptions_for(PRAL_0129)
    assert [(e.code, e.description) for e in d] == [
        ("0129", "Petroleum Levy On field cannot be empty or null. Please provide a valid Petroleum Levy On value to proceed.")
    ]


def test_error_without_code_is_still_described():
    d = descriptions_for(PRAL_NO_CODE)
    assert [(e.code, e.description) for e in d] == [("", "Please Provide Petroleum Levy on")]


def test_known_code_keeps_catalogue_text():
    body = {"validationResponse": {"statusCode": "01", "errorCode": "0052", "error": "Provide proper HS Code with invoice no. null"}}
    assert descriptions_for(body)[0].description.startswith("HS Code that does not match")


def test_petroleum_levy_on_is_optional_and_forwarded_verbatim():
    inv = copy.deepcopy(SAMPLE_INVOICE)
    assert "petroleumLevyOn" not in InvoicePayload.model_validate(inv).to_pral_dict()["items"][0]  # absent unless sent

    inv["items"][0]["petroleumLevyOn"] = 192083
    text = dumps_pral(InvoicePayload.model_validate(inv).to_pral_dict())
    assert json.loads(text)["items"][0]["petroleumLevyOn"] == 192083

    inv["items"][0]["petroleumLevyOn"] = "Quantity"
    assert json.loads(dumps_pral(InvoicePayload.model_validate(inv).to_pral_dict()))["items"][0]["petroleumLevyOn"] == "Quantity"

    inv["items"][0]["petroleumLevyOn"] = 1250.75
    assert '"petroleumLevyOn": 1250.75' in dumps_pral(InvoicePayload.model_validate(inv).to_pral_dict())


async def test_petroleum_levy_on_reaches_pral_through_the_api(client, pral, validate_url):
    route = pral.post(validate_url).mock(return_value=httpx.Response(200, json=VALIDATE_OK))
    body = sample_submission("0061000009")
    body["invoice"]["items"][0]["petroleumLevyOn"] = 192083
    r = await client.post(f"{DEV_URL}/validate", json=body, headers=auth_headers())
    assert r.status_code == 200, r.text  # no longer a 422 "extra field" rejection
    assert json.loads(route.calls.last.request.content)["items"][0]["petroleumLevyOn"] == 192083


async def test_envelope_carries_prals_text_for_0129(client, pral, validate_url):
    pral.post(validate_url).mock(return_value=httpx.Response(200, json=PRAL_0129))
    r = await client.post(f"{DEV_URL}/validate", json=sample_submission("0061000009"), headers=auth_headers())
    assert r.status_code == 422 and r.json()["status"] == "REJECTED_FBR"
    assert r.json()["errorDescriptions"][0]["code"] == "0129"
    assert "Petroleum Levy On" in r.json()["errorDescriptions"][0]["description"]
