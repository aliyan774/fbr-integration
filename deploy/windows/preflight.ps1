<#
.SYNOPSIS
    Pre-flight checks for deploying the FBR Middleware on a Windows Server.

.DESCRIPTION
    Read-only. Verifies everything the install needs before you start: ports already in use
    (another application runs on this server), Python version, PyPI reachability (pip install),
    outbound reach to the PRAL gateway, proxy settings, TLS 1.2, NSSM, disk space, clock sync,
    PowerShell version and admin rights. Layout per docs\DEPLOYMENT.md: the repo at -Root with
    venv\ and .env inside it, NSSM shared at C:
ssm.
    Prints PASS / WARN / FAIL per check and exits 1 if any check failed.

.EXAMPLE
    .\preflight.ps1
    .\preflight.ps1 -Port 8080 -Root C:\fbr-middleware -FbrHost gw.fbr.gov.pk
#>
[CmdletBinding()]
param(
    [string]$Root = "C:\fbr-middleware",
    [int]$Port = 8080,
    [string]$FbrHost = "gw.fbr.gov.pk",
    [string]$NssmDir = "C:\nssm",
    [int]$MinFreeGB = 5
)

$script:failed = 0
function Report([string]$status, [string]$name, [string]$detail) {
    $color = switch ($status) { "PASS" { "Green" } "WARN" { "Yellow" } default { "Red" } }
    Write-Host ("[{0}] {1,-38} {2}" -f $status, $name, $detail) -ForegroundColor $color
    if ($status -eq "FAIL") { $script:failed++ }
}

# 1. Admin rights
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Report ($(if ($isAdmin) { "PASS" } else { "WARN" })) "Administrator shell" $(if ($isAdmin) { "yes" } else { "no: service install and icacls will fail" })

# 2. PowerShell version
Report ($(if ($PSVersionTable.PSVersion.Major -ge 5) { "PASS" } else { "FAIL" })) "PowerShell version" $PSVersionTable.PSVersion.ToString()

# 3. Python 3.12 64-bit on PATH
try {
    $pyVer = (& python --version 2>&1 | Out-String).Trim()
    $arch = (& python -c "import struct,sys;print(struct.calcsize('P')*8, sys.executable)" 2>&1 | Out-String).Trim()
    $ok = $pyVer -match "^Python 3\.12\." -and $arch -like "64 *"
    Report ($(if ($ok) { "PASS" } else { "FAIL" })) "Python 3.12 64-bit" "$pyVer ($arch)"
    $store = $arch -like "*WindowsApps*"
    if ($store) { Report "FAIL" "Python is the Store build" "install from python.org and remove the App Execution Alias" }
} catch {
    Report "FAIL" "Python 3.12 64-bit" "python not found on PATH"
}
try {
    $launcher = (& py -0p 2>&1 | Out-String).Trim()
    Report "PASS" "py launcher interpreters" ($launcher -replace "`r?`n", " | ")
} catch { Report "WARN" "py launcher" "not installed (optional)" }

# 4. TLS 1.2 for this session (older servers default to TLS 1.0 and fail against PyPI / PRAL)
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Report "PASS" "TLS 1.2 enabled for checks" ([Net.ServicePointManager]::SecurityProtocol.ToString())
} catch { Report "WARN" "TLS 1.2" $_.Exception.Message }

