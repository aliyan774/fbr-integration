# ==========================================================================
# FBR Middleware - Windows service installation (NSSM)
#
# Run in an ELEVATED PowerShell from the app directory on the target machine:
#   cd C:\fbr-middleware
#   powershell -ExecutionPolicy Bypass -File deploy\windows\install-service.ps1 `
#       -Python "C:\Program Files\Python312\python.exe" -SapServers 10.0.0.11,10.0.0.12
#
# What it does:
#   0. Preconditions: admin shell, app present, .env present with LISTEN_PORT and real API keys
#   1. Verifies the Python interpreter is 3.12 64-bit (never touches PATH or other apps' Python)
#   2. Creates the venv + installs the package (pip install -e .) + pip check
#   3. Locates NSSM (PATH, C:\nssm) or downloads 2.24
#   4. Checks LISTEN_PORT is free (another application already runs on this server)
#   5. Registers the "FbrMiddleware" service: auto-start, restart + throttle, log rotation,
#      PYTHONUNBUFFERED / TZ, NETWORK SERVICE account, directory and .env ACLs
#   6. Opens the firewall port (Domain,Private; optionally restricted to the SAP servers)
#   7. Starts the service and checks /health
# ==========================================================================

param(
    [string]$AppDir      = "C:\fbr-middleware",
    [string]$ServiceName = "FbrMiddleware",
    [string]$NssmDir     = "C:\nssm",
    [string]$Python      = "",          # python.exe 3.12; default: py -3.12, then python on PATH
    [string]$SapServers  = "",          # comma-separated IPs/CIDRs allowed inbound; empty = no address restriction
    [string]$HttpsProxy  = "",          # e.g. http://proxy.corp:8080, set in the service environment
    [switch]$SkipFirewall               # when IIS on this box is the only caller (LISTEN_HOST=127.0.0.1)
)

$ErrorActionPreference = "Stop"

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function Step($n, $msg) { Write-Host "==> $n/7 $msg" -ForegroundColor Cyan }

# --- 0. Preconditions ------------------------------------------------------
$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail "This script must run in an elevated (Administrator) PowerShell."
}
if (-not (Test-Path "$AppDir\pyproject.toml") -or -not (Test-Path "$AppDir\src\fbr_middleware\main.py")) {
    Fail "App not found at $AppDir. Clone or copy the repository there first (see docs\DEPLOYMENT.md section 3)."
}
$envFile = "$AppDir\.env"
if (-not (Test-Path $envFile)) {
    Fail "$envFile is missing. Copy .env.example to .env and set production values first."
}

# read the few .env values this script needs (no quotes handling beyond trimming)
$envValues = @{}
foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
    $k, $v = $line -split '=', 2
    $envValues[$k.Trim()] = $v.Trim().Trim('"').Trim("'")
}
$port = 0
if ($envValues.ContainsKey("LISTEN_PORT")) { $port = [int]$envValues["LISTEN_PORT"] }
if ($port -le 0) { Fail "LISTEN_PORT is not set in .env. Pick a free port (preflight.ps1 lists what is in use) and set it." }
$listenHost = if ($envValues.ContainsKey("LISTEN_HOST")) { $envValues["LISTEN_HOST"] } else { "0.0.0.0" }
$apiKeys = if ($envValues.ContainsKey("MIDDLEWARE_API_KEYS")) { $envValues["MIDDLEWARE_API_KEYS"] } else { "" }
if (-not $apiKeys -or $apiKeys -like "*change-me*") {
    Fail "MIDDLEWARE_API_KEYS in .env is empty or still the example value. Set a real key per SAP system."
}
Write-Host "    AppDir=$AppDir  Service=$ServiceName  LISTEN_HOST=$listenHost  LISTEN_PORT=$port"

