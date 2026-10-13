"""Mock PRAL gateway for local testing before sandbox access.

Mimics ``/di_data/v1/di/validateinvoicedata[_sb]`` and ``/di_data/v1/di/postinvoicedata[_sb]``
with the sample responses from the DI API spec v1.12.

Behaviour triggers (so the SAP team can exercise every middleware outcome):

* ``Authorization: Bearer bad``       -> HTTP 401                         (middleware: AUTH_ERROR)
* productDescription contains REJECT  -> item-level invalid, errorCode 0046 (REJECTED_FBR)
* any item with empty ``rate``        -> item-level invalid, errorCode 0046 (direct mock calls only:
                                         the middleware rejects an empty rate locally with 422)
* any item with hsCode 9999.9999 or empty -> header-level invalid, errorCode 0052 (REJECTED_FBR)
* productDescription contains AUTH0401 -> header-level errorCode 0401     (AUTH_ERROR)
* productDescription contains FAIL500  -> HTTP 500                        (FAILED_UPSTREAM after retries)
* productDescription contains SLOW     -> sleeps 90 s                     (validate: FAILED_UPSTREAM, post: UNKNOWN)
* otherwise                            -> valid; post issues ``<sellerNTN>DI<epoch ms>``

Run:  python tools\\mock_pral.py --port 9000
Then point the middleware at it:  FBR_BASE_URL=http://localhost:9000
"""

from __future__ import annotations

import argparse
import asyncio
import time
from datetime import datetime
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock PRAL DI gateway", version="1.12-mock")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _unauthorized() -> JSONResponse:
    return JSONResponse(status_code=401, content={"message": "Unauthorized: invalid or missing bearer token"})


async def _handle(request: Request, authorization: str | None, issue_number: bool) -> JSONResponse:
    if not authorization or not authorization.lower().startswith("bearer "):
        return _unauthorized()
    token = authorization.split(None, 1)[1].strip()
    if token == "bad":
        return _unauthorized()

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"message": "Bad Request: body is not JSON"})

    items = body.get("items") or []
    descriptions = " ".join(str(i.get("productDescription", "")) for i in items if isinstance(i, dict))

    if "SLOW" in descriptions:
        await asyncio.sleep(90)
    if "FAIL500" in descriptions:
        return JSONResponse(status_code=500, content={"message": "Internal Server Error (mock)"})

    if "AUTH0401" in descriptions:
        return JSONResponse(
            status_code=200,
            content={
                "dated": _now(),
                "validationResponse": {
                    "statusCode": "01",
                    "status": "Invalid",
                    "errorCode": "0401",
                    "error": "The provided seller NTN/CNIC does not have a valid or authorized access token",
                    "invoiceStatuses": None,
                },
            },
        )

    # Header-level rejection (spec sample): any item without hsCode, or the 9999.9999 trigger
    if not items or any(
        str(i.get("hsCode", "")).strip() in ("", "9999.9999") for i in items if isinstance(i, dict)
    ):
        return JSONResponse(
            status_code=200,
            content={
                "dated": _now(),
                "validationResponse": {
                    "statusCode": "01",
                    "status": "Invalid",
                    "errorCode": "0052",
                    "error": "Provide proper HS Code with invoice no. null",
                    "invoiceStatuses": None,
                },
            },
        )

    seller = str(body.get("sellerNTNCNIC", "0000000"))
    invoice_number = f"{seller}DI{int(time.time() * 1000)}" if issue_number else None

    statuses = []
    any_invalid = False
    for idx, item in enumerate(items, start=1):
        if not str(item.get("rate", "")).strip() or "REJECT" in str(item.get("productDescription", "")):
            any_invalid = True
            statuses.append(
                {
                    "itemSNo": str(idx),
                    "statusCode": "01",
                    "status": "Invalid",
                    "invoiceNo": None,
                    "errorCode": "0046",
                    "error": "Provide rate.",
                }
            )
        else:
            st: dict[str, Any] = {
                "itemSNo": str(idx),
                "statusCode": "00",
                "status": "Valid",
                "errorCode": "" if issue_number else None,
                "error": "",
            }
            if issue_number:
                st["invoiceNo"] = f"{invoice_number}-{idx}"
            statuses.append(st)

    response: dict[str, Any] = {
        "dated": _now(),
        "validationResponse": {
            "statusCode": "00",
            "status": "invalid" if any_invalid else "Valid",
            "errorCode": None if not issue_number else "",
            "error": "",
            "invoiceStatuses": statuses,
        },
    }
    if issue_number and not any_invalid:
        response = {"invoiceNumber": invoice_number, **response}
        response["validationResponse"].pop("errorCode", None)
    return JSONResponse(status_code=200, content=response)


@app.post("/di_data/v1/di/validateinvoicedata")
@app.post("/di_data/v1/di/validateinvoicedata_sb")
async def validate(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
    return await _handle(request, authorization, issue_number=False)


@app.post("/di_data/v1/di/postinvoicedata")
@app.post("/di_data/v1/di/postinvoicedata_sb")
async def post(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
    return await _handle(request, authorization, issue_number=True)


@app.get("/")
async def index() -> dict[str, Any]:
    return {"mock": "PRAL DI API v1.12", "see": __doc__}


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock PRAL Digital Invoicing gateway")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
