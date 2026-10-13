# Deployment: FBR Middleware on Windows Server

Target: one Windows Server machine (2019 or later), plain Python, run as a Windows service with
NSSM. No Docker, no Linux. One instance serves both PRAL environments:

| URL on the middleware | PRAL target |
|---|---|
| `POST /api/v1/dev/invoices` | sandbox (`..._sb` endpoints, scenarioId required) |
| `POST /api/v1/prd/invoices` | production (plain endpoints, scenarioId stripped) |

All commands are PowerShell, run as Administrator unless noted.

## 1. Prerequisites

| Item | Detail |
|---|---|
| Python | 3.12 64-bit from python.org, installed for all users. Not the Microsoft Store build. 3.14 is not supported yet by the pydantic wheels. **If any other Python already exists on the server, install without "Add to PATH"** and use the absolute interpreter path in step 3; the middleware never needs `python` on PATH. |
| NSSM | `nssm.exe` 2.24 or later (https://nssm.cc), copied to `C:\fbr-middleware\nssm.exe` |
| Network | Outbound HTTPS from the server to `gw.fbr.gov.pk` (through the corporate proxy if any). Inbound TCP from the SAP application servers to the middleware port. |
| Service account | A local or domain account with "Log on as a service". Below: `NT SERVICE\FbrMiddleware` (virtual account, created by the service) |
| Secrets | Middleware API key(s) for SAP. The PRAL tokens are **not** needed on this server; SAP sends them per request. |
| Certificate | Only if TLS terminates on the middleware (see section 8). |

## 1b. Pre-flight checks (run before anything is installed)

Run `app\deploy\preflight.ps1` (read-only, prints PASS / WARN / FAIL per check), or the
individual commands below. Every FAIL blocks the install.

```powershell
# Python: must print 3.12.x, 64-bit, from python.org (not WindowsApps)
python --version; py -0p
python -c "import struct, sys; print(struct.calcsize('P')*8, sys.executable)"

# Outbound HTTPS to PyPI: pip install needs it (HTTP 200)
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest https://pypi.org/simple/pip/ -UseBasicParsing -TimeoutSec 10 | Select-Object StatusCode

# Outbound HTTPS to the PRAL gateway: any HTTP answer (200, 403, 404) proves the route and TLS work;
# a timeout or "could not resolve" means firewall, proxy or DNS
Invoke-WebRequest https://gw.fbr.gov.pk -UseBasicParsing -TimeoutSec 10 | Select-Object StatusCode
Resolve-DnsName gw.fbr.gov.pk

# Proxy: if the site uses one, the service needs HTTPS_PROXY too (step 6)
Get-ChildItem env: | Where-Object Name -match "PROXY"
(Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings").ProxyServer

# Listen port free (8080 behind IIS, 8443 for direct TLS)
Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue

# Disk, clock, time zone
Get-PSDrive C | Select-Object @{n="FreeGB";e={[math]::Round($_.Free/1GB,1)}}
w32tm /query /status
Get-TimeZone
```

| Check | Why it matters |
|---|---|
| Python 3.12 64-bit | pydantic-core has no wheel for 3.14; the Store build breaks `python -m venv` and service paths |
| PyPI reachable | `pip install` downloads about 40 packages; without internet, build a wheelhouse on another machine (`pip download -r ...`) and install with `--no-index --find-links` |
| PRAL gateway reachable | Every submission ends `FAILED_UPSTREAM` otherwise. Test from the server itself, not from a workstation |
| Proxy | httpx honours `HTTPS_PROXY`; it must be set in the service environment, not only in the admin's profile |
| Port free | uvicorn fails to bind and NSSM restarts the service in a loop |
| Disk space | SQLite file plus 30 days of logs plus the venv: 5 GB is comfortable |
| Clock and time zone | `invoiceDate` must not be in the future and PRAL's `dated` is compared by operators; the server should sync to a domain or NTP source |
| Admin shell | service install, `icacls`, firewall rule |

If pip cannot reach PyPI, prepare the packages offline:

```powershell
# on a machine with internet (same Python 3.12 64-bit)
python -m pip download -d .\wheelhouse -e .\app
# on the server
.\.venv\Scripts\python.exe -m pip install --no-index --find-links .\wheelhouse -e .\app
```

## 1c. Coexistence with other software on the server

The deployment is self-contained. What it adds and what it never touches:

| Adds | Never touches |
|---|---|
| folder `C:\fbr-middleware` with its own venv | the system Python packages of any other application |
| one service `FbrMiddleware` (one process, about 100-150 MB RAM) | other services, scheduled tasks, the registry |
| one listening port (8080 behind IIS, or 8443) | ports of other applications |
| one inbound firewall rule scoped to the SAP server IPs | existing rules |
| optional IIS site for TLS | existing IIS sites, if the new site uses its own host name or port |
| outbound HTTPS to `gw.fbr.gov.pk` only | other outbound traffic |

The only system-wide change is the Python 3.12 installer. With "Add to PATH" ticked it becomes
the `python` that other scripts and services on the box resolve to. On a shared server install it
without that option and create the venv from the absolute path:

```powershell
& "C:\Program Files\Python312\python.exe" -m venv C:\fbr-middleware\.venv
```

Checks 13 to 15 of `preflight.ps1` (section 1b) list other Python installations, existing IIS bindings on 443,
the firewall rule and other Python or NSSM services, so port and folder clashes are seen before
anything is installed.

## 2. Layout

```
C:\fbr-middleware\
  app\            the repository (src\, tools\, pyproject.toml, README.md, docs\)
  .venv\          Python virtual environment
  data\           fbr_middleware.db (SQLite)
  logs\           app.log (live), app-YYYY-MM-DD.log (30 days kept), service-out.log, service-err.log
  .env            settings, readable only by the service account and Administrators
  nssm.exe
```

## 3. Install the application

```powershell
New-Item -ItemType Directory -Force C:\fbr-middleware, C:\fbr-middleware\data, C:\fbr-middleware\logs | Out-Null
cd C:\fbr-middleware

# Copy or clone the release into .\app
git clone <repo-url> app          # or copy the folder over AnyDesk / file share

python --version                  # must print 3.12.x
python -m venv .venv                       # or: & "C:\Program Files\Python312\python.exe" -m venv .venv (shared server, Python not on PATH)
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .\app          # add ".\app[postgres]" for PostgreSQL
```

Smoke test the install (no service yet):

```powershell
.\.venv\Scripts\python.exe -c "import fbr_middleware; print(fbr_middleware.__version__)"
```

## 4. Configure `.env`

```powershell
Copy-Item .\app\.env.example .\.env
notepad .\.env
```

Production values:

```ini
FBR_BASE_URL=https://gw.fbr.gov.pk
FBR_ENABLED_ENVS=dev,prd            # "dev" only on a box that must never reach production
FBR_TIMEOUT_SECONDS=30
FBR_MAX_RETRIES_5XX=3
MAX_INFLIGHT_PER_SELLER=1
MAX_INFLIGHT_TOTAL=50
MIDDLEWARE_API_KEYS=<long random key for SAP PRD>,<key for SAP QAS>
DATABASE_URL=sqlite+aiosqlite:///C:/fbr-middleware/data/fbr_middleware.db
LOG_DIR=C:\fbr-middleware\logs
LOG_LEVEL=INFO
LISTEN_HOST=127.0.0.1               # behind IIS; 0.0.0.0 if SAP calls the port directly
LISTEN_PORT=8080
```

Generate a key: `[Convert]::ToBase64String((1..48 | % { Get-Random -Max 256 }) -as [byte[]])`.

Leave `FBR_DEV_BASE_URL` / `FBR_PRD_BASE_URL` unset on the server; they exist to point `dev` at
the local mock during development.

Restrict the file:

```powershell
icacls C:\fbr-middleware\.env /inheritance:r /grant:r "NT SERVICE\FbrMiddleware:R" "Administrators:F"
```

(`NT SERVICE\FbrMiddleware` resolves only after the service exists; run this again after step 6
if it errors now.)

## 5. First run in the foreground

```powershell
cd C:\fbr-middleware\app
$env:ENV_FILE = "C:\fbr-middleware\.env"
..\.venv\Scripts\python.exe -m fbr_middleware
```

In a second window:

```powershell
Invoke-RestMethod http://localhost:8080/health | ConvertTo-Json -Depth 4
```

Expect `status: ok`, `db: ok` and both `dev` and `prd` under `environments` with the PRAL URLs.
Stop the foreground process with Ctrl+C.

## 6. Install the Windows service

Either run `app\deploy\install-service.ps1` or the commands below.

```powershell
cd C:\fbr-middleware
.\nssm.exe install FbrMiddleware "C:\fbr-middleware\.venv\Scripts\python.exe" "-m fbr_middleware"
.\nssm.exe set FbrMiddleware AppDirectory C:\fbr-middleware\app
.\nssm.exe set FbrMiddleware AppEnvironmentExtra ENV_FILE=C:\fbr-middleware\.env
.\nssm.exe set FbrMiddleware DisplayName "FBR Digital Invoicing Middleware"
.\nssm.exe set FbrMiddleware Description "SAP -> PRAL pass-through (dev + prd)"
.\nssm.exe set FbrMiddleware AppStdout C:\fbr-middleware\logs\service-out.log
.\nssm.exe set FbrMiddleware AppStderr C:\fbr-middleware\logs\service-err.log
.\nssm.exe set FbrMiddleware AppRotateFiles 1
.\nssm.exe set FbrMiddleware AppRotateBytes 10485760
.\nssm.exe set FbrMiddleware AppStopMethodSkip 6      # send Ctrl+C only, let uvicorn finish in-flight calls
.\nssm.exe set FbrMiddleware AppStopMethodConsole 30000
.\nssm.exe set FbrMiddleware Start SERVICE_AUTO_START
.\nssm.exe set FbrMiddleware AppExit Default Restart
.\nssm.exe set FbrMiddleware AppRestartDelay 5000
.\nssm.exe start FbrMiddleware
.\nssm.exe status FbrMiddleware
```

If the site uses an outbound proxy:

```powershell
.\nssm.exe set FbrMiddleware AppEnvironmentExtra ENV_FILE=C:\fbr-middleware\.env HTTPS_PROXY=http://proxy.corp:8080 NO_PROXY=localhost,127.0.0.1
```

Grant the data and log folders to the service account:

```powershell
icacls C:\fbr-middleware\data /grant "NT SERVICE\FbrMiddleware:(OI)(CI)M"
icacls C:\fbr-middleware\logs /grant "NT SERVICE\FbrMiddleware:(OI)(CI)M"
.\nssm.exe restart FbrMiddleware
```

## 7. Firewall

Allow only the SAP application servers:

```powershell
New-NetFirewallRule -DisplayName "FBR Middleware" -Direction Inbound -Protocol TCP -LocalPort 443 `
  -RemoteAddress 10.0.0.11,10.0.0.12 -Action Allow
```

Use port 443 when IIS fronts the service, otherwise the `LISTEN_PORT` value.

## 8. TLS

SAP's SM59 destination should use HTTPS. Pick one:

**A. IIS reverse proxy (recommended).** Install IIS with URL Rewrite and Application Request
Routing. Bind the company certificate on 443. Add a site with a reverse-proxy rule to
`http://localhost:8080/`. In ARR set the proxy timeout to 180 seconds (PRAL worst case is about
100 s per submission) and disable response buffering. Keep `LISTEN_HOST=127.0.0.1`.