# 5. Proxy settings
$proxyEnv = @("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY") | ForEach-Object { if (Test-Path "env:$_") { "$_=$((Get-Item "env:$_").Value)" } }
$sysProxy = (Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" -ErrorAction SilentlyContinue)
$proxyDetail = if ($proxyEnv) { $proxyEnv -join "; " } elseif ($sysProxy.ProxyEnable -eq 1) { "system proxy $($sysProxy.ProxyServer) (set HTTPS_PROXY for the service too)" } else { "none" }
Report "PASS" "Proxy configuration" $proxyDetail

# 6. PyPI reachable (needed for pip install)
try {
    $r = Invoke-WebRequest https://pypi.org/simple/pip/ -UseBasicParsing -TimeoutSec 10
    Report ($(if ($r.StatusCode -eq 200) { "PASS" } else { "FAIL" })) "PyPI reachable (pip install)" "HTTP $($r.StatusCode)"
} catch { Report "FAIL" "PyPI reachable (pip install)" $_.Exception.Message }

# 7. PRAL gateway reachable (any HTTP answer proves the route; 404/403 is fine)
try {
    $r = Invoke-WebRequest "https://$FbrHost" -UseBasicParsing -TimeoutSec 10
    Report "PASS" "PRAL gateway reachable" "https://$FbrHost HTTP $($r.StatusCode)"
} catch {
    $resp = $_.Exception.Response
    if ($resp -and $resp.StatusCode) {
        Report "PASS" "PRAL gateway reachable" "https://$FbrHost HTTP $([int]$resp.StatusCode) (route and TLS OK)"
    } else {
        Report "FAIL" "PRAL gateway reachable" "https://$FbrHost : $($_.Exception.Message)"
    }
}
try {
    $dns = Resolve-DnsName $FbrHost -ErrorAction Stop | Select-Object -First 1
    Report "PASS" "DNS for PRAL gateway" "$FbrHost -> $($dns.IPAddress)"
} catch { Report "FAIL" "DNS for PRAL gateway" $_.Exception.Message }

# 8. Port inventory: everything already listening on this server (another application is deployed here)
$allListening = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Sort-Object LocalPort -Unique)
$portList = ($allListening | ForEach-Object {
    $pn = (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName
    "$($_.LocalPort)=$pn"
}) -join ", "
Report "PASS" "Ports already in use" $(if ($portList) { $portList } else { "none" })
foreach ($cand in @($Port, 8080, 8443, 8081, 8444) | Select-Object -Unique) {
    if (-not ($allListening | Where-Object { $_.LocalPort -eq $cand })) {
        if ($cand -eq $Port) { Report "PASS" "First free candidate port" "${cand}: your chosen port is free, use it as LISTEN_PORT" }
        else { Report "WARN" "First free candidate port" "${cand}: chosen port $Port is in use, set LISTEN_PORT=$cand (and the firewall / IIS target) instead" }
        break
    }
}

# 8b. Chosen listen port free
$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    $owner = (Get-Process -Id $listening[0].OwningProcess -ErrorAction SilentlyContinue).ProcessName
    Report "FAIL" "Port $Port free" "in use by $owner (pid $($listening[0].OwningProcess))"
} else { Report "PASS" "Port $Port free" "nothing listening" }

# 9. Existing service
$svc = Get-Service -Name FbrMiddleware -ErrorAction SilentlyContinue
Report "PASS" "Existing FbrMiddleware service" $(if ($svc) { "present ($($svc.Status)); install-service.ps1 will replace it" } else { "none (fresh install)" })

# 10. Root folder and NSSM
Report ($(if (Test-Path $Root) { "PASS" } else { "WARN" })) "Root folder $Root" $(if (Test-Path $Root) { "exists" } else { "will be created by the install steps" })
$nssmCmd = Get-Command nssm -ErrorAction SilentlyContinue
$nssm = if ($nssmCmd) { $nssmCmd.Source } elseif (Test-Path (Join-Path $NssmDir "nssm.exe")) { Join-Path $NssmDir "nssm.exe" } else { $null }
Report ($(if ($nssm) { "PASS" } else { "WARN" })) "NSSM present" $(if ($nssm) { "$nssm (shared with other services)" } else { "not at $NssmDir or on PATH; install-service.ps1 downloads 2.24" })
$appPresent = (Test-Path (Join-Path $Root "pyproject.toml")) -and (Test-Path (Join-Path $Root "src\fbr_middleware\main.py"))
Report ($(if ($appPresent) { "PASS" } else { "WARN" })) "Repository at $Root" $(if ($appPresent) { "found" } else { "clone or copy the repository there before install" })
$envPath = Join-Path $Root ".env"
Report ($(if (Test-Path $envPath) { "PASS" } else { "WARN" })) ".env at $Root" $(if (Test-Path $envPath) { "present (install-service.ps1 reads LISTEN_PORT and MIDDLEWARE_API_KEYS from it)" } else { "copy .env.example to .env and set production values" })

# 11. Disk space on the root drive
$drive = (Split-Path -Qualifier $Root)
$vol = Get-PSDrive -Name $drive.TrimEnd(":") -ErrorAction SilentlyContinue
if ($vol) {
    $freeGB = [math]::Round($vol.Free / 1GB, 1)
    Report ($(if ($freeGB -ge $MinFreeGB) { "PASS" } else { "FAIL" })) "Free disk on $drive" "$freeGB GB (need >= $MinFreeGB GB)"
} else { Report "WARN" "Free disk on $drive" "drive not found" }

