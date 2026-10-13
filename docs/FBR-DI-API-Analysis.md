# FBR Digital Invoicing API v1.12 — Document Analysis

Source: `docs/FBRTechnicalDocumentationforDIAPIV1.12.pdf` (PRAL, "Technical Specification for DI API", v1.12, issued 7-Apr-2025, last revised 24-Jul-2025, 51 pages, privacy level Public).

PRAL = Pakistan Revenue Automation (Pvt.) Ltd, FBR's technology arm. DI = Digital Invoicing. The document is the contract every ERP must implement to push sales invoices to FBR in real time.

---

## 1. What the system does

- **Mode:** real-time, synchronous HTTPS + JSON. There is no batch or async path.
- **Flow (Figure 1, p.5):** ERP sends invoice data to the API. FBR's "Sales Data Controller" digitally signs it, encrypts it, stores it, and returns an **FBR invoice number**. The data pre-populates the buyer's Annex-A and the seller's Annex-C in their sales tax returns.
- **Platform:** PRAL ESP (Enterprise Service Platform), gateway host `gw.fbr.gov.pk`.
- **Onboarding:** each ERP registers in the FBR system; the taxpayer then integrates against this spec.

## 2. Security

- Bearer token in the `Authorization` header on every request.
- Token issued by PRAL, **valid for 5 years**, renewed on request.
- Sandbox vs production is selected by **both** the URL (sandbox endpoints carry an `_sb` suffix) and the token. Section 4's note says "URLs will remain the same ... routing will be based on the security token", but the sections immediately after give distinct sandbox URLs. Treat the `_sb` URLs as authoritative for sandbox and expect a sandbox-only token to be rejected on production URLs.
- Error codes `0401`/`0402` (added in v1.12) show the token is bound to specific seller/buyer NTN/CNIC values. A token for taxpayer A cannot post invoices for taxpayer B.

## 3. Core endpoints (section 4)

| Purpose | Method | Production URL | Sandbox URL |
|---|---|---|---|
| Post invoice (issues FBR number) | POST | `https://gw.fbr.gov.pk/di_data/v1/di/postinvoicedata` | `.../postinvoicedata_sb` |
| Validate only (no number issued) | POST | `https://gw.fbr.gov.pk/di_data/v1/di/validateinvoicedata` | `.../validateinvoicedata_sb` |

Both accept the identical request body. HTTP status is 200 / 401 / 500 only. **Validation failures come back as HTTP 200** with an error payload, so HTTP status alone is not a success signal.

### 3.1 Request body

Header (one per request):

| Field | Type | Required | Notes |
|---|---|---|---|
| `invoiceType` | string | yes | `"Sale Invoice"` or `"Debit Note"`. Credit Note is referenced throughout the error codes but never listed here. |
| `invoiceDate` | date | yes | `YYYY-MM-DD` |
| `sellerNTNCNIC` | string | yes | 7-digit NTN or 13-digit CNIC |
| `sellerBusinessName` | string | yes | |
| `sellerProvince` | string | yes | Province **description** from reference API 5.1, e.g. `"Sindh"` |
| `sellerAddress` | string | yes | |
| `buyerNTNCNIC` | string | conditional | Required for Registered; optional for Unregistered |
| `buyerBusinessName` | string | yes | |
| `buyerProvince` | string | yes | Province description |
| `buyerAddress` | string | yes | |
| `buyerRegistrationType` | string | yes | `"Registered"` or `"Unregistered"`. The description table misspells it `buyeRegistrationType`; the JSON samples are correct. |
| `invoiceRefNo` | string | debit note only | The FBR invoice number of the original invoice. 22 chars for NTN sellers, 28 for CNIC sellers. |
| `scenarioId` | string | **sandbox only** | `SN001`…`SN028`. Must be dropped (or ignored) in production. |
| `items` | array | yes | At least one item |

Item (repeating):

| Field | Type | Required | Notes |
|---|---|---|---|
| `hsCode` | string | yes | e.g. `"0101.2100"`; from reference API 5.3 |
| `productDescription` | string | yes | |
| `rate` | string | yes | The **rate description** string, e.g. `"18%"`, from `ratE_DESC` in API 5.8. Not a number. |
| `uoM` | string | yes | UOM **description** from API 5.6 / 5.9, e.g. `"Numbers, pieces, units"` |
| `quantity` | decimal | yes | |
| `totalValues` | decimal | yes | "Total Sales Value (Including Tax)". Sample shows `0.00` despite 1000 + 180 tax, so the sample is unreliable. |
| `valueSalesExcludingST` | decimal | yes | |
| `fixedNotifiedValueOrRetailPrice` | decimal | yes | Item-based retail/notified price (3rd Schedule goods) |
| `salesTaxApplicable` | decimal | yes | Sales tax or FED-in-ST-mode, excluding further/extra tax |
| `salesTaxWithheldAtSource` | decimal | yes | |
| `extraTax` | decimal | optional | |
| `furtherTax` | decimal | optional | |
| `sroScheduleNo` | string | optional | From API 5.7 |
| `fedPayable` | decimal | optional | |
| `discount` | decimal | optional | |
| `saleType` | string | yes | Transaction type **description** from API 5.5, e.g. `"Goods at standard rate (default)"` |
| `sroItemSerialNo` | string | optional | From API 5.4 / 5.10 |

