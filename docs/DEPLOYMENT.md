# Deploying the FBR Middleware on Windows Server

How we run the FBR middleware on the PRL machine, following the same pattern as the OGRA API that
already runs there: plain Python, supervised by NSSM, configuration in a `.env` file, logs on disk.
No Docker, no IIS dependency unless TLS is terminated there.

Everything here assumes an **elevated PowerShell** on the target machine: Start menu, type
`PowerShell`, right-click, "Run as administrator". The prompt reads `PS C:\>`. If it reads `C:\>`
you are in cmd.exe, where `;` is not a command separator and the commands below fail with "The
filename, directory name, or volume label syntax is incorrect"; type `powershell` there first, or
join commands with `&&` and use `cd /d`.

The machine already hosts `OgraApi` on port 8000 with NSSM at `C:\nssm`, so this guide reuses
that NSSM and never touches port 8000, the OGRA directory, or the Python the OGRA service uses.

Scripts: `deploy\windows\preflight.ps1` (read-only checks), `deploy\windows\install-service.ps1`
(does everything in sections 3 to 7), `deploy\windows\uninstall-service.ps1` (reverses it).

Addresses used throughout:

| Machine | Address | Role |
|---|---|---|
| Middleware (this server) | `10.0.0.86`, port `8080` | runs `FbrMiddleware`; SAP calls `http://10.0.0.86:8080/api/v1/...` (HTTPS once section 8 is done) |
| SAP DEV application server | `10.0.0.38` (SID `DEV`, instance `00`) | the only caller allowed through the firewall for now |
| SAP QAS / PRD application servers | to be added | add to the firewall rule and the API key list before each system goes live |
| OgraApi (same server) | `10.0.0.86`, port `8000` | existing service; untouched |

---

## 1. What runs the process

A Windows Server has no `systemd`. A process started from a console dies with the session and does
not return after a reboot, so "it works when I run it" is not a deployment.

| Approach | Fit for this service |
|---|---|
| **NSSM**, wraps any executable as a service | yes: restarts on crash, captures stdout/stderr, no code changes; same tool as OgraApi |
| IIS + HttpPlatformHandler | only if IIS is wanted for TLS; it then proxies to the NSSM service, it does not replace it |
| Task Scheduler at startup | no supervision; a crash stays crashed |

The middleware is one Python process: `python.exe -m fbr_middleware` starts uvicorn single-process
on `LISTEN_HOST:LISTEN_PORT` from `.env`. It handles both PRAL environments through the URL
(`/api/v1/dev/...` sandbox, `/api/v1/prd/...` production), so one service is enough.

---

## 2. Get the code, then pre-flight

The checks live in the repository, so the code comes first. Nothing is installed by this step.

```powershell
git clone https://github.com/aliyan774/fbr-integration.git C:\fbr-middleware
```

Without git on the server, or without credentials for the repository there: download the zip from
GitHub (Code, Download ZIP) on a workstation, copy it over AnyDesk, then

```powershell
Expand-Archive -Path C:\Users\<you>\Downloads\fbr-integration-main.zip -DestinationPath C:\ -Force
Rename-Item C:\fbr-integration-main C:\fbr-middleware
```

Now run the checks:

```powershell
cd C:\fbr-middleware; powershell -ExecutionPolicy Bypass -File deploy\windows\preflight.ps1 -Root C:\fbr-middleware -Port 8080
```

It prints PASS / WARN / FAIL for each item and exits 1 on any FAIL. What it looks at, and why:

| Check | Why it matters here |
|---|---|
| Every port already listening, with its process | OgraApi holds 8000; the middleware needs its own port. The script names the first free candidate from 8080, 8443, 8081, 8444 |
| Python 3.12 64-bit, python.org build | pydantic-core has no wheel for 3.14; the Store build breaks venvs under a service account. Pass `-Python <python.exe>` to check a specific interpreter |
| Python used by other NSSM services | prints OgraApi's interpreter and its venv base. Leave it alone; reuse its base only if it is 3.12 and machine-wide |
| Other Python installations on PATH | OgraApi's install script relies on `python` on PATH. **Do not change PATH.** Install 3.12 without "Add to PATH" and point this install at it with `-Python` (section 3) |
| PyPI reachable | `pip install` needs it; offline wheelhouse procedure in section 11 |
| `gw.fbr.gov.pk` reachable and resolvable | otherwise every submission ends `FAILED_UPSTREAM`; test from the server, not a workstation |
| Proxy variables | httpx honours `HTTPS_PROXY`; it must be in the service environment, not only the admin's profile |
| IIS bindings on 443 | only relevant if IIS terminates TLS; a second site on 443 needs its own host name |
| Firewall rule name, existing service, NSSM present, disk, clock, time zone | avoid duplicates; `invoiceDate` validation depends on the clock |

