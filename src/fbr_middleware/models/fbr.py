"""PRAL Digital Invoicing payload and response models (spec v1.12, sections 4.1 / 4.2).

The field rules mirror the "Request parameters" tables in docs/middleware-flow.md:

* required fields have no default,
* optional decimals default to Decimal("0.00"), optional strings to "",
* conditional rules are validators that name the offending field in the error location,
* decimals are serialised with 2 places (4 for ``quantity``) and optional keys are always emitted.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

NTN_CNIC_PATTERN = r"^(\d{7}|\d{13})$"
HS_CODE_PATTERN = r"^\d{4}\.\d{4}$"
SCENARIO_ID_PATTERN = r"^SN0(0[1-9]|1\d|2[0-8])$"
INVOICE_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"

_NTN_CNIC_RE = re.compile(NTN_CNIC_PATTERN)
_SCENARIO_RE = re.compile(SCENARIO_ID_PATTERN)
_DATE_RE = re.compile(INVOICE_DATE_PATTERN)

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")
ZERO = Decimal("0.00")


class InvoiceType(str, Enum):
    SALE_INVOICE = "Sale Invoice"
    DEBIT_NOTE = "Debit Note"


class BuyerRegistrationType(str, Enum):
    REGISTERED = "Registered"
    UNREGISTERED = "Unregistered"


def _require_text(v: str) -> str:
    if v is None or not str(v).strip():
        raise ValueError("must be a non-empty string")
    return v


def is_ntn_or_cnic(value: str) -> bool:
    return bool(_NTN_CNIC_RE.match(value or ""))


def parse_invoice_date(value: str) -> date:
    """Strict YYYY-MM-DD parser; raises ValueError on any other shape."""
    if not _DATE_RE.match(value or ""):
        raise ValueError("invoiceDate must be in YYYY-MM-DD format")
    return date.fromisoformat(value)


# --------------------------------------------------------------------------- request


class InvoiceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hsCode: str = Field(pattern=HS_CODE_PATTERN, description="e.g. 0101.2100")
    productDescription: str = Field(min_length=1)
    rate: str = Field(min_length=1, description='rate description from reference API 5.8, e.g. "18%"')
    uoM: str = Field(min_length=1, description="UOM description from reference API 5.6")
    saleType: str = Field(min_length=1, description="transaction type description from reference API 5.5")
    quantity: Decimal = Field(gt=0, decimal_places=4)
    totalValues: Decimal = Field(ge=0, decimal_places=2)
    valueSalesExcludingST: Decimal = Field(ge=0, decimal_places=2)
    fixedNotifiedValueOrRetailPrice: Decimal = Field(ge=0, decimal_places=2)
    salesTaxApplicable: Decimal = Field(ge=0, decimal_places=2)
    salesTaxWithheldAtSource: Decimal = Field(ge=0, decimal_places=2)
    extraTax: Decimal = Field(default=ZERO, ge=0, decimal_places=2)
    furtherTax: Decimal = Field(default=ZERO, ge=0, decimal_places=2)
    fedPayable: Decimal = Field(default=ZERO, ge=0, decimal_places=2)
    discount: Decimal = Field(default=ZERO, ge=0, decimal_places=2)
    sroScheduleNo: str = ""
    sroItemSerialNo: str = ""

    @field_validator("productDescription", "rate", "uoM", "saleType")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        return _require_text(v)

    @field_validator("salesTaxWithheldAtSource")
    @classmethod
    def _withheld_zero_or_equal(cls, v: Decimal, info: ValidationInfo) -> Decimal:
        applicable = info.data.get("salesTaxApplicable")
        if applicable is not None and v != 0 and v != applicable:
            raise ValueError(
                "salesTaxWithheldAtSource must be 0 or equal to salesTaxApplicable (PRAL error 0008)"
            )
        return v

    def to_pral_dict(self) -> dict[str, Any]:
        """Key order and quantisation as in the PRAL samples. Every optional key is emitted."""
        q2 = lambda d: d.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)  # noqa: E731
        return {
            "hsCode": self.hsCode,
            "productDescription": self.productDescription,
            "rate": self.rate,
            "uoM": self.uoM,
            "quantity": self.quantity.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP),
            "totalValues": q2(self.totalValues),
            "valueSalesExcludingST": q2(self.valueSalesExcludingST),
            "fixedNotifiedValueOrRetailPrice": q2(self.fixedNotifiedValueOrRetailPrice),
            "salesTaxApplicable": q2(self.salesTaxApplicable),
            "salesTaxWithheldAtSource": q2(self.salesTaxWithheldAtSource),
            "extraTax": q2(self.extraTax),
            "furtherTax": q2(self.furtherTax),
            "sroScheduleNo": self.sroScheduleNo,
            "fedPayable": q2(self.fedPayable),
            "discount": q2(self.discount),
            "saleType": self.saleType,
            "sroItemSerialNo": self.sroItemSerialNo,
        }


class InvoicePayload(BaseModel):
    """The PRAL invoice body. Field declaration order matters: validators read earlier fields."""

    model_config = ConfigDict(extra="forbid")

    invoiceType: InvoiceType
    invoiceDate: str = Field(pattern=INVOICE_DATE_PATTERN, description="YYYY-MM-DD, not in the future")
    sellerNTNCNIC: str = Field(pattern=NTN_CNIC_PATTERN, description="7-digit NTN or 13-digit CNIC")
    sellerBusinessName: str = Field(min_length=1)
    sellerProvince: str = Field(min_length=1)  # TODO: check against the province reference cache
    sellerAddress: str = Field(min_length=1)
    buyerRegistrationType: BuyerRegistrationType
    buyerNTNCNIC: str = Field(default="", validate_default=True, description="required when Registered; 7 or 13 digits")
    buyerBusinessName: str = Field(min_length=1)
    buyerProvince: str = Field(min_length=1)  # TODO: check against the province reference cache
    buyerAddress: str = Field(min_length=1)
    invoiceRefNo: str = Field(default="", validate_default=True, description="Debit Note only: original FBR invoice number")
    scenarioId: str = Field(default="", validate_default=True, description="SN001..SN028, sandbox only")
    items: list[InvoiceItem] = Field(min_length=1)

    @field_validator(
        "sellerBusinessName",
        "sellerProvince",
        "sellerAddress",
        "buyerBusinessName",
        "buyerProvince",
        "buyerAddress",
    )
    @classmethod
    def _non_blank(cls, v: str) -> str:
        return _require_text(v)

    @field_validator("invoiceDate")
    @classmethod
    def _date_not_in_future(cls, v: str) -> str:
        d = parse_invoice_date(v)
        if d > date.today():
            raise ValueError("invoiceDate must not be in the future")
        return v

    @field_validator("buyerNTNCNIC")
    @classmethod
    def _buyer_ntn_rules(cls, v: str, info: ValidationInfo) -> str:
        v = (v or "").strip()
        reg_type = info.data.get("buyerRegistrationType")
        if reg_type == BuyerRegistrationType.REGISTERED and not v:
            raise ValueError("buyerNTNCNIC is required when buyerRegistrationType is Registered")
        if v and not is_ntn_or_cnic(v):
            raise ValueError("buyerNTNCNIC must be a 7-digit NTN or a 13-digit CNIC")
        seller = info.data.get("sellerNTNCNIC")
        if v and seller and v == seller:
            raise ValueError("buyerNTNCNIC must differ from sellerNTNCNIC (self-invoicing, PRAL error 0058)")
        return v

    @field_validator("invoiceRefNo")
    @classmethod
    def _invoice_ref_rules(cls, v: str, info: ValidationInfo) -> str:
        v = (v or "").strip()
        inv_type = info.data.get("invoiceType")
        if inv_type == InvoiceType.DEBIT_NOTE:
            if not v:
                raise ValueError("invoiceRefNo is required for a Debit Note (PRAL error 0026)")
            if len(v) not in (22, 28):
                raise ValueError("invoiceRefNo must be the original FBR invoice number (22 or 28 characters)")
        elif inv_type == InvoiceType.SALE_INVOICE and v:
            raise ValueError("invoiceRefNo must be empty for a Sale Invoice")
        return v

    @field_validator("scenarioId")
    @classmethod
    def _scenario_rules(cls, v: str, info: ValidationInfo) -> str:
        v = (v or "").strip()
        ctx = info.context or {}
        if ctx.get("sandbox") and not v:
            raise ValueError("scenarioId is required in the sandbox environment")
        if v and not _SCENARIO_RE.match(v):
            raise ValueError("scenarioId must be SN001..SN028")
        return v

    def to_pral_dict(self, include_scenario_id: bool = True) -> dict[str, Any]:
        """Dict in PRAL sample order with quantised Decimals; feed it to :func:`dumps_pral`."""
        d: dict[str, Any] = {
            "invoiceType": self.invoiceType.value,
            "invoiceDate": self.invoiceDate,
            "sellerNTNCNIC": self.sellerNTNCNIC,
            "sellerBusinessName": self.sellerBusinessName,
            "sellerProvince": self.sellerProvince,
            "sellerAddress": self.sellerAddress,
            "buyerNTNCNIC": self.buyerNTNCNIC,
            "buyerBusinessName": self.buyerBusinessName,
            "buyerProvince": self.buyerProvince,
            "buyerAddress": self.buyerAddress,
            "buyerRegistrationType": self.buyerRegistrationType.value,
            "invoiceRefNo": self.invoiceRefNo,
        }
        if include_scenario_id:
            d["scenarioId"] = self.scenarioId
        d["items"] = [item.to_pral_dict() for item in self.items]
        return d


_DEC_TOKEN_RE = re.compile(r'"__DEC__(-?\d+(?:\.\d+)?)__"')


def dumps_pral(obj: Any) -> str:
    """json.dumps that writes Decimals as bare JSON numbers keeping their places (1000.00, 1.0000)."""

    def conv(o: Any) -> Any:
        if isinstance(o, Decimal):
            return f"__DEC__{o:f}__"
        if isinstance(o, dict):
            return {k: conv(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [conv(v) for v in o]
        return o

    text = json.dumps(conv(obj), ensure_ascii=False)
    return _DEC_TOKEN_RE.sub(r"\1", text)


# --------------------------------------------------------------------------- response


class PralInvoiceStatus(BaseModel):
    """One entry of validationResponse.invoiceStatuses[]."""

    model_config = ConfigDict(extra="allow", coerce_numbers_to_str=True)

    itemSNo: str | None = None
    statusCode: str | None = None
    status: str | None = None
    invoiceNo: str | None = None
    errorCode: str | None = None
    error: str | None = None


class PralValidationResponse(BaseModel):
    model_config = ConfigDict(extra="allow", coerce_numbers_to_str=True)

    statusCode: str | None = None
    status: str | None = None
    errorCode: str | None = None
    error: str | None = None
    invoiceStatuses: list[PralInvoiceStatus] | None = None

    def is_success(self) -> bool:
        """Header statusCode "00" AND every invoiceStatuses[].statusCode "00" (status text is not trusted)."""
        if self.statusCode != "00":
            return False
        return all(s.statusCode == "00" for s in self.invoiceStatuses or [])

    def error_codes(self) -> list[str]:
        codes: list[str] = []
        if self.errorCode:
            codes.append(self.errorCode)
        for s in self.invoiceStatuses or []:
            if s.errorCode and s.errorCode not in codes:
                codes.append(s.errorCode)
        return codes


class PralResponse(BaseModel):
    """Body of validateinvoicedata / postinvoicedata. Kept tolerant: PRAL varies null vs ""."""

    model_config = ConfigDict(extra="allow", coerce_numbers_to_str=True)

    invoiceNumber: str | None = None
    dated: str | None = None
    validationResponse: PralValidationResponse | None = None

    def is_success(self) -> bool:
        return self.validationResponse is not None and self.validationResponse.is_success()

    def error_codes(self) -> list[str]:
        return self.validationResponse.error_codes() if self.validationResponse else []

    @classmethod
    def try_parse(cls, body: Any) -> PralResponse | None:
        if not isinstance(body, dict):
            return None
        try:
            return cls.model_validate(body)
        except Exception:  # pragma: no cover - unexpected shape, caller treats as not-success
            return None
