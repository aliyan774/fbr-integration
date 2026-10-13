"""Unit tests for the error catalogue, pre-validation and the concurrency gate."""

from __future__ import annotations

import asyncio
import copy

from fbr_middleware.models.fbr import InvoicePayload
from fbr_middleware.services.concurrency import ConcurrencyGate
from fbr_middleware.services.errors import ERROR_CATALOGUE, describe, descriptions_for
from fbr_middleware.services.prevalidate import prevalidate

from .conftest import POST_HEADER_INVALID, SAMPLE_INVOICE


def test_catalogue_has_spec_codes():
    for code in ("0001", "0008", "0026", "0046", "0052", "0058", "0113", "0300", "0401", "0402"):
        assert code in ERROR_CATALOGUE
    assert "same" in describe("0058")
    assert describe("9999").startswith("Error code not listed")
    assert [d.code for d in descriptions_for(POST_HEADER_INVALID)] == ["0052"]
    assert descriptions_for(None) == [] and descriptions_for({"validationResponse": None}) == []


def test_prevalidate_passes_sample():
    assert prevalidate(InvoicePayload.model_validate(SAMPLE_INVOICE)) == []


def test_prevalidate_catches_rules_on_a_constructed_model():
    inv = InvoicePayload.model_validate(copy.deepcopy(SAMPLE_INVOICE))
    # bypass model validation to exercise the service layer
    inv = inv.model_copy(update={"buyerNTNCNIC": inv.sellerNTNCNIC, "invoiceRefNo": "x"})
    errors = prevalidate(inv)
    codes = {e.code for e in errors}
    assert "L004" in codes and "L007" in codes
    assert all(e.field.startswith("invoice.") for e in errors)


async def test_gate_serialises_same_seller_and_caps_total():
    gate = ConcurrencyGate(max_inflight_total=2, max_inflight_per_seller=1)
    order: list[str] = []

    async def work(seller: str, tag: str, hold: float):
        async with gate.acquire(seller):
            order.append(f"start-{tag}")
            await asyncio.sleep(hold)
            order.append(f"end-{tag}")

    # same seller: second waits for first
    await asyncio.gather(work("A", "a1", 0.05), work("A", "a2", 0.01))
    assert order == ["start-a1", "end-a1", "start-a2", "end-a2"]

    order.clear()
    # different sellers run concurrently, but at most 2 in flight overall
    peak = 0

    async def measured(seller: str):
        nonlocal peak
        async with gate.acquire(seller):
            peak = max(peak, gate.inflight)
            await asyncio.sleep(0.02)

    await asyncio.gather(measured("A"), measured("B"), measured("C"), measured("D"))
    assert peak == 2
    assert gate.inflight == 0
