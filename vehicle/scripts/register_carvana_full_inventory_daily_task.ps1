# Register or remove the local daily full-inventory Task Scheduler job.
param(
    [ValidateSet('Register', 'Unregister', 'Status')]
    [string]$Action = 'Register',
    [string]$At = '12:01AM',
    [string]$TaskName = 'researchOS-CarvanaFullInventoryDaily'
)
$ErrorActionPreference = 'Stop'
$vehicleRoot = Split-Path -Parent $PSScriptRoot
$repositoryRoot = Split-Path -Parent $vehicleRoot
$runner = Join-Path $PSScriptRoot 'run_carvana_full_inventory_daily.ps1'
if (-not (Test-Path -LiteralPath $runner)) { throw "Missing daily runner: $runner" }

if ($Action -eq 'Unregister') {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-Host "Removed scheduled task: $TaskName"
    return
}

if ($Action -eq 'Status') {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    $task | Select-Object TaskName, State, @{n='At';e={ $_.Triggers | ForEach-Object { $_.StartBoundary } }}
    $info | Select-Object LastRunTime, LastTaskResult, NextRunTime
    return
}

$argument = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $runner
$actionObj = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argument -WorkingDirectory $repositoryRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$repetition = New-ScheduledTaskTrigger -Once -At $At `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Hours 20)
$trigger.Repetition = $repetition.Repetition
$logon = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 16)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$description = 'Daily Carvana year-first full inventory (VPN off, logged-on session). Logs under vehicle/data/experiments/carvana_full_inventory_daily_logs.'

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $actionObj `
    -Trigger @($trigger, $logon) `
    -Settings $settings `
    -Principal $principal `
    -Description $description `
    -Force | Out-Null

Write-Host "Registered $TaskName daily at $At, then hourly for 20 hours, and at logon."
Write-Host "Runner: $runner"
Write-Host "Working directory: $repositoryRoot"
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object NextRunTime, LastRunTime, LastTaskResult
