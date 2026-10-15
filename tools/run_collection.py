"""Replay a middleware Postman collection without Postman.

Runs every request in the collection in order, checks the HTTP code against the codes the
collection's own test scripts expect, and prints one line per request. Meant for the server
itself (over AnyDesk) or any machine on the server's network.

    .\\venv\\Scripts\\python.exe tools\\run_collection.py postman\\FBR-Middleware-PRL-inline.postman_collection.json ^
        --api-key <X-API-Key from .env> --token dummy-token

    # before the PRAL token exists: AUTH_ERROR from PRAL counts as the expected answer
    .\\venv\\Scripts\\python.exe tools\\run_collection.py postman\\FBR-Middleware-PRL-inline.postman_collection.json ^
        --base-url http://localhost:8003 --api-key <key> --token dummy-token --no-token-yet

    # the variable-based collection works too; values are substituted on the fly
    .\\venv\\Scripts\\python.exe tools\\run_collection.py postman\\FBR-Middleware.postman_collection.json ^
        --base-url http://localhost:8003 --api-key <key> --token dummy-token

Exit code 1 if any request answered a code its tests do not expect.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import httpx

PLACEHOLDERS = {
    "REPLACE_WITH_API_KEY": "api_key",
    "REPLACE_WITH_SANDBOX_TOKEN": "token",
    "REPLACE_WITH_PRODUCTION_TOKEN": "prd_token",
}
VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay a middleware Postman collection")
    ap.add_argument("collection", type=pathlib.Path)
    ap.add_argument("--base-url", default=None, help="override the server, e.g. http://localhost:8003")
    ap.add_argument("--api-key", default="", help="X-API-Key value")
    ap.add_argument("--token", default="dummy-token", help="sandbox Bearer token (dev requests)")
    ap.add_argument("--prd-token", default="", help="production Bearer token (prd requests); skipped when empty")
    ap.add_argument("--erp", default=None, help="erpInvoiceNo to use (default: the collection's)")
    ap.add_argument("--only", default=None, help="run only requests whose name contains this text")
    ap.add_argument("--timeout", type=float, default=210.0)
    ap.add_argument("--no-token-yet", action="store_true",
                    help="no PRAL token available: accept 502 AUTH_ERROR as the expected answer for every request that reaches PRAL")
    args = ap.parse_args()

    col = json.loads(args.collection.read_text(encoding="utf-8"))
    variables = {v["key"]: v["value"] for v in col.get("variable", [])}
    variables.update({"apiKey": args.api_key, "devToken": args.token, "prdToken": args.prd_token or "dummy-token"})
    if args.base_url:
        variables["baseUrl"] = args.base_url
    if args.erp:
        variables["erpInvoiceNo"] = args.erp
    literal = {"api_key": args.api_key, "token": args.token, "prd_token": args.prd_token or "dummy-token"}

    def sub(text: str) -> str:
        text = VAR_RE.sub(lambda m: variables.get(m.group(1), m.group(0)), text)
        for ph, key in PLACEHOLDERS.items():
            text = text.replace(ph, literal[key])
        if args.base_url:
            text = re.sub(r"https?://[^/\"]+(?=/api/|/health|/$|/\")", args.base_url.rstrip("/"), text)
        if args.erp:
            text = text.replace('"erpInvoiceNo": "0090000001"', f'"erpInvoiceNo": "{args.erp}"').replace("/0090000001", f"/{args.erp}")
        return text

    def expected(item: dict) -> set[int]:
        codes: set[int] = set()
        for ev in item.get("event", []):
            for line in ev["script"]["exec"]:
                m = re.search(r"to\.have\.status\((\d+)\)", line)
                if m:
                    codes.add(int(m.group(1)))
                m = re.search(r"to\.be\.oneOf\(\[([\d, ]+)\]\)", line)
                if m:
                    codes.update(int(x) for x in m.group(1).split(","))
        return codes

    def walk(items: list, folder: str = ""):
        for it in items:
            if "item" in it:
                yield from walk(it["item"], it["name"])
            else:
                yield folder, it

    failures = 0
    with httpx.Client(timeout=args.timeout) as client:
        for folder, item in walk(col["item"]):
            name = f"{folder} / {item['name']}" if folder else item["name"]
            if args.only and args.only.lower() not in name.lower():
                continue
            if folder.startswith("prd") and not args.prd_token:
                print(f"SKIP           {name}  (no --prd-token)")
                continue
            req = item["request"]
            url = sub(req["url"]["raw"])
            headers = {h["key"]: sub(h["value"]) for h in req.get("header", []) if not h.get("disabled")}
            body = sub(req["body"]["raw"]) if "body" in req else None
            try:
                resp = client.request(req["method"], url, headers=headers, content=body)
            except httpx.HTTPError as exc:
                failures += 1
                print(f"ERR  ---       {name}  {type(exc).__name__}: {exc}")
                continue
            exp = expected(item)
            ok = (resp.status_code in exp) if exp else True
            status = ""
            note = ""
            try:
                j = resp.json()
                if isinstance(j, dict):
                    status = j.get("status") or (j.get("detail") if isinstance(j.get("detail"), str) else "")
                    if (not ok and args.no_token_yet and resp.status_code in (200, 502)
                            and j.get("status") == "AUTH_ERROR" and j.get("pralHttpStatus") == 401):
                        ok = True
                        note = "  [reached PRAL; token rejected as expected without a real token]"
                    if j.get("fbrInvoiceNo"):
                        status += f"  fbrInvoiceNo={j['fbrInvoiceNo']}"
                    if j.get("trackingId"):
                        status += f"  trackingId={j['trackingId']}"
            except ValueError:
                pass
            failures += 0 if ok else 1
            tag = "OK " if ok else "BAD"
            print(f"{tag}  {resp.status_code} (expected {sorted(exp) or 'any'})  {name}  {status}{note}")
    print(f"\n{'all requests answered as expected' if not failures else f'{failures} request(s) did not'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