**Key design fact:** the invoice payload carries human-readable description strings for province, rate, UOM and sale type, not the numeric IDs the reference APIs return. The strings must match the reference data exactly, so the reference lookups cannot be skipped.

### 3.2 Response body

Post, valid:

```json
{
  "invoiceNumber": "7000007DI1747119701593",
  "dated": "2025-05-13 12:01:41",
  "validationResponse": {
    "statusCode": "00",
    "status": "Valid",
    "error": "",
    "invoiceStatuses": [
      { "itemSNo": "1", "statusCode": "00", "status": "Valid",
        "invoiceNo": "7000007DI1747119701593-1", "errorCode": "", "error": "" }
    ]
  }
}
```

- FBR invoice number format: `<sellerNTN><"DI"><13-digit epoch ms>`. Per-item numbers append `-<itemSNo>`.
- Validate returns the same shape without `invoiceNumber` and with `errorCode: null` rather than `""`.

Two distinct failure shapes:

1. **Header-level rejection.** `validationResponse.statusCode = "01"`, `errorCode` set, `invoiceStatuses = null`.
2. **Item-level rejection.** `validationResponse.statusCode = "00"` but `status = "invalid"` (note the lowercase), and one or more `invoiceStatuses[].statusCode = "01"` with their own `errorCode`.

So success means: header `statusCode == "00"` **and** every item `statusCode == "00"`. Comparing `status` strings is unsafe because the casing varies between samples.

## 4. Reference APIs (section 5)

All GET, all return a JSON array, all under `gw.fbr.gov.pk`. HTTP 200 / 401 / 500.

| # | Endpoint | Query params | Returns |
|---|---|---|---|
| 5.1 | `/pdi/v1/provinces` | none | `stateProvinceCode`, `stateProvinceDesc` |
| 5.2 | `/pdi/v1/doctypecode` | none | `docTypeId`, `docDescription` (4 = Sale Invoice, 9 = Debit Note) |
| 5.3 | `/pdi/v1/itemdesccode` | none | `hS_CODE`, `description` (full HS tariff list, no pagination) |
| 5.4 | `/pdi/v1/sroitemcode` | none | `srO_ITEM_ID`, `srO_ITEM_DESC` |
| 5.5 | `/pdi/v1/transtypecode` | none | `transactioN_TYPE_ID`, `transactioN_DESC` |
| 5.6 | `/pdi/v1/uom` | none | `uoM_ID`, `description` |
| 5.7 | `/pdi/v1/SroSchedule` | `rate_id`, `date` (`DD-MMM-YYYY`), `origination_supplier_csv` | `srO_ID`, `srO_DESC` |
| 5.8 | `/pdi/v2/SaleTypeToRate` | `date` (`DD-MMM-YYYY`), `transTypeId`, `originationSupplier` | `ratE_ID`, `ratE_DESC`, `ratE_VALUE` |
| 5.9 | `/pdi/v2/HS_UOM` | `hs_code`, `annexure_id` | `uoM_ID`, `description` |
| 5.10 | `/pdi/v2/SROItem` | `date` (`YYYY-MM-DD`), `sro_id` | `srO_ITEM_ID`, `srO_ITEM_DESC` |
| 5.11 | `/dist/v1/statl` | JSON body `{"regno","date"}` | `status code` (`01`/`02`), `status` |
| 5.12 | `/dist/v1/Get_Reg_Type` | JSON body `{"Registration_No"}` | `statuscode` (`00` Registered / `01` unregistered), `REGISTRATION_NO`, `REGISTRATION_TYPE` |

Observations:

- Property names follow a broken .NET camel-casing of upper-snake columns (`hS_CODE`, `srO_ID`, `ratE_DESC`). Deserializers must match these literally.
- **Three date formats** are in play: `YYYY-MM-DD` for invoices and SROItem, `DD-MMM-YYYY` for SroSchedule and SaleTypeToRate, and `dd-MMM-yy` in one error message.
- 5.11 and 5.12 are described as GET but take a JSON body. Expect them to actually be POST, or to need a body on GET; confirm against sandbox.
- STATL (Sales Tax Active Taxpayer List) only shows `In-Active` samples for both codes `01` and `02`; the "Active" response is not documented.
- Rate lookup is **date- and province-dependent**: the same transaction type can return different rates by date and origination province, so cache keys must include both.
- 5.4 and 5.10 have the same title in the document but are different endpoints (static list vs date/SRO-filtered).