# --- 1. Python -------------------------------------------------------------
Step 1 "Checking the Python interpreter"
if (-not $Python) {
    $viaPy = $null
    try { $viaPy = (& py -3.12 -c "import sys; print(sys.executable)" 2>$null | Out-String).Trim() } catch { $viaPy = $null }
    if ($viaPy -and (Test-Path $viaPy)) { $Python = $viaPy }
    else {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if ($cmd) { $Python = $cmd.Source }
    }
}
if (-not $Python -or -not (Test-Path $Python)) {
    Fail "No Python 3.12 found. Install 3.12 64-bit from python.org WITHOUT 'Add to PATH' (another app owns PATH) and pass -Python <path to python.exe>."
}
$pyInfo = (& $Python -c "import sys, struct; print(sys.version_info[0], sys.version_info[1], struct.calcsize('P')*8, sys.executable)" | Out-String).Trim()
$parts = $pyInfo -split ' ', 4
if ($parts[0] -ne "3" -or $parts[1] -ne "12" -or $parts[2] -ne "64") {
    Fail "Interpreter at $Python is '$pyInfo'; need Python 3.12 64-bit (3.14 has no pydantic-core wheel)."
}
if ($Python -like "*WindowsApps*") { Fail "The Store build of Python cannot be used for a service. Install from python.org." }
Write-Host "    Using: $Python (3.12, 64-bit)"

# --- 2. Virtual environment ------------------------------------------------
Step 2 "Creating venv + installing the package"
$venvPy = "$AppDir\venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    & $Python -m venv "$AppDir\venv"
    if (-not (Test-Path $venvPy)) { Fail "venv creation failed at $AppDir\venv" }
}
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -e $AppDir --quiet
& $venvPy -m pip check
& $venvPy -c "import fbr_middleware; print('    fbr_middleware', fbr_middleware.__version__)"
New-Item -ItemType Directory -Force -Path "$AppDir\logs", "$AppDir\data" | Out-Null

# --- 3. NSSM ---------------------------------------------------------------
Step 3 "Locating NSSM"
$nssm = $null
$cmd = Get-Command nssm -ErrorAction SilentlyContinue
if ($cmd) { $nssm = $cmd.Source }
elseif (Test-Path "$NssmDir\nssm.exe") { $nssm = "$NssmDir\nssm.exe" }
else {
    Write-Host "    Downloading NSSM 2.24..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $zip = "$env:TEMP\nssm.zip"
    Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath "$env:TEMP\nssm-extract" -Force
    New-Item -ItemType Directory -Force -Path $NssmDir | Out-Null
    Copy-Item "$env:TEMP\nssm-extract\nssm-2.24\win64\nssm.exe" "$NssmDir\nssm.exe" -Force
    $nssm = "$NssmDir\nssm.exe"
}
Write-Host "    Using NSSM at: $nssm"

# --- 4. Port check ---------------------------------------------------------
Step 4 "Checking port $port is free"
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing -and $existing.Status -eq "Running") {
    Write-Host "    Service $ServiceName is running; stopping it before the port check."
    & $nssm stop $ServiceName | Out-Null
    Start-Sleep -Seconds 3
}
$holder = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($holder) {
    $pn = (Get-Process -Id $holder.OwningProcess -ErrorAction SilentlyContinue).ProcessName
    Fail "Port $port is already in use by '$pn' (pid $($holder.OwningProcess)). Another application is deployed on this server: pick a free LISTEN_PORT in .env (run deploy\windows\preflight.ps1 for the inventory) and re-run."
}
Write-Host "    Port $port is free."

# --- 5. Service registration -------------------------------------------------
Step 5 "Registering service '$ServiceName'"
if ($existing) {
    Write-Host "    Service exists - removing the old registration."
    & $nssm remove $ServiceName confirm | Out-Null
    Start-Sleep -Seconds 2
}

