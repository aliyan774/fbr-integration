# ==========================================================================
# FBR Middleware - build a ready-to-run Postman collection on the server
#
# Run in an ELEVATED PowerShell (the .env file is readable by Administrators only):
#   cd C:\fbr-middleware
#   powershell -ExecutionPolicy Bypass -File deploy\windows\make-filled-collection.ps1 -Token <sandbox token>
#
# What it does:
#   1. Reads the API key and port from .env (the key is never printed)
#   2. Fills postman\FBR-Middleware-PRL-inline.postman_collection.json with the key and the
#      PRAL sandbox token(s) and writes the result OUTSIDE git tracking
#   3. Calls the local /api/v1/dev/token-check to prove the key and the token work
#
# The output file contains secrets: keep it on this machine, do not commit or mail it.
# ==========================================================================

param(
    [Parameter(Mandatory = $true)][string]$Token,          # portal "Token" (postinvoicedata_sb)
    [string]$ValidateToken = "",                            # portal "Sandbox Security Token"; default: same as -Token
    [string]$PrdToken = "",                                 # production token, when it exists
    [string]$AppDir = "C:\fbr-middleware",
    [string]$Out = ""
)

$ErrorActionPreference = "Stop"
function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

if (-not $ValidateToken) { $ValidateToken = $Token }
if (-not $Out) { $Out = Join-Path $AppDir "FBR-sandbox-filled.postman_collection.json" }

$envFile = Join-Path $AppDir ".env"
$source  = Join-Path $AppDir "postman\FBR-Middleware-PRL-inline.postman_collection.json"
if (-not (Test-Path $source)) { Fail "$source not found. Run 'git pull' in $AppDir first." }

# --- 1. key and port from .env ------------------------------------------------
try { $lines = Get-Content $envFile -ErrorAction Stop }
catch { Fail "Cannot read $envFile ($($_.Exception.Message)). Open PowerShell with 'Run as administrator' and try again." }

$values = @{}
foreach ($line in $lines) {
    if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
    $k, $v = $line -split '=', 2
    $values[$k.Trim()] = $v.Trim().Trim('"').Trim("'")
}
$key = ""
if ($values.ContainsKey("MIDDLEWARE_API_KEYS")) { $key = ($values["MIDDLEWARE_API_KEYS"] -split ',')[0].Trim() }
if (-not $key) { Fail "MIDDLEWARE_API_KEYS is empty in $envFile." }
$port = 8003
if ($values.ContainsKey("LISTEN_PORT")) { $port = [int]$values["LISTEN_PORT"] }
Write-Host "==> 1/3 Read .env: API key of $($key.Length) characters, port $port" -ForegroundColor Cyan

# --- 2. fill the collection -----------------------------------------------------
$text = Get-Content $source -Raw
$text = $text.Replace("REPLACE_WITH_SANDBOX_VALIDATE_TOKEN", $ValidateToken)
$text = $text.Replace("REPLACE_WITH_SANDBOX_TOKEN", $Token)
$text = $text.Replace("REPLACE_WITH_API_KEY", $key)
if ($PrdToken) { $text = $text.Replace("REPLACE_WITH_PRODUCTION_TOKEN", $PrdToken) }
[System.IO.File]::WriteAllText($Out, $text, (New-Object System.Text.UTF8Encoding($false)))
$left = [regex]::Matches($text, 'REPLACE_WITH_\w+') | ForEach-Object { $_.Value } | Sort-Object -Unique
Write-Host "==> 2/3 Wrote $Out" -ForegroundColor Cyan
Write-Host "    Placeholders left: $(if ($left) { $left -join ', ' } else { 'none' })$(if ($left -contains 'REPLACE_WITH_PRODUCTION_TOKEN') { '  (expected until the production token exists)' })"

# --- 3. prove key + token against the running service ---------------------------
Write-Host "==> 3/3 Token check through the local middleware" -ForegroundColor Cyan
try {
    $r = Invoke-RestMethod -Uri "http://localhost:$port/api/v1/dev/token-check" -TimeoutSec 60 `
        -Headers @{ "X-API-Key" = $key; "Authorization" = "Bearer $Token" }
    Write-Host "    tokenStatus=$($r.tokenStatus)  PRAL HTTP $($r.pralHttpStatus)  provinces=$(@($r.provinces).Count)  $($r.elapsedMs) ms" -ForegroundColor Green
    Write-Host ""
    Write-Host "Import $Out into Postman on this machine, or replay it without Postman:"
    Write-Host "  .\venv\Scripts\python.exe tools\run_collection.py `"$Out`" --base-url http://localhost:$port"
} catch {
    $detail = $_.ErrorDetails.Message
    if (-not $detail) { $detail = $_.Exception.Message }
    Write-Host "    Token check did not pass: $detail" -ForegroundColor Yellow
    Write-Host "    404 = service is running old code (git pull, then Restart-Service FbrMiddleware)."
    Write-Host "    401 Invalid X-API-Key = service was not restarted after .env changed."
    Write-Host "    502 AUTH_ERROR = PRAL rejected the token."
}