Manual equivalents of the two that matter most:

```powershell
# everything listening, with the owning process (8000 will show OgraApi's python)
Get-NetTCPConnection -State Listen | Sort-Object LocalPort -Unique |
  Select-Object LocalPort, LocalAddress, @{n="Process";e={(Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName}} |
  Format-Table -AutoSize

# the interpreter this service will use; must print 3.12.x and 64
py -3.12 -c "import sys, struct; print(sys.version, struct.calcsize('P')*8)"
```

If the pre-flight reports no Python 3.12 (as on the PRL machine: `python` is not on PATH and
OgraApi's interpreter is not registered machine-wide), install it for all users **without**
touching PATH. Download the 64-bit installer for the latest 3.12.x from python.org, then:

```powershell
.\python-3.12.10-amd64.exe /quiet InstallAllUsers=1 PrependPath=0 Include_launcher=1 Include_test=0
& "C:\Program Files\Python312\python.exe" --version
powershell -ExecutionPolicy Bypass -File deploy\windows\preflight.ps1 -Root C:\fbr-middleware -Port 8080 -Python "C:\Program Files\Python312\python.exe"
```

`PrependPath=0` leaves `python` on PATH exactly as OgraApi's installer found it. A per-user Python
(under `C:\Users\...`) is not usable for this service: it runs as NETWORK SERVICE, which cannot
read another user's profile, and a venv needs its base install at run time.

Write the chosen port down. It goes into `.env` (section 3) and nowhere else; the install script
reads it from there for the port check, the firewall rule and the health check.

---

## 3. Lay out the directory

Outside any user profile, mirroring `C:\ogra-api`:

```
C:\fbr-middleware\        git checkout of this repository (src\, deploy\, docs\, pyproject.toml)
  venv\                   created in section 3, used by the service and by operators
  .env                    settings, ACL-restricted; the only place the port and API keys live
  data\                   fbr_middleware.db (SQLite), created on first start
  logs\                   app.log (daily rotation, 30 kept), service.out.log, service.err.log
```

```powershell
New-Item -ItemType Directory -Force -Path C:\fbr-middleware\logs, C:\fbr-middleware\data | Out-Null
```

The checkout itself was made in section 2. Create the venv **inside** the directory from the 3.12 interpreter. On this machine `python` on
PATH belongs to the OGRA deployment, so name the interpreter explicitly:

```powershell
cd C:\fbr-middleware
py -3.12 -m venv venv                                  # or: & "C:\Program Files\Python312\python.exe" -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -e .
.\venv\Scripts\python.exe -m pip check
.\venv\Scripts\python.exe -c "import fbr_middleware; print(fbr_middleware.__version__)"
```

### Configuration and secrets

```powershell
Copy-Item .env.example .env; notepad .env
```

Production values:

```ini
FBR_BASE_URL=https://gw.fbr.gov.pk
FBR_ENABLED_ENVS=dev,prd              # "dev" only on a box that must never reach production
FBR_TIMEOUT_SECONDS=30
FBR_MAX_RETRIES_5XX=3
MAX_INFLIGHT_PER_SELLER=1
MAX_INFLIGHT_TOTAL=50
MIDDLEWARE_API_KEYS=<long random key for SAP PRD>,<key for SAP QAS>
DATABASE_URL=sqlite+aiosqlite:///./data/fbr_middleware.db
LOG_DIR=./logs
LOG_LEVEL=INFO
LISTEN_HOST=0.0.0.0                   # 127.0.0.1 when IIS on this box is the only caller
LISTEN_PORT=8080                      # the free port from section 2; never 8000
```

Generate a key: `[Convert]::ToBase64String((1..48 | % { Get-Random -Max 256 }) -as [byte[]])`.
Leave `FBR_DEV_BASE_URL` and `FBR_PRD_BASE_URL` unset; they exist to point `dev` at the local mock
during development. PRAL tokens are never stored on this server; SAP sends them per request.

Keep secrets in this file, never in the service definition: arguments passed to NSSM are visible in
the registry and in `Get-CimInstance Win32_Service`. Strip inherited permissions so the file is not
readable by every account on the box (the service account is granted in section 6):

```powershell
icacls C:\fbr-middleware\.env /inheritance:r /grant:r "Administrators:(R,W)" /grant:r "SYSTEM:(R)" /grant:r "NETWORK SERVICE:(R)"
```

### Prove it in the foreground once

```powershell
cd C:\fbr-middleware; .\venv\Scripts\python.exe -m fbr_middleware
```

In a second window `Invoke-RestMethod http://localhost:8080/health` must show `status: ok`,
`db: ok` and both `dev` and `prd` under `environments` with the PRAL URLs. Ctrl+C to stop. A
service that fails to start is far harder to diagnose than a console run, so do this first.

---

## 4. Choose and open the port

Check nothing holds the port from `.env`, and only then open the firewall:

```powershell
Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue | Select-Object OwningProcess, State
```

Empty output means free. If something answers, find out what and pick another port in `.env`:

```powershell
Get-Process -Id (Get-NetTCPConnection -LocalPort 8080 -State Listen).OwningProcess
```

### Bind address

`LISTEN_HOST=0.0.0.0` listens on every interface; `127.0.0.1` only to the machine itself. Bind to
`127.0.0.1` when IIS on the same box is the only caller (section 8): then no firewall rule is needed
and the service cannot be reached directly even by accident.

### Firewall

```powershell
New-NetFirewallRule -DisplayName "FBR Middleware" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Domain,Private
Set-NetFirewallRule -DisplayName "FBR Middleware" -RemoteAddress 10.0.0.38              # SAP DEV app server; add QAS/PRD later
Get-NetFirewallRule -DisplayName "FBR Middleware" | Get-NetFirewallPortFilter
```

`-Profile Domain,Private` is deliberate: without it the port is open on `Public` too. Restricting
`RemoteAddress` to the SAP servers is the second line; the API key is the third.

---

## 5. NSSM

OgraApi already installed NSSM 2.24 at `C:\nssm\nssm.exe`; reuse it. If it is missing:

```powershell
Invoke-WebRequest -Uri https://nssm.cc/release/nssm-2.24.zip -OutFile $env:TEMP\nssm.zip -UseBasicParsing
Expand-Archive -Path $env:TEMP\nssm.zip -DestinationPath $env:TEMP\nssm-extract -Force
New-Item -ItemType Directory -Force -Path C:\nssm | Out-Null
Copy-Item $env:TEMP\nssm-extract\nssm-2.24\win64\nssm.exe C:\nssm\nssm.exe -Force
```

One self-contained executable; nothing to uninstall, and one copy serves every service on the box.

---

## 6. Register the service

`deploy\windows\install-service.ps1` runs all of this (section 12). The commands, for the record
and for when something has to be changed by hand:

```powershell
$nssm = "C:\nssm\nssm.exe"
$app  = "C:\fbr-middleware"

& $nssm install FbrMiddleware "$app\venv\Scripts\python.exe" "-m fbr_middleware"
& $nssm set FbrMiddleware AppDirectory  $app
& $nssm set FbrMiddleware DisplayName   "FBR Digital Invoicing Middleware"
& $nssm set FbrMiddleware Description   "SAP -> PRAL Digital Invoicing pass-through (dev + prd). Owner: integration team"
& $nssm set FbrMiddleware Start         SERVICE_AUTO_START
```

The executable is the real `python.exe` in the venv, not a `.bat`: a batch file exits as soon as
it has launched Python and NSSM then restarts it forever.

**`AppDirectory` is not optional.** Without it the process starts in `C:\Windows\System32` and the
relative paths in `.env` (`./data`, `./logs`) and `.env` itself silently resolve to the wrong place.

### Restart behaviour

```powershell
& $nssm set FbrMiddleware AppExit         Default Restart
& $nssm set FbrMiddleware AppRestartDelay 3000
& $nssm set FbrMiddleware AppThrottle     5000
& $nssm set FbrMiddleware AppStopMethodSkip 6          # Ctrl+C first, let uvicorn finish in-flight PRAL calls
& $nssm set FbrMiddleware AppStopMethodConsole 30000
```

`AppThrottle` is the one that matters: if the process dies within 5 s of starting, NSSM backs off
instead of spinning. Without it a bad `.env` produces thousands of restarts a minute.

### Logs

NSSM captures stdout and stderr, the only place a startup exception appears. Set rotation when you
set the path, not later.

```powershell
& $nssm set FbrMiddleware AppStdout       "$app\logs\service.out.log"
& $nssm set FbrMiddleware AppStderr       "$app\logs\service.err.log"
& $nssm set FbrMiddleware AppRotateFiles  1
& $nssm set FbrMiddleware AppRotateOnline 1
& $nssm set FbrMiddleware AppRotateBytes  10485760
```

The application's own log, `logs\app.log`, is written by the middleware with daily rotation and
the Authorization-redaction filter; `service.*.log` only hold what uvicorn prints to the console.

### Environment variables

```powershell
& $nssm set FbrMiddleware AppEnvironmentExtra "PYTHONUNBUFFERED=1" "TZ=Asia/Karachi"
# with a corporate proxy:
& $nssm set FbrMiddleware AppEnvironmentExtra "PYTHONUNBUFFERED=1" "TZ=Asia/Karachi" "HTTPS_PROXY=http://proxy.corp:8080" "NO_PROXY=localhost,127.0.0.1"
```

`PYTHONUNBUFFERED=1` so a crash does not lose the lines that explain it. `TZ=Asia/Karachi` because
the "invoice date not in the future" rule and PRAL's `dated` are Pakistan dates.

### Which account it runs as

Not `LocalSystem`. The service needs its own directory and a port, nothing more:

```powershell
& $nssm set FbrMiddleware ObjectName "NT AUTHORITY\NetworkService" ""
icacls C:\fbr-middleware /grant "NETWORK SERVICE:(OI)(CI)(M)"
```

The `.env` ACL from section 3 already grants `NETWORK SERVICE` read.

---

## 7. Start it, and prove it

```powershell
Start-Service FbrMiddleware; Start-Sleep 5; Get-Service FbrMiddleware
```

`Running` is necessary but not sufficient; NSSM reports Running while the app inside crash-loops.
Check the app itself:

```powershell
Invoke-RestMethod http://localhost:8080/health | ConvertTo-Json -Depth 4
Get-Content C:\fbr-middleware\logs\service.err.log -Tail 40
```

Then one real submission to `dev` with the sandbox token, body from `docs\samples\sale-invoice-dev.json`:

```powershell
$headers = @{ "X-API-Key" = "<key>"; "Authorization" = "Bearer <sandbox token>"; "Content-Type" = "application/json" }
$body = Get-Content C:\fbr-middleware\docs\samples\sale-invoice-dev.json -Raw
Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/v1/dev/invoices/validate -Headers $headers -Body $body | Select-Object status, errorDescriptions
Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/v1/dev/invoices -Headers $headers -Body $body | Select-Object status, fbrInvoiceNo, trackingId
```

Expect `VALIDATED`, then `SUBMITTED` with an FBR number. The same POST again returns 200 with
`replayed: true`. From the SAP DEV application server (10.0.0.38), prove the firewall rule:

```powershell
Invoke-RestMethod http://10.0.0.86:8080/health
```

If that times out while the local call works, the firewall rule or its `RemoteAddress` is wrong. Import `postman\FBR-Middleware.postman_collection.json` for the
full set.

**When the service will not start, read `service.err.log` first.** It holds the traceback, the
missing-module error, the port conflict. The Windows Event Log only records that the process exited.

---

## 8. TLS

SAP's SM59 destination should use HTTPS. Two options:

- **IIS as reverse proxy.** Install IIS with URL Rewrite and Application Request Routing, bind the
  company certificate on 443 (own host name if OgraApi or another site already uses 443), reverse
  proxy to `http://localhost:8080/`, ARR proxy timeout **200 s** (a submission can take two PRAL
  calls with retries), response buffering off. Set `LISTEN_HOST=127.0.0.1` and skip the firewall
  rule for 8080; open 443 instead.
- **uvicorn with the certificate.** Export the certificate and key as PEM and set in `.env`:
  `LISTEN_PORT=8443`, `SSL_CERTFILE=C:\fbr-middleware\cert.pem`, `SSL_KEYFILE=C:\fbr-middleware\key.pem`.
  Restart the service after every renewal.

---

## 9. SAP side

Covered in `docs\SAP-ABAP-Interface-Spec.md` section 8: SM59 destination type G to
`10.0.0.86` port `8080` (HTTP; switch to 443 / SSL active after section 8), STRUST import of the
certificate chain, `ZFBR_TOKEN` for the PRAL tokens, client timeout at
least 200 s, and GET before any resend after a 504 or a timeout.

---

## 10. Day-to-day

```powershell
Restart-Service FbrMiddleware                                   # after a deploy or an .env change
Stop-Service FbrMiddleware; Start-Service FbrMiddleware         # when a restart hangs
Get-Service FbrMiddleware | Select-Object Status, StartType
Get-Content C:\fbr-middleware\logs\service.err.log -Tail 50 -Wait   # follow the console log
Get-Content C:\fbr-middleware\logs\app.log -Tail 50 -Wait           # follow the application log, search by trackingId
& C:\nssm\nssm.exe dump FbrMiddleware                               # how the service is really registered
```

### A routine deploy

```powershell
Stop-Service FbrMiddleware
Copy-Item -Recurse -Force C:\fbr-middleware\data C:\fbr-middleware\backup\data-$(Get-Date -Format yyyyMMdd-HHmm)
cd C:\fbr-middleware; git pull
.\venv\Scripts\python.exe -m pip install -e .; .\venv\Scripts\python.exe -m pip check
Start-Service FbrMiddleware
git -C C:\fbr-middleware log -1 --oneline; Get-Service FbrMiddleware; Invoke-RestMethod http://localhost:8080/health
```

Confirm the running process has the new code: a restart that silently failed looks exactly like a
successful one. Tables are created on start if missing; a release that changes an existing table
says so in its README and, until Alembic is added, is applied by hand after the backup. Rollback:
stop, `git checkout <previous tag>`, reinstall, restore `data\` if the schema changed, start.

### Operations

| Topic | Where / how |
|---|---|
| Application log | `logs\app.log`, one line per step with `trackingId`; rotated at midnight, 30 days kept |
| Console log | `logs\service.out.log`, `logs\service.err.log`, rotated at 10 MB by NSSM |
| Health | `GET /health`: `db`, `inflight`, PRAL URLs per environment. Point the monitoring tool at it |
| Database | `data\fbr_middleware.db`. Back up with a VSS-aware tool. Tables `invoices`, `attempts` |
| UNKNOWN invoices | `SELECT env, erp_invoice_no, tracking_id, received_at FROM invoices WHERE status='UNKNOWN'`; reconcile on the FBR portal; the row blocks resubmission until cleared (admin endpoint is a TODO) |
| Token problems | `AUTH_ERROR` means PRAL rejected the token SAP sent; fix `ZFBR_TOKEN` in SAP, nothing on this server |
| Secrets hygiene | the token never appears in logs or the DB; a log line containing `Authorization` or a bearer value is an incident |
| Key rotation | add the new key to `MIDDLEWARE_API_KEYS`, restart, switch SAP, remove the old key, restart |

### Removing it

```powershell
cd C:\fbr-middleware; powershell -ExecutionPolicy Bypass -File deploy\windows\uninstall-service.ps1
```

Stops and removes the service and the firewall rule; the directory, `.env` and database stay.

---

## 11. Things that cost us time

**Port 8000 is OgraApi's.** The middleware defaults to 8080 and the install script refuses to
register the service while something else listens on `LISTEN_PORT`. If the service starts and dies
within seconds and `service.err.log` says `WinError 10048` / "address already in use", the port in
`.env` collides with another application.

**`python` on PATH is not ours.** OgraApi's installer uses `python` from PATH. Installing 3.12 with
"Add to PATH" would silently change which interpreter that service and its deploy script use. Install
3.12 without that option, use `py -3.12` or the absolute path, and pass `-Python` to our install script.

**Python 3.14 has no pydantic-core wheel.** `pip install` ends in `ResolutionImpossible`. Use 3.12.

**cmd is not PowerShell.** A `C:\>` prompt is cmd.exe: `;` does not separate commands there, so
`cd C:\fbr-middleware; powershell ...` fails with "The filename, directory name, or volume label
syntax is incorrect". Type `powershell` first, or in cmd write
`cd /d C:\fbr-middleware && powershell -ExecutionPolicy Bypass -File deploy\windows\preflight.ps1 -Root C:\fbr-middleware -Port 8080`.

**PowerShell is not bash.** `&&` and `||` are parser errors in Windows PowerShell 5.1; use `;` or
`if ($?) { ... }`. `curl` is an alias for `Invoke-WebRequest`; call `curl.exe` when you mean curl.
`Invoke-RestMethod` throws on 4xx/5xx; wrap in `try { } catch { $_.ErrorDetails.Message }` to see
the envelope of a 422 or 502.

**TLS 1.2.** On an older server `Invoke-WebRequest` to PyPI or PRAL fails with "underlying
connection was closed" until `[Net.ServicePointManager]::SecurityProtocol = 'Tls12'` is set in the
session (and enabled in the OS for Server 2012 R2).

**No route to PyPI.** Build a wheelhouse on a machine with internet and the same Python:
`py -3.12 -m pip download -d .\wheelhouse -e .` then on the server
`.\venv\Scripts\python.exe -m pip install --no-index --find-links .\wheelhouse -e .`.

**A dependency conflict can hide from `pip install`.** `pip check` reports it; pin the transitive
package in `pyproject.toml` once hit.

**A pull run as the wrong user leaves files that user owns.** The service account then cannot write
`data\` or `logs\`; the symptom is a permission error at startup. Re-run the `icacls` grant.

**A service does not pick up `.env` changes until it restarts.** Editing the file and re-running a
health check proves nothing. This includes API keys.

**Timeouts are long.** Worst case per submission is about 200 s (two PRAL calls, three attempts each,
backoff). ABAP's client timeout and any IIS ARR timeout must exceed that, or SAP sees a timeout while
the middleware completes and stores a result; SAP must then call GET before resubmitting.

**Check the reverse proxy points where you think.** Restarting the service while testing through
IIS can mean restarting one instance and testing another.

**Every submission ends `FAILED_UPSTREAM`.** No outbound route to `gw.fbr.gov.pk`: proxy variables
missing from the service environment, firewall, or DNS. `Invoke-WebRequest https://gw.fbr.gov.pk -UseBasicParsing`
from the server (any HTTP answer, even 404, proves the route).

**`AUTH_ERROR` for one NTN only.** The token for that NTN is wrong, expired, or issued for the other
environment (sandbox token on `prd` or the reverse).

---

## 12. The scripts

`deploy\windows\install-service.ps1` does sections 3 to 7 for this project: admin check, 3.12
interpreter check, venv and `pip install -e .`, NSSM (reuse `C:\nssm` or download), port check
against `LISTEN_PORT` from `.env`, service registration with restart, throttle, log rotation,
environment variables and the `NETWORK SERVICE` account, ACLs, the firewall rule, start, and a
`/health` check. Run it elevated from the app directory:

```powershell
cd C:\fbr-middleware
powershell -ExecutionPolicy Bypass -File deploy\windows\install-service.ps1 -Python "C:\Program Files\Python312\python.exe" -SapServers 10.0.0.38
```

It refuses to run without `C:\fbr-middleware\.env`, with an empty or default `MIDDLEWARE_API_KEYS`,
or while another process listens on `LISTEN_PORT`. That is deliberate: a service that starts
without its configuration, or on another application's port, is worse than one that will not start.

Parameters, if the defaults do not suit:

```powershell
.\deploy\windows\install-service.ps1 -AppDir C:\other -ServiceName OtherFbr -NssmDir C:\nssm -Python <python.exe> -SapServers <ip,ip> -HttpsProxy http://proxy:8080 -SkipFirewall
```

`-SkipFirewall` when IIS fronts the service and `LISTEN_HOST` is `127.0.0.1`.
`deploy\windows\uninstall-service.ps1 -ServiceName FbrMiddleware` reverses the service and firewall
parts. `deploy\windows\preflight.ps1` is section 2 and changes nothing.
