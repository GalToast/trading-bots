$ErrorActionPreference = "Stop"
. "$PSScriptRoot\task_launcher_helpers.ps1"

$RootDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$TaskName = "TradingBots-CryptoWatchdog-Ensure"
$TaskDescription = "Keeps the MT5 crypto watchdog loop alive by running ensure_crypto_watchdog.ps1 at logon and every 5 minutes."
$EnsureScriptPath = Join-Path $RootDir "scripts\operators\ensure_crypto_watchdog.ps1"
$PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$SupervisorDir = Join-Path $env:LOCALAPPDATA "TradingBotsSupervisor"
$LauncherPath = Join-Path $SupervisorDir "run_crypto_watchdog_ensure_hidden.vbs"
$UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not (Test-Path $EnsureScriptPath)) {
    throw "Missing ensure script: $EnsureScriptPath"
}

$action = New-HiddenPowerShellTaskAction `
    -LauncherPath $LauncherPath `
    -PowerShellExe $PowerShellExe `
    -ScriptPath $EnsureScriptPath

$startAt = (Get-Date).AddMinutes(1)
$repeatTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At $startAt `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $UserId

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -DisallowHardTerminate:$false `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable
$settings.Hidden = $true

$principal = New-ScheduledTaskPrincipal `
    -UserId $UserId `
    -LogonType Interactive `
    -RunLevel Limited

$task = New-ScheduledTask `
    -Action $action `
    -Principal $principal `
    -Trigger @($repeatTrigger, $logonTrigger) `
    -Settings $settings `
    -Description $TaskDescription

Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Output "Installed scheduled task: $TaskName"
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State, Author, Description | Format-List