**B. uvicorn with the certificate.** Export the certificate and key as PEM, then in `.env`:

```ini
LISTEN_HOST=0.0.0.0
LISTEN_PORT=8443
SSL_CERTFILE=C:\fbr-middleware\cert.pem
SSL_KEYFILE=C:\fbr-middleware\key.pem
```

Restart the service after every certificate renewal.

## 9. Verify

```powershell
Invoke-RestMethod https://<host>/health | ConvertTo-Json -Depth 4
```

Then submit the sample invoice to `dev` with a real sandbox token (body in
`app\docs\samples\sale-invoice-dev.json`):

```powershell
$headers = @{ "X-API-Key" = "<key>"; "Authorization" = "Bearer <sandbox token>"; "Content-Type" = "application/json" }
$body = Get-Content C:\fbr-middleware\app\docs\samples\sale-invoice-dev.json -Raw
Invoke-RestMethod -Method Post -Uri https://<host>/api/v1/dev/invoices -Headers $headers -Body $body | ConvertTo-Json -Depth 10
```

Expect HTTP 201 and `status: SUBMITTED`. The same call again returns 200 with `replayed: true`.
Import `app\postman\FBR-Middleware.postman_collection.json` into Postman for the full set of
calls (health, dev, prd, every error case).

## 10. SAP side checklist

