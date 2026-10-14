# FBR Digital Invoicing Middleware

Synchronous pass-through between SAP and PRAL's Digital Invoicing API (spec v1.12). SAP calls
`POST /api/v1/{env}/invoices` with the invoice and the PRAL Bearer token; within the same request
the middleware validates the payload, calls PRAL `validateinvoicedata` then `postinvoicedata`,
stores the outcome, and returns PRAL's answer verbatim inside a thin envelope.

One instance serves both PRAL environments. The URL picks the target:

| URL | PRAL environment | PRAL endpoints | `scenarioId` |
|---|---|---|---|
| `POST /api/v1/dev/invoices` | sandbox | `.../validateinvoicedata_sb`, `.../postinvoicedata_sb` | required, forwarded |
| `POST /api/v1/prd/invoices` | production | `.../validateinvoicedata`, `.../postinvoicedata` | stripped |
| `POST /api/v1/{dev\|prd}/invoices/validate` | dry run on either | `.../validateinvoicedata` only; nothing posted or stored | as above |

SAP sends the Bearer token issued for that environment (`ZFBR_TOKEN` keyed by `ENV`). Any other
segment answers 404. `erpInvoiceNo` is idempotent per environment, so the same SAP document can
be sent to `dev` for certification and later to `prd`.

Documents:

| Document | Content |
|---|---|
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Windows Server deployment: pre-flight, layout, port and firewall, NSSM service, verification, day-to-day, lessons learned |
| [docs/SAP-ABAP-Interface-Spec.md](docs/SAP-ABAP-Interface-Spec.md) | Interface specification for the ABAP developer: endpoints, parameters, SAP field mapping, responses, validations, prerequisites, test checklist (Word copy: `docs/SAP-ABAP-Interface-Spec.docx`) |
| [docs/api-samples.md](docs/api-samples.md) | Request parameters, sample payloads and responses |
| [docs/samples/](docs/samples/) | The same samples as JSON files |
| [postman/FBR-Middleware.postman_collection.json](postman/FBR-Middleware.postman_collection.json) | Postman collection for the middleware: health, dev, prd, every error case |
| [postman/FBR-Middleware-PRL.postman_environment.json](postman/FBR-Middleware-PRL.postman_environment.json) | Postman environment for the deployed server (10.0.0.86:8080); fill in `apiKey` and the PRAL tokens |
| [postman/FBR-Digital-Invoicing.postman_collection.json](postman/FBR-Digital-Invoicing.postman_collection.json) | PRAL's own endpoints (spec samples), for direct sandbox tests |
| [docs/middleware-flow.md](docs/middleware-flow.md) | Design: flow, contract, token handling |
| [docs/FBR-DI-API-Analysis.md](docs/FBR-DI-API-Analysis.md) | Analysis of the PRAL spec v1.12 |
| [deploy/windows/preflight.ps1](deploy/windows/preflight.ps1) | Read-only pre-flight checks: ports in use, Python, PyPI, PRAL reachability, proxy, disk, clock |
| [deploy/windows/install-service.ps1](deploy/windows/install-service.ps1) | End-to-end installer: venv, NSSM, port check, service, firewall, health; `uninstall-service.ps1` reverses it |

Target platform is Windows Server with plain Python 3.12 (no Docker, no Linux). Everything below
is PowerShell.

## Layout

```
src/fbr_middleware/
  config.py               settings (pydantic-settings, .env / ENV_FILE)
  security.py             X-API-Key and Authorization: Bearer dependencies
  logging_setup.py        daily-rotating file + console, Authorization redaction filter
  models/fbr.py           PRAL payload + response models (required/optional/conditional rules)
  models/api.py           SAP envelope: InvoiceSubmission, InvoiceEnvelope, status -> HTTP map
  db/models.py            invoices + attempts tables (no token column anywhere)
  clients/pral.py         httpx client: retries, 401 -> AuthError, post timeout -> TimeoutAfterSend
  services/prevalidate.py local business rules, scenarioId stripping
  services/errors.py      PRAL error code -> BRIEF_MSG_DESC catalogue (spec section 7)
  services/concurrency.py per-seller-NTN lock + global in-flight cap
  services/submit.py      orchestration: idempotency -> prevalidate -> validate -> post -> persist
  api/invoices.py         POST /api/v1/{env}/invoices[/validate], GET /api/v1/{env}/invoices/{erpInvoiceNo}
  api/health.py           GET /health
  main.py, run.py         app factory and `python -m fbr_middleware`
tools/mock_pral.py        mock PRAL gateway for local / SAP-team testing
tests/                    pytest + respx
```

## Setup (PowerShell)

Python 3.12 64-bit from python.org (not the Microsoft Store build). If `python` resolves to a
different version use `py -3.12` in the first command.

```powershell
cd D:\Projects\fbr-integration
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"            # add ,postgres for asyncpg
Copy-Item .env.example .env       # then edit .env
```

If `Activate.ps1` is blocked by the execution policy, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or skip activation and call
`.\.venv\Scripts\python.exe` explicitly.

Settings in `.env` (all keys are documented in `.env.example`):

