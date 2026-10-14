# API parameters and sample payloads

Machine-readable copies of every sample here are in `docs/samples/` and in the Postman
collection `postman/FBR-Middleware.postman_collection.json`.

## Endpoints

| Method | URL | Headers | Purpose |
|---|---|---|---|
| POST | `/api/v1/dev/invoices` | `X-API-Key`, `Authorization: Bearer <sandbox token>` | Submit to PRAL sandbox (validate, then post) |
| POST | `/api/v1/prd/invoices` | `X-API-Key`, `Authorization: Bearer <production token>` | Submit to PRAL production |
| POST | `/api/v1/{dev\|prd}/invoices/validate` | same as submit | Dry run: local rules + PRAL `validateinvoicedata`, nothing posted or stored; `status` VALIDATED (200) or a rejection |
| GET | `/api/v1/{dev\|prd}/invoices/{erpInvoiceNo}` | `X-API-Key` | Stored result |
| GET | `/health` | none | Liveness, DB, in-flight, PRAL URLs per environment |

## Request body: `POST /api/v1/{env}/invoices`

Envelope:

| Field | Type | Required | Rule |
|---|---|---|---|
| `erpInvoiceNo` | string 1-50 | yes | SAP billing document number. Idempotency key within the environment. |
| `sellerNtn` | string | yes | 7-digit NTN or 13-digit CNIC. Must equal `invoice.sellerNTNCNIC`. |
| `callbackRef` | string <= 100 | no | Echoed back unchanged (e.g. company code + fiscal year). |
| `invoice` | object | yes | PRAL payload below. |

`invoice` header:

| Field | Required | Rule |
|---|---|---|
| `invoiceType` | yes | `Sale Invoice` or `Debit Note` |
| `invoiceDate` | yes | `YYYY-MM-DD`, not in the future |
| `sellerNTNCNIC` | yes | 7 or 13 digits |
| `sellerBusinessName`, `sellerProvince`, `sellerAddress` | yes | non-empty; province is the PRAL description string, e.g. `Sindh` |
| `buyerRegistrationType` | yes | `Registered` or `Unregistered` |
| `buyerNTNCNIC` | conditional | required for `Registered`; 7 or 13 digits; must differ from the seller |
| `buyerBusinessName`, `buyerProvince`, `buyerAddress` | yes | non-empty |
| `invoiceRefNo` | conditional | `Debit Note`: original FBR invoice number (22 or 28 chars). `Sale Invoice`: must be empty. |
| `scenarioId` | conditional | `SN001`..`SN028`. Required on `dev`; stripped on `prd`. |
| `items` | yes | at least one |

`invoice.items[]`:

| Field | Required | Rule |
|---|---|---|
| `hsCode` | yes | `dddd.dddd`, e.g. `0101.2100` |
| `productDescription` | yes | non-empty |
| `rate` | yes | PRAL rate description, e.g. `18%` |
| `uoM` | yes | PRAL UOM description, e.g. `Numbers, pieces, units` |
| `saleType` | yes | PRAL transaction type description, e.g. `Goods at standard rate (default)` |
| `quantity` | yes | > 0, up to 4 decimals |
| `totalValues`, `valueSalesExcludingST`, `fixedNotifiedValueOrRetailPrice`, `salesTaxApplicable` | yes | >= 0, 2 decimals |
| `salesTaxWithheldAtSource` | yes | >= 0 and either `0` or equal to `salesTaxApplicable` |
| `extraTax`, `furtherTax`, `fedPayable`, `discount` | no | default `0.00` |
| `sroScheduleNo`, `sroItemSerialNo` | no | default `""`; PRAL requires them for SRO-based sale types |

Unknown keys are rejected (422). Decimals are forwarded to PRAL with 2 places (4 for quantity)
and optional keys are always sent.

## Sample: sale invoice, dev (sandbox)

`docs/samples/sale-invoice-dev.json`

