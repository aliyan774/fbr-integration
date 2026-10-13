<#
.SYNOPSIS
    Installs (or reinstalls) the FBR Middleware as a Windows service with NSSM.

.DESCRIPTION
    Expects the layout from docs/DEPLOYMENT.md:
        <Root>\app        repository
        <Root>\.venv      virtual environment with the package installed
        <Root>\.env       settings
        <Root>\nssm.exe   NSSM binary
    Run as Administrator.

.EXAMPLE
    .\install-service.ps1 -Root C:\fbr-middleware
    .\install-service.ps1 -Root C:\fbr-middleware -HttpsProxy http://proxy.corp:8080
#>
[CmdletBinding()]
param(
    [string]$Root = "C:\fbr-middleware",
    [string]$ServiceName = "FbrMiddleware",
    [string]$HttpsProxy = "",
    [string]$HealthUrl = ""
)

$ErrorActionPreference = "Stop"

$nssm   = Join-Path $Root "nssm.exe"
$python = Join-Path $Root ".venv\Scripts\python.exe"
$app    = Join-Path $Root "app"
$envf   = Join-Path $Root ".env"
$logs   = Join-Path $Root "logs"
$data   = Join-Path $Root "data"

foreach ($p in @($nssm, $python, $app, $envf)) {
    if (-not (Test-Path $p)) { throw "Missing: $p (see docs/DEPLOYMENT.md sections 2-4)" }
}
New-Item -ItemType Directory -Force $logs, $data | Out-Null

$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Service $ServiceName exists; stopping and removing it first."
    & $nssm stop $ServiceName | Out-Null
    & $nssm remove $ServiceName confirm | Out-Null
}

& $nssm install $ServiceName $python "-m fbr_middleware"
& $nssm set $ServiceName AppDirectory $app
$envExtra = "ENV_FILE=$envf"
if ($HttpsProxy) { $envExtra = "$envExtra HTTPS_PROXY=$HttpsProxy NO_PROXY=localhost,127.0.0.1" }
& $nssm set $ServiceName AppEnvironmentExtra $envExtra
& $nssm set $ServiceName DisplayName "FBR Digital Invoicing Middleware"
& $nssm set $ServiceName Description "SAP -> PRAL Digital Invoicing pass-through (dev + prd)"
& $nssm set $ServiceName AppStdout (Join-Path $logs "service-out.log")
& $nssm set $ServiceName AppStderr (Join-Path $logs "service-err.log")
& $nssm set $ServiceName AppRotateFiles 1
& $nssm set $ServiceName AppRotateBytes 10485760
& $nssm set $ServiceName AppStopMethodSkip 6
& $nssm set $ServiceName AppStopMethodConsole 30000
& $nssm set $ServiceName Start SERVICE_AUTO_START
& $nssm set $ServiceName AppExit Default Restart
& $nssm set $ServiceName AppRestartDelay 5000

# The virtual service account exists once the service is registered.
icacls $envf /inheritance:r /grant:r "NT SERVICE\${ServiceName}:R" "Administrators:F" | Out-Null
icacls $data /grant "NT SERVICE\${ServiceName}:(OI)(CI)M" | Out-Null
icacls $logs /grant "NT SERVICE\${ServiceName}:(OI)(CI)M" | Out-Null

& $nssm start $ServiceName
Start-Sleep -Seconds 3
& $nssm status $ServiceName

if (-not $HealthUrl) {
    $port = (Get-Content $envf | Where-Object { $_ -match '^\s*LISTEN_PORT\s*=' } | ForEach-Object { ($_ -split '=')[1].Trim() } | Select-Object -First 1)
    if (-not $port) { $port = 8000 }
    $HealthUrl = "http://localhost:$port/health"
}
try {
    $h = Invoke-RestMethod $HealthUrl
    Write-Host "Health: status=$($h.status) db=$($h.db) environments=$(($h.environments | Get-Member -MemberType NoteProperty).Name -join ',')"
} catch {
    Write-Warning "Health check at $HealthUrl failed: $($_.Exception.Message). See $logs\service-err.log"
}