- SM59 destination type G: host, port 443, SSL active, path prefix empty.
- Headers per call: `X-API-Key` (static, from the destination or the client class) and
  `Authorization: Bearer <token>` read from `ZFBR_TOKEN` by `SELLER_NTN` and `ENV`.
- Path per environment: `/api/v1/dev/invoices` while certifying, `/api/v1/prd/invoices` after
  the production token is issued. The `ENV` key that selects the token also selects the path.
- HTTP client timeout at least 120 s.
- Handle 201 / 200 / 422 / 502 / 504 / 401 as in README.md "Outcomes". On 504 or a client-side
  timeout call `GET /api/v1/{env}/invoices/{erpInvoiceNo}` before any resubmit.

## 11. Updating a release

```powershell
cd C:\fbr-middleware
.\nssm.exe stop FbrMiddleware
Copy-Item -Recurse -Force C:\fbr-middleware\data C:\fbr-middleware\backup\data-$(Get-Date -Format yyyyMMdd-HHmm)
cd app; git pull; cd ..                 # or replace the folder
.\.venv\Scripts\python.exe -m pip install -e .\app
.\nssm.exe start FbrMiddleware
Invoke-RestMethod http://localhost:8080/health
```

Tables are created on start if missing. A release that changes an existing table ships a note
in its README; until Alembic is added, apply such changes by hand after the backup.