```json
{
  "erpInvoiceNo": "0090000001",
  "sellerNtn": "0786909",
  "callbackRef": "1000/2025",
  "invoice": {
    "invoiceType": "Sale Invoice",
    "invoiceDate": "2025-04-21",
    "sellerNTNCNIC": "0786909",
    "sellerBusinessName": "Company 8",
    "sellerProvince": "Sindh",
    "sellerAddress": "Karachi",
    "buyerNTNCNIC": "1000000000000",
    "buyerBusinessName": "FERTILIZER MANUFAC IRS NEW",
    "buyerProvince": "Sindh",
    "buyerAddress": "Karachi",
    "buyerRegistrationType": "Registered",
    "invoiceRefNo": "",
    "scenarioId": "SN001",
    "items": [
      {
        "hsCode": "0101.2100",
        "productDescription": "product Description",
        "rate": "18%",
        "uoM": "Numbers, pieces, units",
        "quantity": 1.0000,
        "totalValues": 0.00,
        "valueSalesExcludingST": 1000.00,
        "fixedNotifiedValueOrRetailPrice": 0.00,
        "salesTaxApplicable": 180.00,
        "salesTaxWithheldAtSource": 0.00,
        "extraTax": 0.00,
        "furtherTax": 120.00,
        "sroScheduleNo": "",
        "fedPayable": 0.00,
        "discount": 0.00,
        "saleType": "Goods at standard rate (default)",
        "sroItemSerialNo": ""
      }
    ]
  }
}
```

## Sample: sale invoice, prd (production)

`docs/samples/sale-invoice-prd.json`: same shape, `buyerRegistrationType: "Unregistered"`,
`sroScheduleNo: "SRO123"` (as printed in the PRAL spec), and no `scenarioId`.

## Sample: debit note, dev

`docs/samples/debit-note-dev.json`: `invoiceType: "Debit Note"`, `invoiceRefNo` set to the FBR
invoice number of the original (`7000007DI1747119701593` in the spec sample; use the
`fbrInvoiceNo` returned for the original sale invoice), note date on or after the original and
within 180 days.

## Response: SUBMITTED (HTTP 201)

`docs/samples/response-submitted.json`

```json
{
  "trackingId": "e9895805-4b54-4ec6-8b6a-8311b66fbe08",
  "environment": "dev",
  "erpInvoiceNo": "0090000001",
  "sellerNtn": "0786909",
  "callbackRef": "1000/2025",
  "status": "SUBMITTED",
  "replayed": false,
  "fbrInvoiceNo": "0786909DI1791922500470",
  "dated": "2026-10-14 01:15:00",
  "pralHttpStatus": 200,
  "pralResponse": {
    "invoiceNumber": "0786909DI1791922500470",
    "dated": "2026-10-14 01:15:00",
    "validationResponse": {
      "statusCode": "00",
      "status": "Valid",
      "error": "",
      "invoiceStatuses": [
        { "itemSNo": "1", "statusCode": "00", "status": "Valid", "errorCode": "", "error": "",
          "invoiceNo": "0786909DI1791922500470-1" }
      ]
    }
  },
  "localErrors": [],
  "errorDescriptions": [],
  "attempts": 2,
  "receivedAt": "2026-10-13T20:15:00.016823Z",
  "completedAt": "2026-10-13T20:15:00.062818Z",
  "elapsedMs": 45
}
```

The same body sent again returns HTTP 200 with `replayed: true` and the same `trackingId`.

## Response: REJECTED_FBR (HTTP 422)

`docs/samples/response-rejected-fbr.json`: `status: "REJECTED_FBR"`, `fbrInvoiceNo: null`,
`pralResponse.validationResponse.invoiceStatuses[0].errorCode: "0046"`, and

```json
"errorDescriptions": [
  { "code": "0046", "description": "Rate cannot be empty, please provide valid rate as per selected Sales Type." }
]
```

## Response: shape violation (HTTP 422, nothing stored)

`docs/samples/response-shape-422.json`

```json
{ "detail": [ { "field": "invoice.scenarioId",
                "message": "scenarioId (SN001..SN028) is required for the dev (sandbox) environment",
                "type": "L008" } ] }
```

## Status to HTTP

| `status` | HTTP | SAP action |
|---|---|---|
| `SUBMITTED` | 201 | store `fbrInvoiceNo` and per-item `invoiceNo`, release output |
| `VALIDATED` | 200 | dry run passed; submit when ready |
| replayed | 200 | same as the stored status; PRAL was not called again |
| `REJECTED_LOCAL` | 422 | fix per `localErrors`, resubmit |
| `REJECTED_FBR` | 422 | fix per `pralResponse` + `errorDescriptions`, resubmit |
| `FAILED_UPSTREAM` | 502 | PRAL 5xx after retries or unreachable; resubmit later |
| `AUTH_ERROR` | 502 | PRAL 401 / 0401 / 0402; fix the token in `ZFBR_TOKEN`, resubmit |
| `UNKNOWN` | 504 | post sent, no answer; call GET later, do not resubmit blindly |
