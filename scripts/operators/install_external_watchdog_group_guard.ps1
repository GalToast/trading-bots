param(
    [Parameter(Mandatory = $true)]
    [string]$GroupName
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\task_launcher_helpers.ps1"

$RootDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$ConfigPath = Join-Path $RootDir "configs\watchdog_groups.json"
$SupervisorDir = Join-Path $env:LOCALAPPDATA "TradingBotsSupervisor"
$PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
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
$ExternalScriptPath = Join-Path $SupervisorDir ("watch_" + $GroupName + ".ps1")
$LauncherPath = Join-Path $SupervisorDir ("run_{0}_watchdog_guard_hidden.vbs" -f $GroupName)
$TaskName = "TradingBots-${label}Watchdog-Guard"
$TaskDescription = "External one-shot guard outside the repo that restarts the MT5 ${label} watchdog loop if its heartbeat or process goes stale."
$repoRootLiteral = $RootDir.Replace("'", "''")
$groupNameLiteral = $GroupName.Replace("'", "''")
$labelLiteral = $label.Replace("'", "''")

$externalScript = @"
param(
    [switch]`$EmitTestAlert,
    [string]`$TestReason = 'manual_self_test'
)

`$ErrorActionPreference = 'Stop'

`$RepoRoot = '$repoRootLiteral'
`$GroupName = '$groupNameLiteral'
`$GroupLabel = '$labelLiteral'
`$LoopStatePath = Join-Path `$RepoRoot ('reports\watchdog\' + `$GroupName + '_loop_state.json')
`$EnsureScriptPath = Join-Path `$RepoRoot 'scripts\operators\ensure_watchdog_group.ps1'
`$RepoAlertPath = Join-Path `$RepoRoot ('reports\watchdog\' + `$GroupName + '_alerts.jsonl')
`$PostSwitchboardScript = Join-Path `$RepoRoot 'scripts\post_to_switchboard.py'
`$PowerShellExe = Join-Path `$env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
`$PythonExe = Join-Path `$env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
if (-not (Test-Path `$PythonExe)) {
    `$PythonExe = 'python'
}
`$LogDir = Join-Path `$env:LOCALAPPDATA 'TradingBotsSupervisor'
`$LogPath = Join-Path `$LogDir (`$GroupName + '_guard.log')
`$GuardStatePath = Join-Path `$LogDir (`$GroupName + '_guard_state.json')
`$LocalAlertPath = Join-Path `$LogDir (`$GroupName + '_alerts.jsonl')
`$StaleAfterSeconds = 150
`$RecoveryWaitSeconds = 20

function Write-GuardLog([string]`$Message) {
    New-Item -ItemType Directory -Force -Path `$LogDir | Out-Null
    Add-Content -Path `$LogPath -Value ('[{0}] {1}' -f ([DateTime]::UtcNow.ToString('o')), `$Message)
}

function Get-LoopProcesses() {
    function Get-LoopNameFromCommandLine([string]`$CommandLine) {
        if (-not `$CommandLine) {
            return ""
        }

        `$pattern = '(?i)(^|\s)--loop-name(?:\s+|=)(?:"([^"]*)"|'`'([^'`']*)'`'|([^\s]+))'
        `$match = [regex]::Match(`$CommandLine, `$pattern)
        if (-not `$match.Success) {
            return ""
        }

        if (`$match.Groups[2].Value) {
            return `$match.Groups[2].Value
        }
        if (`$match.Groups[3].Value) {
            return `$match.Groups[3].Value
        }
        return `$match.Groups[4].Value
    }

    Get-CimInstance Win32_Process |
        Where-Object {
            `$_.Name -eq 'python.exe' -and
            `$_.CommandLine -match [regex]::Escape('watch_penetration_lattice_runners.py') -and
            (Get-LoopNameFromCommandLine -CommandLine `$_.CommandLine) -ieq `$GroupName
        } |
        Select-Object ProcessId, CommandLine
}

function Read-LoopState() {
    if (-not (Test-Path `$LoopStatePath)) {
        return `$null
    }
    try {
        return Get-Content `$LoopStatePath -Raw | ConvertFrom-Json
    }
    catch {
        Write-GuardLog ('failed_to_parse_loop_state: ' + `$_.Exception.Message)
        return `$null
    }
}

function Read-GuardState() {
    if (-not (Test-Path `$GuardStatePath)) {
        return `$null
    }
    try {
        return Get-Content `$GuardStatePath -Raw | ConvertFrom-Json
    }
    catch {
        Write-GuardLog ('failed_to_parse_guard_state: ' + `$_.Exception.Message)
        return `$null
    }
}

function Write-GuardState([hashtable]`$State) {
    `$payload = (`$State | ConvertTo-Json -Depth 6)
    Set-Content -Path `$GuardStatePath -Value `$payload -Encoding UTF8
}

function Build-GuardState([string]`$Status, [string]`$Reason, [int]`$ProcessCount, [double]`$AgeSeconds, [object]`$LoopState, [hashtable]`$Extras) {
    `$state = @{
        status = `$Status
        reason = `$Reason
        process_count = `$ProcessCount
        loop_state_age_seconds = [math]::Round(`$AgeSeconds, 1)
        updated_at = [DateTime]::UtcNow.ToString('o')
        repo_root = `$RepoRoot
        loop_state_path = `$LoopStatePath
        loop_status = if (`$LoopState) { [string]`$LoopState.status } else { '' }
        loop_updated_at = if (`$LoopState) { [string]`$LoopState.updated_at } else { '' }
        group_name = `$GroupName
    }
    foreach (`$key in (`$Extras.Keys | Where-Object { `$null -ne `$_ })) {
        `$state[`$key] = `$Extras[`$key]
    }
    return `$state
}

function Get-LoopHealth() {
    `$processes = @(Get-LoopProcesses)
    `$processCount = `$processes.Count
    `$loopState = Read-LoopState
    `$ageSeconds = [double]::PositiveInfinity
    `$status = ''

    if (`$loopState -and `$loopState.updated_at) {
        try {
            `$updatedAt = [DateTimeOffset]::Parse([string]`$loopState.updated_at)
            `$ageSeconds = [Math]::Max(0.0, ([DateTimeOffset]::UtcNow - `$updatedAt).TotalSeconds)
            `$status = [string]`$loopState.status
        }
        catch {
            Write-GuardLog ('failed_to_parse_loop_state_updated_at: ' + `$_.Exception.Message)
            `$ageSeconds = [double]::PositiveInfinity
            `$status = 'error'
        }
    }

    return @{
        process_count = `$processCount
        loop_state = `$loopState
        age_seconds = `$ageSeconds
        status = `$status
    }
}

function Append-JsonLine([string]`$Path, [hashtable]`$Payload) {
    New-Item -ItemType Directory -Force -Path (Split-Path `$Path -Parent) | Out-Null
    Add-Content -Path `$Path -Value ((`$Payload | ConvertTo-Json -Compress))
}

`$DesktopAlertsEnabled = [string]`$env:TRADING_BOTS_ENABLE_DESKTOP_ALERTS -eq '1'

function Show-DesktopAlert([string]`$Title, [string]`$Message) {
    try {
        `$escapedTitle = `$Title.Replace("'", "''")
        `$escapedMessage = `$Message.Replace("'", "''")
        `$popupCommand = "Add-Type -AssemblyName System.Windows.Forms; [void][System.Windows.Forms.MessageBox]::Show('`$escapedMessage','`$escapedTitle')"
        Start-Process -FilePath `$PowerShellExe -WindowStyle Hidden -ArgumentList @(
            '-NoProfile',
            '-WindowStyle', 'Hidden',
            '-Command', `$popupCommand
        ) | Out-Null
    }
    catch {
        Write-GuardLog ('desktop_alert_failed: ' + `$_.Exception.Message)
    }
}

function Post-SwitchboardAlert([string]`$Message) {
    if (-not (Test-Path `$PostSwitchboardScript)) {
        return
    }
    try {
        & `$PythonExe `$PostSwitchboardScript ('@' + `$GroupName + '-guard') `$Message | Out-Null
    }
    catch {
        Write-GuardLog ('switchboard_alert_failed: ' + `$_.Exception.Message)
    }
}

function Emit-Alert([string]`$Severity, [string]`$EventType, [string]`$Reason, [int]`$ProcessCount, [double]`$AgeSeconds, [object]`$LoopState) {
    `$nowText = [DateTime]::UtcNow.ToString('o')
    `$title = ('TradingBots ' + `$GroupLabel + ' watchdog ' + `$EventType)
    `$message = ('group=' + `$GroupName + '; severity=' + `$Severity + '; reason=' + `$Reason + '; process_count=' + `$ProcessCount + '; loop_age_s=' + ([math]::Round(`$AgeSeconds, 1)))
    `$payload = @{
        ts_utc = `$nowText
        severity = `$Severity
        event_type = `$EventType
        reason = `$Reason
        process_count = `$ProcessCount
        loop_state_age_seconds = [math]::Round(`$AgeSeconds, 1)
        repo_root = `$RepoRoot
        loop_state_path = `$LoopStatePath
        loop_status = if (`$LoopState) { [string]`$LoopState.status } else { '' }
        loop_updated_at = if (`$LoopState) { [string]`$LoopState.updated_at } else { '' }
        hostname = `$env:COMPUTERNAME
        group_name = `$GroupName
    }
    Append-JsonLine -Path `$LocalAlertPath -Payload `$payload
    Append-JsonLine -Path `$RepoAlertPath -Payload `$payload
    Post-SwitchboardAlert ('[' + `$GroupName + '-guard] ' + `$EventType + ' | ' + `$message)
    if (`$DesktopAlertsEnabled) {
        Show-DesktopAlert -Title `$title -Message `$message
    }
    Write-GuardLog ('alert_emitted ' + `$EventType + ' ' + `$message)
}

if (-not (Test-Path `$EnsureScriptPath)) {
    Write-GuardLog ('ensure_script_missing: ' + `$EnsureScriptPath)
    `$failureState = Build-GuardState -Status 'error' -Reason 'ensure_script_missing' -ProcessCount 0 -AgeSeconds -1 -LoopState `$null -Extras @{}
    Write-GuardState -State `$failureState
    exit 1
}

`$previousGuardState = Read-GuardState
if (`$EmitTestAlert) {
    `$loopState = Read-LoopState
    `$ageSeconds = [double]::PositiveInfinity
    `$processCount = @(Get-LoopProcesses).Count
    if (`$loopState -and `$loopState.updated_at) {
        try {
            `$updatedAt = [DateTimeOffset]::Parse([string]`$loopState.updated_at)
            `$ageSeconds = [Math]::Max(0.0, ([DateTimeOffset]::UtcNow - `$updatedAt).TotalSeconds)
        }
        catch {
            `$ageSeconds = [double]::PositiveInfinity
        }
    }
    Emit-Alert -Severity 'info' -EventType 'self_test' -Reason `$TestReason -ProcessCount `$processCount -AgeSeconds `$ageSeconds -LoopState `$loopState
    `$selfTestState = Build-GuardState -Status 'healthy' -Reason 'self_test_emitted' -ProcessCount `$processCount -AgeSeconds `$ageSeconds -LoopState `$loopState -Extras @{
        last_alert_key = if (`$previousGuardState) { [string]`$previousGuardState.last_alert_key } else { '' }
        last_self_test_at = [DateTime]::UtcNow.ToString('o')
        last_self_test_reason = `$TestReason
    }
    Write-GuardState -State `$selfTestState
    exit 0
}

`$health = Get-LoopHealth
`$processCount = [int]`$health.process_count
`$loopState = `$health.loop_state
`$ageSeconds = [double]`$health.age_seconds
`$status = [string]`$health.status
`$reason = ''
if (`$loopState -and `$loopState.updated_at -and `$status -eq 'error') {
    `$reason = 'loop_state_parse_failed'
}

`$needsEnsure = `$false
if (`$processCount -lt 1) {
    `$needsEnsure = `$true
    `$reason = 'loop_process_missing'
}
elseif (-not `$loopState) {
    `$needsEnsure = `$true
    `$reason = 'loop_state_missing'
}
elseif (`$status -ne 'ok') {
    `$needsEnsure = `$true
    `$reason = 'loop_state_not_ok:' + `$status
}
elseif (`$ageSeconds -gt `$StaleAfterSeconds) {
    `$needsEnsure = `$true
    `$reason = ('loop_state_stale:{0:n1}s' -f `$ageSeconds)
}

if (`$needsEnsure) {
    `$currentFailureKey = ('failure|' + `$reason)
    if (-not `$previousGuardState -or [string]`$previousGuardState.last_alert_key -ne `$currentFailureKey) {
        Emit-Alert -Severity 'critical' -EventType 'failure_detected' -Reason `$reason -ProcessCount `$processCount -AgeSeconds `$ageSeconds -LoopState `$loopState
    }
    Write-GuardLog ('invoking_ensure reason=' + `$reason)
    `$ensureProcess = Start-Process -FilePath `$PowerShellExe -ArgumentList @(
        '-NoProfile',
        '-WindowStyle', 'Hidden',
        '-ExecutionPolicy', 'Bypass',
        '-File', `$EnsureScriptPath,
        '-GroupName', `$GroupName
    ) -WindowStyle Hidden -PassThru -Wait
    if (`$ensureProcess.ExitCode -ne 0) {
        Write-GuardLog ('ensure_script_exit_code=' + `$ensureProcess.ExitCode + '; reason=' + `$reason)
    }
    `$deadline = [DateTimeOffset]::UtcNow.AddSeconds(`$RecoveryWaitSeconds)
    do {
        `$health = Get-LoopHealth
        `$processCount = [int]`$health.process_count
        `$loopState = `$health.loop_state
        `$ageSeconds = [double]`$health.age_seconds
        `$status = [string]`$health.status
        if (`$processCount -ge 1 -and `$loopState -and `$status -eq 'ok' -and `$ageSeconds -le `$StaleAfterSeconds) {
            break
        }
        Start-Sleep -Seconds 1
    } while ([DateTimeOffset]::UtcNow -lt `$deadline)
    if (`$processCount -ge 1 -and `$loopState -and `$status -eq 'ok' -and `$ageSeconds -le `$StaleAfterSeconds) {
        `$recoveredState = Build-GuardState -Status 'recovered' -Reason `$reason -ProcessCount `$processCount -AgeSeconds `$ageSeconds -LoopState `$loopState -Extras @{
            last_alert_key = ('recovered|' + `$reason)
            last_recovery_at = [DateTime]::UtcNow.ToString('o')
        }
        Write-GuardState -State `$recoveredState
        Write-GuardLog ('recovered reason=' + `$reason + '; process_count=' + `$processCount + '; loop_age_s=' + ([math]::Round(`$ageSeconds, 1)))
        if (-not `$previousGuardState -or [string]`$previousGuardState.last_alert_key -ne `$recoveredState.last_alert_key) {
            Emit-Alert -Severity 'warning' -EventType 'recovered' -Reason `$reason -ProcessCount `$processCount -AgeSeconds `$ageSeconds -LoopState `$loopState
        }
        exit 0
    }
    `$failedState = Build-GuardState -Status 'error' -Reason ('ensure_failed:' + `$reason) -ProcessCount `$processCount -AgeSeconds `$ageSeconds -LoopState `$loopState -Extras @{
        last_alert_key = ('recovery_failed|' + `$reason)
    }
    Write-GuardState -State `$failedState
    if (-not `$previousGuardState -or [string]`$previousGuardState.last_alert_key -ne `$failedState.last_alert_key) {
        Emit-Alert -Severity 'critical' -EventType 'recovery_failed' -Reason `$reason -ProcessCount `$processCount -AgeSeconds `$ageSeconds -LoopState `$loopState
    }
    exit 1
}

`$healthyState = Build-GuardState -Status 'healthy' -Reason 'no_action_needed' -ProcessCount `$processCount -AgeSeconds `$ageSeconds -LoopState `$loopState -Extras @{
    last_alert_key = if (`$previousGuardState) { [string]`$previousGuardState.last_alert_key } else { '' }
}
Write-GuardState -State `$healthyState
exit 0
"@

New-Item -ItemType Directory -Force -Path $SupervisorDir | Out-Null
Set-Content -Path $ExternalScriptPath -Value $externalScript -Encoding UTF8

$action = New-HiddenPowerShellTaskAction `
    -LauncherPath $LauncherPath `
    -PowerShellExe $PowerShellExe `
    -ScriptPath $ExternalScriptPath

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

Write-Output "Installed external guard task: $TaskName"
Write-Output "External script: $ExternalScriptPath"
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State, Author, Description | Format-List
