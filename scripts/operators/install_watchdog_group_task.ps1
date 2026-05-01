param(
    [Parameter(Mandatory = $true)]
    [string]$GroupName
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\task_launcher_helpers.ps1"

$RootDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$ConfigPath = Join-Path $RootDir "configs\watchdog_groups.json"
$EnsureScriptPath = Join-Path $RootDir "scripts\operators\ensure_watchdog_group.ps1"
$PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$SupervisorDir = Join-Path $env:LOCALAPPDATA "TradingBotsSupervisor"
$UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

function Get-GroupConfig([string]$Name) {
    if (-not (Test-Path $ConfigPath)) {
        throw "Missing watchdog config: $ConfigPath"
    }
    $raw = Get-Content $ConfigPath -Raw | ConvertFrom-Json
    $group = $raw.groups.$Name
    if (-not $group) {
        throw "Unknown watchdog group: $Name"
    }
    return $group
}

$group = Get-GroupConfig -Name $GroupName
$label = [string]$group.label
$LauncherPath = Join-Path $SupervisorDir ("run_{0}_watchdog_ensure_hidden.vbs" -f $GroupName)
$TaskName = "TradingBots-${label}Watchdog-Ensure"
$TaskDescription = "Keeps the MT5 ${label} watchdog loop alive by running ensure_watchdog_group.ps1 for ${GroupName} at logon and every 5 minutes."

if (-not (Test-Path $EnsureScriptPath)) {
    throw "Missing ensure script: $EnsureScriptPath"
}

$action = New-HiddenPowerShellTaskAction `
    -LauncherPath $LauncherPath `
    -PowerShellExe $PowerShellExe `
    -ScriptPath $EnsureScriptPath `
    -ScriptArguments @('-GroupName', $GroupName)

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