Rollback: stop the service, `git checkout <previous tag>` (or restore the folder), reinstall
with pip, restore `data\` from the backup if the schema changed, start the service.

## 12. Operations

| Topic | Where / how |
|---|---|
| Application log | `C:\fbr-middleware\logs\app.log`, one line per step with `trackingId`; rotated at midnight, 30 days |
| Service stdout/stderr | `service-out.log`, `service-err.log` (uvicorn start-up errors land here) |
| Health | `GET /health`: `db`, `inflight`, PRAL URLs per environment. Point the monitoring tool at it. |
| Database | `data\fbr_middleware.db`. Back up with the normal server backup (VSS-aware tool). Rows: `invoices`, `attempts`. |
| UNKNOWN invoices | `SELECT env, erp_invoice_no, tracking_id, received_at FROM invoices WHERE status='UNKNOWN'`. Reconcile on the FBR portal; resubmission is blocked until an operator clears the row (admin endpoint is a TODO). |
| Token problems | `status = AUTH_ERROR` means PRAL rejected the token SAP sent. Fix `ZFBR_TOKEN` in SAP; nothing to change on this server. |
| Secrets hygiene | The token never appears in logs or the DB. A log line containing `Authorization` or a bearer value is an incident: the redaction filter is broken. |
| Clock | `invoiceDate` validation and PRAL's `dated` depend on the server clock; keep NTP in sync. |
| Reboots | Windows Update restarts the service. Calls in flight at that moment return an error to ABAP and must be resubmitted (or looked up via GET first). |

## 13. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Service stops immediately | Check `service-err.log`. Usually a bad `.env` value (e.g. `FBR_ENABLED_ENVS` typo) or the `.env` ACL denies the service account. |
| `/health` shows `db: error` | Service account lacks write access to `data\`, or the path in `DATABASE_URL` does not exist. |
| 404 on `/api/v1/prd/invoices` | `FBR_ENABLED_ENVS` does not include `prd`. |
| Every call ends FAILED_UPSTREAM | No outbound route to `gw.fbr.gov.pk`: proxy variables, firewall, or DNS. Test with `Invoke-WebRequest https://gw.fbr.gov.pk/ -UseBasicParsing`. |
| AUTH_ERROR for one NTN only | Token for that NTN is wrong, expired, or issued for the other environment (sandbox token on `prd` or vice versa). |
| ABAP reports timeouts but the middleware logs SUBMITTED | ABAP/IIS timeout too short. Raise to at least 120 s (ARR: 180 s) and have ABAP call GET before resubmitting. |
| `pip install` fails on `pydantic-core` | Wrong Python (3.14 or 32-bit). Install 3.12 64-bit and recreate `.venv`. |
| `pip install` hangs or "connection timed out" | No route to PyPI from the server. Set `HTTPS_PROXY` in the shell, or use the offline wheelhouse from section 1a. |
| `Invoke-WebRequest` to PyPI or PRAL fails with "underlying connection was closed" | TLS 1.2 not enabled in the session or the OS. Run the `SecurityProtocol` line from section 1a; on Server 2012 R2 enable TLS 1.2 in the registry. |
