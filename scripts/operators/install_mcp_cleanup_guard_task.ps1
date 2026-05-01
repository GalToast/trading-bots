param(
    [double]$MinAgeSeconds = 120
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\task_launcher_helpers.ps1"

$RootDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$EnsureScriptPath = Join-Path $RootDir "scripts\operators\ensure_mcp_cleanup_guard.ps1"
$PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$SupervisorDir = Join-Path $env:LOCALAPPDATA "TradingBotsSupervisor"
$LauncherPath = Join-Path $SupervisorDir "run_mcp_cleanup_guard_hidden.vbs"
$TaskName = "TradingBots-McpCleanup-Guard"
$TaskDescription = "Reaps stale or duplicate chrome-devtools-mcp and @playwright/mcp node helper trees at logon and every 5 minutes."
$UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not (Test-Path $EnsureScriptPath)) {
    throw "Missing ensure script: $EnsureScriptPath"
}

$action = New-HiddenPowerShellTaskAction `
    -LauncherPath $LauncherPath `
    -PowerShellExe $PowerShellExe `
    -ScriptPath $EnsureScriptPath `
    -ScriptArguments @('-MinAgeSeconds', [string]$MinAgeSeconds)

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
