# ==========================================================================
# FBR Middleware - remove the Windows service (NSSM) and its firewall rule
# Run elevated:  powershell -ExecutionPolicy Bypass -File deploy\windows\uninstall-service.ps1
# The directory, venv, .env and data\ are left in place.
# ==========================================================================
param(
    [string]$ServiceName = "FbrMiddleware",
    [string]$NssmDir     = "C:\nssm"
)
$ErrorActionPreference = "Stop"
function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

$nssm = $null
$cmd = Get-Command nssm -ErrorAction SilentlyContinue
if ($cmd) { $nssm = $cmd.Source }
elseif (Test-Path "$NssmDir\nssm.exe") { $nssm = "$NssmDir\nssm.exe" }

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc) {
    if (-not $nssm) { Fail "Service '$ServiceName' exists but NSSM was not found at $NssmDir; cannot remove it." }
    & $nssm stop $ServiceName | Out-Null
    & $nssm remove $ServiceName confirm | Out-Null
    Write-Host "Service '$ServiceName' removed."
} else {
    Write-Host "Service '$ServiceName' not found."
}

$rule = Get-NetFirewallRule -DisplayName "FBR Middleware" -ErrorAction SilentlyContinue
if ($rule) {
    Remove-NetFirewallRule -DisplayName "FBR Middleware"
    Write-Host "Firewall rule 'FBR Middleware' removed."
}
Write-Host "Files under the app directory were not touched."