& $nssm install $ServiceName $venvPy "-m fbr_middleware"
& $nssm set $ServiceName AppDirectory  $AppDir
& $nssm set $ServiceName DisplayName   "FBR Digital Invoicing Middleware"
& $nssm set $ServiceName Description   "SAP -> PRAL Digital Invoicing pass-through (dev + prd). Owner: integration team"
& $nssm set $ServiceName Start         SERVICE_AUTO_START
# restart on crash, throttle rapid restart loops, graceful stop (let in-flight PRAL calls finish)
& $nssm set $ServiceName AppExit            Default Restart
& $nssm set $ServiceName AppRestartDelay    3000
& $nssm set $ServiceName AppThrottle        5000
& $nssm set $ServiceName AppStopMethodSkip  6
& $nssm set $ServiceName AppStopMethodConsole 30000
# console log capture + rotation (10 MB); the app's own log is logs\app.log
& $nssm set $ServiceName AppStdout       "$AppDir\logs\service.out.log"
& $nssm set $ServiceName AppStderr       "$AppDir\logs\service.err.log"
& $nssm set $ServiceName AppRotateFiles  1
& $nssm set $ServiceName AppRotateOnline 1
& $nssm set $ServiceName AppRotateBytes  10485760
# environment: unbuffered output, Pakistan time for invoiceDate checks, optional proxy
$envExtra = @("PYTHONUNBUFFERED=1", "TZ=Asia/Karachi")
if ($HttpsProxy) { $envExtra += "HTTPS_PROXY=$HttpsProxy"; $envExtra += "NO_PROXY=localhost,127.0.0.1" }
& $nssm set $ServiceName AppEnvironmentExtra @envExtra
# least-privilege account
& $nssm set $ServiceName ObjectName "NT AUTHORITY\NetworkService" ""
icacls $AppDir /grant "NETWORK SERVICE:(OI)(CI)(M)" | Out-Null
icacls $envFile /inheritance:r /grant:r "Administrators:(R,W)" /grant:r "SYSTEM:(R)" /grant:r "NETWORK SERVICE:(R)" | Out-Null
Write-Host "    Service registered (NETWORK SERVICE, auto-start, restart with throttle, logs in $AppDir\logs)."

# --- 6. Firewall -------------------------------------------------------------
Step 6 "Firewall rule (inbound TCP $port)"
if ($SkipFirewall -or $listenHost -eq "127.0.0.1") {
    Write-Host "    Skipped: service listens on $listenHost / -SkipFirewall given (IIS fronts it)."
} else {
    $rule = Get-NetFirewallRule -DisplayName "FBR Middleware" -ErrorAction SilentlyContinue
    if (-not $rule) {
        New-NetFirewallRule -DisplayName "FBR Middleware" -Direction Inbound -Protocol TCP `
            -LocalPort $port -Action Allow -Profile Domain,Private | Out-Null
        Write-Host "    Rule created (Domain,Private)."
    } else {
        Set-NetFirewallRule -DisplayName "FBR Middleware" -LocalPort $port | Out-Null
        Write-Host "    Rule exists; port updated to $port."
    }
    if ($SapServers) {
        Set-NetFirewallRule -DisplayName "FBR Middleware" -RemoteAddress ($SapServers -split ',') | Out-Null
        Write-Host "    Restricted to: $SapServers"
    }
}

# --- 7. Start + health check -------------------------------------------------
Step 7 "Starting service"
& $nssm start $ServiceName | Out-Null
Start-Sleep -Seconds 5

try {
    $resp = Invoke-RestMethod -Uri "http://localhost:$port/health" -TimeoutSec 15
    $envs = ($resp.environments | Get-Member -MemberType NoteProperty | ForEach-Object { $_.Name }) -join ", "
    if ($resp.status -eq "ok") {
        Write-Host ""
        Write-Host "SUCCESS - service '$ServiceName' is running." -ForegroundColor Green
        Write-Host "  Health:        http://localhost:$port/health   (db=$($resp.db), environments: $envs)"
        Write-Host "  Submit (dev):  POST http://<host>:$port/api/v1/dev/invoices"
        Write-Host "  Submit (prd):  POST http://<host>:$port/api/v1/prd/invoices"
        Write-Host "  OpenAPI:       http://localhost:$port/docs"
        Write-Host "  Logs:          $AppDir\logs\  (app.log, service.out.log, service.err.log)"
        Write-Host "  Manage:        nssm start|stop|restart|status $ServiceName"
    } else {
        Write-Host "Service is up but /health reports status '$($resp.status)' (db=$($resp.db))." -ForegroundColor Yellow
    }
} catch {
    Write-Host ""
    Write-Host "Service registered but /health did not respond yet." -ForegroundColor Yellow
    Write-Host "Check: Get-Service $ServiceName ; Get-Content $AppDir\logs\service.err.log -Tail 40"
}
