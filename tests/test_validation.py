"""Shape and conditional rules: 422 naming the field, nothing stored."""

from __future__ import annotations

import copy
from datetime import date, timedelta
from decimal import Decimal

import pytest

from fbr_middleware.models.fbr import InvoiceItem, InvoicePayload, PralResponse, dumps_pral

from .conftest import (
    POST_HEADER_INVALID,
    POST_ITEM_INVALID,
    POST_OK,
    SAMPLE_INVOICE,
    VALIDATE_ITEM_INVALID,
    VALIDATE_OK,
    auth_headers,
    sample_submission,
)


def _mutate(**changes):
    body = sample_submission("INV-BAD")
    inv = body["invoice"]
    for k, v in changes.items():
        if k.startswith("item."):
            inv["items"][0][k[5:]] = v
        elif v is None:
            inv.pop(k, None)
        else:
            inv[k] = v
    return body


CASES = {
    "registered_without_buyer_ntn": (_mutate(buyerRegistrationType="Registered", buyerNTNCNIC=""), "buyerNTNCNIC"),
    "registered_missing_buyer_ntn_key": (_mutate(buyerRegistrationType="Registered", buyerNTNCNIC=None), "buyerNTNCNIC"),
    "debit_note_without_ref": (_mutate(invoiceType="Debit Note", invoiceRefNo=""), "invoiceRefNo"),
    "debit_note_ref_wrong_length": (_mutate(invoiceType="Debit Note", invoiceRefNo="7000007DI1747"), "invoiceRefNo"),
    "sale_invoice_with_ref": (_mutate(invoiceRefNo="7000007DI1747119701593"), "invoiceRefNo"),
    "sandbox_missing_scenario": (_mutate(scenarioId=None), "scenarioId"),
    "sandbox_empty_scenario": (_mutate(scenarioId=""), "scenarioId"),
    "bad_scenario_id": (_mutate(scenarioId="SN099"), "scenarioId"),
    "buyer_equals_seller": (_mutate(buyerNTNCNIC="0786909"), "buyerNTNCNIC"),
    "buyer_ntn_bad_length": (_mutate(buyerNTNCNIC="12345"), "buyerNTNCNIC"),
    "seller_ntn_bad_length": (_mutate(sellerNTNCNIC="123"), "sellerNTNCNIC"),
    "withheld_tax_mismatch": (_mutate(**{"item.salesTaxWithheldAtSource": 50.00}), "salesTaxWithheldAtSource"),
    "bad_hs_code": (_mutate(**{"item.hsCode": "01012100"}), "hsCode"),
    "quantity_zero": (_mutate(**{"item.quantity": 0}), "quantity"),
    "negative_value": (_mutate(**{"item.valueSalesExcludingST": -1}), "valueSalesExcludingST"),
    "missing_required_item_field": (_mutate(**{"item.rate": None}), "rate"),
    "invalid_invoice_type": (_mutate(invoiceType="Credit Note"), "invoiceType"),
    "invalid_registration_type": (_mutate(buyerRegistrationType="Reg"), "buyerRegistrationType"),
    "date_wrong_format": (_mutate(invoiceDate="21-04-2025"), "invoiceDate"),
    "date_in_future": (_mutate(invoiceDate=(date.today() + timedelta(days=1)).isoformat()), "invoiceDate"),
    "blank_seller_name": (_mutate(sellerBusinessName="   "), "sellerBusinessName"),
    "no_items": (_mutate(items=[]), "items"),
    "unknown_field": (_mutate(buyeRegistrationType="Registered"), "buyeRegistrationType"),
}


@pytest.mark.parametrize("name", list(CASES))
async def test_conditional_rule_returns_422_naming_field_and_stores_nothing(client, pral, name):
    body, field = CASES[name]
    if name == "missing_required_item_field":
        body["invoice"]["items"][0].pop("rate")

    r = await client.post("/api/v1/dev/invoices", json=body, headers=auth_headers())

    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert any(d["field"].endswith(field) for d in detail), detail
    assert "status" not in r.json()  # no envelope: nothing was processed
    # nothing stored
    g = await client.get("/api/v1/dev/invoices/INV-BAD", headers={"X-API-Key": "test-api-key"})
    assert g.status_code == 404
    assert len(pral.calls) == 0