## 5. Invoice printing (section 6)

Every invoice must carry the FBR Digital Invoicing System logo and a QR code: **QR version 2 (25×25 modules), 1.0 × 1.0 inch**. The document does **not** specify the QR payload. The reasonable assumption is the FBR invoice number, but this must be confirmed with PRAL.

## 6. Error codes (sections 7 and 8)

### Sales errors (0001–0113, 0300, 0401, 0402)

The table is sparse (many codes are unassigned, e.g. 0004, 0014–0017, 0025, 0033). Grouped by theme:

- **Registration / identity:** 0001, 0002, 0009, 0012, 0053, 0082, 0083, 0106, 0107, 0108; 0401/0402 (token not authorised for the seller/buyer NTN). Note 0002 says buyer NTN is 7 **or 9** digits while 0401/0402 say 7 or 13; the invoice field spec says 7 or 13.
- **Mandatory field missing:** 0010, 0011, 0013, 0018–0024, 0031, 0041–0044, 0046, 0073, 0074, 0077, 0078, 0080, 0081, 0085, 0089, 0090, 0095, 0098.
- **Format:** 0005 and 0113 (date must be `YYYY-MM-DD`), 0088 (invoice number: alphanumeric with embedded hyphens, e.g. `Inv-001`), 0300 (non-numeric decimal in any money/quantity field, reported per item).
- **Debit / credit note rules:** 0026 (ref no required), 0027/0028 (reason, remarks for "Others"), 0029/0035 (note date ≥ original), 0034 (**within 180 days** of original), 0036/0037 (credit note value ≤ original), 0057 (reference invoice does not exist), 0064 (credit note already exists), 0067/0068 (debit note tax > original, credit note tax < original), 0071 (credit note restricted to specific users).
- **Sale type / HS / UOM consistency:** 0007, 0052 (HS code must match sale type), 0093 (sale type not allowed for manufacturer), 0096 (KWH UOM for electricity HS codes), 0097 (KG), 0099 (UOM must match HS code), 0100, 0101 (toll manufacturing for steel), 0056 (buyer not in steel sector).
- **Tax arithmetic:** 0008 and 0050 (withheld tax must be 0 or equal to sales tax), 0079 (5% rate not allowed above Rs 20,000), 0091 (extra tax must be empty), 0102 (3rd Schedule formula), 0103 (potassium chlorate), 0104 (tax % does not match rate), 0105 (tax vs quantity).
- **STWH (sales tax withholding) rules:** 0006, 0032, 0039, 0070 (STWH only for GOV/FTN holders, registered buyers).
- **Business rules:** 0030 (unregistered distributor before cut-off date), 0058 (**self-invoicing not allowed**: buyer and seller NTN cannot match), 0086 (EFS compressor-scrap licence), 0087 (petroleum levy config).

Note that 0027/0028 reference a "reason" and "remarks" for notes, yet no such fields exist in the request model. Either they are validated elsewhere or the model is incomplete.

### Purchase errors (0156–0177)

Mirror of the sales rules for the purchase side (buyer-registered-for-ST, FTN seller not allowed, cotton ginners, KWH/KG UOM, 20,000 / 5% rule, etc.). **The table is printed three times** on pages 42–45; it is a layout defect, not three different code sets. There is no purchase endpoint in this document, so these codes presumably come from a separate purchase/annex API.

## 7. Sandbox scenarios (section 9) and business-activity matrix (section 10)

28 scenarios, `SN001`–`SN028`. Each is tied to a specific `saleType` string. `SN026`–`SN028` are only for taxpayers registered as retailers.

| Scenario | Sale type |
|---|---|
| SN001 / SN002 | Goods at Standard Rate (default), registered / unregistered buyer |
| SN003 | Steel Melting and re-rolling |
| SN004 | Ship breaking |
| SN005 | Goods at Reduced Rate |
| SN006 | Exempt Goods |
| SN007 | Goods at zero-rate |
| SN008 | 3rd Schedule Goods |
| SN009 | Cotton Ginners (purchase type) |
| SN010 | Telecommunication services |
| SN011 | Toll Manufacturing |
| SN012 | Petroleum Products |
| SN013 | Electricity Supply to Retailers |
| SN014 | Gas to CNG stations |
| SN015 | Mobile Phones |
| SN016 | Processing/Conversion of Goods |
| SN017 | Goods (FED in ST Mode) |
| SN018 | Services (FED in ST Mode) |
| SN019 | Services |
| SN020 | Electric Vehicle |
| SN021 | Cement/Concrete Block |
| SN022 | Potassium Chlorate |
| SN023 | CNG Sales |
| SN024 | Goods as per SRO.297(I)/2023 |
| SN025 | Non-Adjustable Supplies (fixed-rate drugs, 8th Schedule serial 81) |
| SN026 / SN027 / SN028 | Retail to end consumer: standard rate / 3rd Schedule / reduced rate |

