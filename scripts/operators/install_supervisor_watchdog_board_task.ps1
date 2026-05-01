param()

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\task_launcher_helpers.ps1"

$RootDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$RefreshScriptPath = Join-Path $RootDir "scripts\operators\refresh_supervisor_watchdog_board.ps1"
$PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$SupervisorDir = Join-Path $env:LOCALAPPDATA "TradingBotsSupervisor"
$LauncherPath = Join-Path $SupervisorDir "run_supervisor_watchdog_board_refresh_hidden.vbs"
$UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$TaskName = "TradingBots-SupervisorWatchdogBoard-Refresh"
$TaskDescription = "Refreshes the consolidated crypto/FX/shadow supervisor watchdog board at logon and every minute."

if (-not (Test-Path $RefreshScriptPath)) {
    throw "Missing refresh script: $RefreshScriptPath"
}

$action = New-HiddenPowerShellTaskAction `
    -LauncherPath $LauncherPath `
    -PowerShellExe $PowerShellExe `
    -ScriptPath $RefreshScriptPath

$startAt = (Get-Date).AddMinutes(1)
$repeatTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At $startAt `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $UserId

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -DisallowHardTerminate:$false `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
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