# 12. Clock sync (invoiceDate validation and PRAL 'dated' depend on it)
try {
    $w32 = (& w32tm /query /status 2>&1 | Out-String)
    $src = ($w32 -split "`r?`n" | Where-Object { $_ -match "^Source:" }) -join ""
    $ok = $w32 -match "Source:" -and $w32 -notmatch "Local CMOS Clock"
    Report ($(if ($ok) { "PASS" } else { "WARN" })) "Time synchronisation" $(if ($src) { $src.Trim() } else { "w32tm reports no source" })
} catch { Report "WARN" "Time synchronisation" "w32tm not available" }
$tz = (Get-TimeZone).Id
Report "PASS" "Server time zone" "$tz (local now $(Get-Date -Format 'yyyy-MM-dd HH:mm'))"

# 13. Coexistence: other Python installations that share PATH with this deployment
$pyOnPath = @(& where.exe python 2>$null) | Where-Object { $_ -and $_ -notlike "*WindowsApps*" }
$pyInstalls = @(Get-ChildItem "HKLM:\SOFTWARE\Python\PythonCore", "HKCU:\SOFTWARE\Python\PythonCore" -ErrorAction SilentlyContinue | ForEach-Object { $_.PSChildName })
if ($pyOnPath.Count -gt 1 -or ($pyInstalls | Where-Object { $_ -notlike "3.12*" })) {
    Report "WARN" "Other Python installs on this server" ("registry: " + ($pyInstalls -join ", ") + "; on PATH: " + ($pyOnPath -join ", ") + " -> install 3.12 WITHOUT 'Add to PATH' and build the venv from its absolute path so other apps keep their python")
} else {
    Report "PASS" "Other Python installs on this server" $(if ($pyInstalls) { "only " + ($pyInstalls -join ", ") } else { "none registered" })
}

# 14. Coexistence: IIS present and anything already bound to 443 (matters only for the IIS reverse-proxy option)
$iis = Get-Service -Name W3SVC -ErrorAction SilentlyContinue
if ($iis) {
    try {
        Import-Module WebAdministration -ErrorAction Stop
        $b443 = @(Get-WebBinding -Port 443 -ErrorAction SilentlyContinue | ForEach-Object { "$($_.ItemXPath -replace ".*name='([^']+)'.*", '$1') [$($_.bindingInformation)]" })
        if ($b443) {
            Report "WARN" "IIS bindings on 443" (($b443 -join "; ") + " -> use a distinct host name (SNI) for the middleware site or another port")
        } else { Report "PASS" "IIS bindings on 443" "IIS running ($($iis.Status)), nothing bound to 443 yet" }
    } catch { Report "WARN" "IIS bindings on 443" "IIS running but WebAdministration module unavailable; check bindings in IIS Manager" }
} else { Report "PASS" "IIS" "not installed (direct uvicorn TLS or install IIS later)" }

# 15. Coexistence: firewall rule name and other services using the same port range
$fw = Get-NetFirewallRule -DisplayName "FBR Middleware" -ErrorAction SilentlyContinue
Report "PASS" "Firewall rule 'FBR Middleware'" $(if ($fw) { "exists already; New-NetFirewallRule would add a duplicate, edit the existing one" } else { "none yet" })
$otherSvcs = @(Get-CimInstance Win32_Service | Where-Object { $_.PathName -match "nssm|python|uvicorn" -and $_.Name -ne "FbrMiddleware" } | ForEach-Object { "$($_.Name) ($($_.State))" })
Report "PASS" "Other Python/NSSM services present" $(if ($otherSvcs) { ($otherSvcs -join "; ") + " -> keep ports and folders separate" } else { "none" })

# 16. Long path / execution policy
$policy = Get-ExecutionPolicy -Scope CurrentUser
Report ($(if ($policy -in @("RemoteSigned", "Unrestricted", "Bypass")) { "PASS" } else { "WARN" })) "Execution policy (CurrentUser)" "$policy (RemoteSigned needed to dot-source Activate.ps1; python.exe calls work regardless)"

Write-Host ""
if ($script:failed -gt 0) {
    Write-Host "$($script:failed) check(s) FAILED. Fix them before running the install steps." -ForegroundColor Red
    exit 1
}
Write-Host "All required checks passed." -ForegroundColor Green
exit 0