Section 10 is a matrix of business activity (Manufacturer, Importer, Distributor, Wholesaler, Exporter, Retailer, Service Provider, Other) × sector (All Other, Steel, FMCG, Textile, Telecom, Petroleum, Electricity Distribution, Gas Distribution, Services, Automobile, CNG Stations, Pharmaceuticals, Wholesale/Retail). It lists which scenarios the taxpayer must clear in sandbox before production access. Patterns:

- The **baseline set** for Manufacturer / Importer / Exporter / Other in most sectors is 11 scenarios: SN001, SN002, SN005, SN006, SN007, SN015, SN016, SN017, SN021, SN022, SN024, plus the sector-specific one (e.g. +SN010 for Telecom, +SN012 for Petroleum).
- Distributors, Wholesalers and Retailers in most sectors get the sector scenario plus the retail set SN026, SN027, SN028, SN008.
- Service Providers get SN018, SN019 plus the sector scenario.
- Steel manufacturers and retailers are the narrow case: only SN003, SN004, SN011.

The row numbering jumps (13→16, 28→31, 43→46, 58→61, 73→76, 88→91, 103→106), which suggests hidden rows in the source spreadsheet; nothing is actually missing per activity/sector pair.

## 8. Gaps and inconsistencies to confirm with PRAL before build

1. **Credit notes.** `invoiceType` allows only Sale Invoice and Debit Note, but ten error codes and the 180-day rule concern credit notes. Confirm the accepted `invoiceType` value and whether `reason`/`remarks` fields exist.
2. **QR payload** is unspecified.
3. **Token acquisition** process is not described beyond "issued by PRAL".
4. **Idempotency / duplicates.** No guidance on retries, timeouts, or what happens if the same invoice is posted twice. The ERP needs its own dedupe keyed on its internal invoice number and must persist the FBR number immediately on success.
5. **Rate limits and payload limits** (max items per invoice, field lengths) are absent.
6. **Sandbox URL vs token routing** contradiction (section 2 above).
7. **NTN length** for buyers: 7/9 vs 7/13.
8. **Date format** differs per endpoint.
9. **STATL / Get_Reg_Type** verb and the undocumented Active response.
10. **`totalValues`** semantics and the sample value of `0.00`.
11. **`invoiceRefNo`** is documented as required for debit notes only; confirm the same for credit notes.
12. **No purchase/annex endpoint** despite a purchase error code table.
13. **Reference lists are unpaginated.** The HS code list is large; plan to sync it on a schedule, not per request.

## 9. Implications for the integration design

- **Reference data layer.** Pull provinces, doc types, UOMs, transaction types, HS codes and SRO lists on a schedule and cache them. Rate and SRO lookups are date- and province-sensitive, so call them at invoice time (or cache keyed by date + province + transaction type).
- **Mapping layer.** Map internal master data to the exact FBR description strings for province, rate, UOM and sale type. A mismatch is a validation failure, not a warning.
- **Two-step submit.** Call `validateinvoicedata` first, then `postinvoicedata`. Both are cheap and synchronous, and validate-first avoids partial failure noise in production.
- **Success check.** Header `statusCode == "00"` and all `invoiceStatuses[].statusCode == "00"`. Persist the full request and response, the FBR invoice number, and each item's `invoiceNo`.
- **Error surface.** Load the section 7 table as a lookup so users see `BRIEF_MSG_DESC` next to the raw `errorCode`.
- **Environment switch.** One config flag chooses the `_sb` URLs, the sandbox token, and whether `scenarioId` is included.
- **Sandbox certification harness.** Drive the scenario list from the section 10 matrix for the taxpayer's activity/sector and run each one against `_sb` with the matching `saleType`.
- **Printing.** Add the FBR logo and a 1-inch version-2 QR code to the invoice template once the QR payload is confirmed.
- **Business rules worth validating client-side** before calling FBR: date format, NTN/CNIC length, buyer ≠ seller, debit/credit note within 180 days and dated ≥ original, withheld tax ∈ {0, salesTaxApplicable}, tax = value × rate, and the Rs 20,000 / 5% rule.
