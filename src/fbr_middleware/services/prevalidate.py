"""Local business-rule pre-validation and payload preparation.

Most of these rules are already enforced by the Pydantic models (422, nothing stored). They are
repeated here as the REJECTED_LOCAL layer so the service stays correct if the models are relaxed
or the service is called with a payload built elsewhere.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..config import FbrEnvironment
from ..models.api import LocalError
from ..models.fbr import (
    BuyerRegistrationType,
    InvoicePayload,
    InvoiceType,
    is_ntn_or_cnic,
    parse_invoice_date,
)


def prevalidate(invoice: InvoicePayload) -> list[LocalError]:
    errors: list[LocalError] = []

    def err(field: str, code: str, message: str) -> None:
        errors.append(LocalError(field=field, code=code, message=message))

    # invoiceDate: YYYY-MM-DD, not in the future
    try:
        d = parse_invoice_date(invoice.invoiceDate)
    except ValueError:
        err("invoice.invoiceDate", "L001", "invoiceDate must be in YYYY-MM-DD format")
    else:
        if d > date.today():
            err("invoice.invoiceDate", "L002", "invoiceDate must not be in the future")

    # NTN / CNIC: 7 or 13 digits
    if not is_ntn_or_cnic(invoice.sellerNTNCNIC):
        err("invoice.sellerNTNCNIC", "L003", "sellerNTNCNIC must be a 7-digit NTN or 13-digit CNIC")
    buyer = (invoice.buyerNTNCNIC or "").strip()
    if invoice.buyerRegistrationType == BuyerRegistrationType.REGISTERED and not buyer:
        err("invoice.buyerNTNCNIC", "L009", "buyerNTNCNIC is required when buyerRegistrationType is Registered")
    if buyer and not is_ntn_or_cnic(buyer):
        err("invoice.buyerNTNCNIC", "L003", "buyerNTNCNIC must be a 7-digit NTN or 13-digit CNIC")

    # buyer != seller (PRAL 0058)
    if buyer and buyer == invoice.sellerNTNCNIC:
        err("invoice.buyerNTNCNIC", "L004", "buyerNTNCNIC must differ from sellerNTNCNIC (self-invoicing not allowed)")

    # withheld tax in {0, salesTaxApplicable} (PRAL 0008)
    for i, item in enumerate(invoice.items):
        if item.salesTaxWithheldAtSource != 0 and item.salesTaxWithheldAtSource != item.salesTaxApplicable:
            err(
                f"invoice.items.{i}.salesTaxWithheldAtSource",
                "L005",
                "salesTaxWithheldAtSource must be 0 or equal to salesTaxApplicable",
            )

    # Debit note needs invoiceRefNo; sale invoice must not carry one
    ref = (invoice.invoiceRefNo or "").strip()
    if invoice.invoiceType == InvoiceType.DEBIT_NOTE:
        if not ref:
            err("invoice.invoiceRefNo", "L006", "invoiceRefNo is required for a Debit Note")
        elif len(ref) not in (22, 28):
            err("invoice.invoiceRefNo", "L006", "invoiceRefNo must be 22 or 28 characters (original FBR invoice number)")
    elif ref:
        err("invoice.invoiceRefNo", "L007", "invoiceRefNo must be empty for a Sale Invoice")

    return errors


def environment_errors(invoice: InvoicePayload, env: FbrEnvironment) -> list[LocalError]:
    """Rules that depend on the target environment (sandbox needs scenarioId)."""
    errors: list[LocalError] = []
    if env.is_sandbox and env.send_scenario_id and not (invoice.scenarioId or "").strip():
        errors.append(
            LocalError(
                field="invoice.scenarioId",
                code="L008",
                message="scenarioId (SN001..SN028) is required for the dev (sandbox) environment",
            )
        )
    return errors


def prepare_payload(invoice: InvoicePayload, env: FbrEnvironment) -> dict[str, Any]:
    """PRAL-ready dict. scenarioId is stripped for prd (production)."""
    return invoice.to_pral_dict(include_scenario_id=env.send_scenario_id)