async def test_seller_ntn_must_match_invoice(client, pral):
    body = sample_submission("INV-MM", sellerNtn="1234567")
    r = await client.post("/api/v1/dev/invoices", json=body, headers=auth_headers())
    assert r.status_code == 422
    assert any(d["field"] == "sellerNtn" for d in r.json()["detail"])


async def test_envelope_field_limits(client, pral):
    r = await client.post("/api/v1/dev/invoices", json=sample_submission("X" * 51), headers=auth_headers())
    assert r.status_code == 422 and any(d["field"] == "erpInvoiceNo" for d in r.json()["detail"])
    r = await client.post("/api/v1/dev/invoices", json=sample_submission(callbackRef="c" * 101), headers=auth_headers())
    assert r.status_code == 422 and any(d["field"] == "callbackRef" for d in r.json()["detail"])


async def test_unregistered_buyer_without_ntn_is_accepted_by_model():
    inv = copy.deepcopy(SAMPLE_INVOICE)
    inv["buyerRegistrationType"] = "Unregistered"
    inv.pop("buyerNTNCNIC")
    m = InvoicePayload.model_validate(inv)
    assert m.buyerNTNCNIC == ""
    assert m.to_pral_dict()["buyerNTNCNIC"] == ""


# ----------------------------------------------------------------------------- serialisation


def test_decimal_serialisation_two_places_four_for_quantity():
    item = InvoiceItem(
        hsCode="0101.2100", productDescription="x", rate="18%", uoM="u", saleType="s",
        quantity=Decimal("2.5"), totalValues=Decimal("1180"), valueSalesExcludingST=Decimal("1000"),
        fixedNotifiedValueOrRetailPrice=Decimal("0"), salesTaxApplicable=Decimal("180"),
        salesTaxWithheldAtSource=Decimal("180"),
    )
    text = dumps_pral(item.to_pral_dict())
    assert '"quantity": 2.5000' in text
    assert '"totalValues": 1180.00' in text
    assert '"salesTaxWithheldAtSource": 180.00' in text
    assert '"extraTax": 0.00' in text and '"discount": 0.00' in text
    assert '"sroScheduleNo": ""' in text and '"sroItemSerialNo": ""' in text
    assert "__DEC__" not in text


def test_withheld_equal_to_applicable_is_valid():
    inv = copy.deepcopy(SAMPLE_INVOICE)
    inv["items"][0]["salesTaxWithheldAtSource"] = 180.00
    InvoicePayload.model_validate(inv)


def test_scenario_id_required_when_validated_with_sandbox_context():
    inv = copy.deepcopy(SAMPLE_INVOICE)
    inv["scenarioId"] = ""
    InvoicePayload.model_validate(inv)  # no context: allowed (production)
    with pytest.raises(ValueError):
        InvoicePayload.model_validate(inv, context={"sandbox": True})


# ----------------------------------------------------------------------------- PRAL response rule


@pytest.mark.parametrize(
    "body, ok, codes",
    [
        (VALIDATE_OK, True, []),
        (POST_OK, True, []),
        (VALIDATE_ITEM_INVALID, False, ["0046"]),
        (POST_HEADER_INVALID, False, ["0052"]),
        (POST_ITEM_INVALID, False, ["0046"]),
    ],
)
def test_is_success_header_and_every_item(body, ok, codes):
    parsed = PralResponse.try_parse(body)
    assert parsed is not None
    assert parsed.is_success() is ok
    assert parsed.error_codes() == codes


def test_is_success_ignores_status_text_and_needs_every_item():
    body = {
        "validationResponse": {
            "statusCode": "00", "status": "Valid", "errorCode": None, "error": "",
            "invoiceStatuses": [
                {"itemSNo": "1", "statusCode": "00", "status": "Valid"},
                {"itemSNo": "2", "statusCode": "01", "status": "Valid", "errorCode": "0099"},
            ],
        }
    }
    parsed = PralResponse.try_parse(body)
    assert parsed.is_success() is False
    assert parsed.error_codes() == ["0099"]
