# Daily year-first Carvana full-inventory live run for Task Scheduler.
# Requires: PC awake and logged on, VPN off, root .venv present.
# Preview still gates window fit, destination freshness, and peer stop markers.
param(
    [switch]$PreviewOnly,
    [switch]$SkipVpnCheck,
    [string]$Python,
    [string]$Config
)
$ErrorActionPreference = 'Stop'
$vehicleRoot = Split-Path -Parent $PSScriptRoot
$repositoryRoot = Split-Path -Parent $vehicleRoot
if (-not $Python) { $Python = Join-Path $repositoryRoot '.venv\Scripts\python.exe' }
if (-not $Config) { $Config = Join-Path $vehicleRoot 'config\carvana_full_inventory_adaptive.json' }
if (-not (Test-Path -LiteralPath $Python)) { throw "Missing Python environment: $Python" }
if (-not (Test-Path -LiteralPath $Config)) { throw "Missing config: $Config" }

$logRoot = Join-Path $vehicleRoot 'data\experiments\carvana_full_inventory_daily_logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$stamp = Get-Date -Format 'yyyy-MM-dd_HHmmss'
$logPath = Join-Path $logRoot "daily_$stamp.log"

function Write-Log([string]$Message) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK'), $Message
    Add-Content -LiteralPath $logPath -Value $line
    Write-Host $line
}

function Get-SuspectedVpnAdapters {
    Get-NetAdapter -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Status -eq 'Up' -and (
                $_.InterfaceDescription -match 'VPN|TAP-Windows|Wintun|WireGuard|OpenVPN|NordLynx|PANGP|Cisco AnyConnect|Tunnel|ZeroTier|Tailscale' -or
                $_.Name -match 'VPN|WireGuard|NordLynx|Tailscale|ZeroTier|OpenVPN'
            )
        }
}

Set-Location -LiteralPath $repositoryRoot
Write-Log "START log=$logPath"
Write-Log "Python=$Python"
Write-Log "Config=$Config"

if (-not $SkipVpnCheck) {
    $vpn = @(Get-SuspectedVpnAdapters)
    if ($vpn.Count -gt 0) {
        $names = ($vpn | ForEach-Object { $_.Name }) -join ', '
        Write-Log "BLOCKED: Suspected VPN adapter(s) up: $names. Disconnect VPN and rerun, or pass -SkipVpnCheck."
        exit 2
    }
    Write-Log 'VPN check: no suspected VPN adapter up'
}

$runner = Join-Path $vehicleRoot 'scripts\run_carvana_full_inventory.py'
$decisionRaw = & $Python -B $runner --decide --config $Config 2>&1 | ForEach-Object { "$_" }
if ($LASTEXITCODE -ne 0) {
    Write-Log "BLOCKED: decision failed: $decisionRaw"
    exit 1
}
$decisionLine = @($decisionRaw | ForEach-Object { $_.ToString() } | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1)
if (-not $decisionLine) {
    Write-Log "BLOCKED: decision returned no JSON: $decisionRaw"
    exit 1
}
Write-Log "DECISION $decisionLine"
$decision = $decisionLine | ConvertFrom-Json
if ($decision.action -ne 'start') {
    Write-Log "SKIP: action=$($decision.action) reason=$($decision.reason)"
    exit 0
}
if ($PreviewOnly) {
    Write-Log "PreviewOnly: would collect attempt $($decision.attempt) at $($decision.spacing_seconds)s into $($decision.destination)"
    exit 0
}

Write-Log "LIVE start cycle_date=$($decision.cycle_date) attempt=$($decision.attempt) spacing=$($decision.spacing_seconds) sha=$($decision.config_sha256)"
& $Python -B $runner --live --config $Config --config-sha256 $decision.config_sha256 `
    --attempt $decision.attempt --spacing-seconds $decision.spacing_seconds 2>&1 |
    ForEach-Object { "$_" } | Add-Content -LiteralPath $logPath -Encoding utf8
$exitCode = $LASTEXITCODE
if ($exitCode -eq 2) {
    Write-Log "LIVE finished incomplete (exit_code=2). Collection ended without a reconciled full catalog."
} else {
    Write-Log "LIVE finished exit_code=$exitCode"
}
exit $exitCode