| Key | Default | Notes |
|---|---|---|
| `FBR_BASE_URL` | `https://gw.fbr.gov.pk` | PRAL gateway for both environments |
| `FBR_DEV_BASE_URL` | empty | override for `dev` only, e.g. `http://127.0.0.1:9000` for the mock |
| `FBR_PRD_BASE_URL` | empty | override for `prd` only |
| `FBR_ENABLED_ENVS` | `dev,prd` | environments this instance accepts; set `dev` on a sandbox-only box |
| `MIDDLEWARE_API_KEYS` | | comma-separated, one per SAP system |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/fbr_middleware.db` | PostgreSQL via `asyncpg` optional |
| `LISTEN_HOST` / `LISTEN_PORT` | `0.0.0.0` / `8000` | |
| `LOG_DIR` | `./logs` | `C:\fbr-middleware\logs` on the server |

`ENV_FILE=C:\path\to\.env` overrides where `.env` is read from (used by the Windows service).
`SSL_CERTFILE` / `SSL_KEYFILE` make uvicorn serve HTTPS directly; leave empty behind IIS ARR.

## Run

Terminal 1, the mock PRAL gateway (until a sandbox token is available):

```powershell
.\.venv\Scripts\python.exe tools\mock_pral.py --port 9000
```

Terminal 2, the middleware (single process, async; no `--workers` on Windows):

```powershell
# in .env: FBR_DEV_BASE_URL=http://127.0.0.1:9000  MIDDLEWARE_API_KEYS=change-me-sap-dev
# (add FBR_PRD_BASE_URL=http://127.0.0.1:9000 too if you want to try the prd path against the mock)
.\.venv\Scripts\python.exe -m fbr_middleware
```

Check it is up:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

OpenAPI UI: http://localhost:8000/docs

## Submit the sample invoice (both headers required)

The body is the spec sample from `postman/FBR-Digital-Invoicing.postman_collection.json`
wrapped in the SAP envelope. Against the mock any token except `bad` is accepted. The example
targets `dev` (sandbox); use `/api/v1/prd/invoices` with the production token for production,
where `scenarioId` may be omitted.

```powershell
$headers = @{
  "X-API-Key"     = "change-me-sap-dev"
  "Authorization" = "Bearer <PRAL token from ZFBR_TOKEN>"
  "Content-Type"  = "application/json"
}

$body = @'
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
'@

$r = Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/v1/dev/invoices -Headers $headers -Body $body
$r | ConvertTo-Json -Depth 10
```

Against the deployed server replace `localhost:8000` with `10.0.0.86:8080`.

Look up a stored result later (only the API key is needed):

```powershell
Invoke-RestMethod -Uri http://localhost:8000/api/v1/dev/invoices/0090000001 -Headers @{ "X-API-Key" = "change-me-sap-dev" }
```

`Invoke-RestMethod` throws on non-2xx. To see the envelope for a 422/502/504, catch it:

```powershell
try { Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/v1/dev/invoices -Headers $headers -Body $body }
catch { $_.ErrorDetails.Message }
```

### Outcomes

| `status` | HTTP | Meaning |
|---|---|---|
| `SUBMITTED` | 201 | `fbrInvoiceNo` set, `pralResponse` is PRAL's post body verbatim |
| `VALIDATED` | 200 | dry run via `.../invoices/validate` passed PRAL validate; nothing posted or stored |
| (replay) | 200 | `replayed: true`, same erpInvoiceNo already SUBMITTED or UNKNOWN; PRAL not called |
| `REJECTED_LOCAL` | 422 | see `localErrors[]` |
| `REJECTED_FBR` | 422 | see `pralResponse.validationResponse` and `errorDescriptions[]` |
| `FAILED_UPSTREAM` | 502 | PRAL 5xx after 3 attempts (2 s / 4 s backoff), or unreachable; safe to resubmit |
| `AUTH_ERROR` | 502 | PRAL 401 or error 0401/0402; fix the token in the Z table, resubmit |
| `UNKNOWN` | 504 | post was sent, no answer. Not retried. Use GET later or reconcile on the portal |

Every envelope also carries `environment` (`dev` or `prd`).

Shape violations (wrong types, missing required field, conditional rule such as missing
`buyerNTNCNIC` for a Registered buyer, missing `scenarioId` on the `dev` path, `buyer == seller`, bad
`hsCode`, withheld tax neither 0 nor `salesTaxApplicable`) return 422 with
`{"detail": [{"field", "message", "type"}]}` and nothing is stored. Missing or malformed
`X-API-Key` / `Authorization: Bearer` returns 401 and nothing is stored.

A rejected or failed invoice (REJECTED_*, FAILED_UPSTREAM, AUTH_ERROR) is re-processed when SAP
resubmits the same `erpInvoiceNo`. SUBMITTED and UNKNOWN are replayed.

Mock PRAL triggers for testing each outcome are listed at the top of `tools/mock_pral.py`
(`REJECT` in the product description or hsCode `9999.9999` -> REJECTED_FBR, token `bad` ->
AUTH_ERROR, `FAIL500` / `SLOW` in the product description -> FAILED_UPSTREAM / UNKNOWN).

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

PRAL is mocked with respx; the backoff sleep is patched, so the suite runs in a few seconds and
needs no network. The tests cover the happy path, header- and item-level PRAL rejections, post
timeout (UNKNOWN), 401 / 0401, 503 retries, idempotent replay, every conditional field rule,
header authentication, lookup, and that the token never reaches logs or the database.

## Windows service

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). `deploy\windows\install-service.ps1` builds the venv and
registers the NSSM service; `python -m fbr_middleware` is the service command, run from the app
directory where `.env` lives.

## Not implemented yet (stubs / TODO)

- Reference data sync (`REFERENCE_SYNC_CRON`, `/api/v1/reference/*`), so `sellerProvince`,
  `uoM`, `rate` and `saleType` strings are not checked against PRAL's lists locally.
- QR code generation (payload to be confirmed by PRAL).
- Admin endpoints (list / reconcile UNKNOWN, force sync).
- Alembic migrations (tables are created on startup with `create_all`).
