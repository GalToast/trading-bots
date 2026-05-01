$ErrorActionPreference = "Stop"

$RootDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$ReportPath = Join-Path $RootDir "reports\execution_monitor_report.json"
$RepoAlertPath = Join-Path $RootDir "reports\watchdog\trade_firing_alerts.jsonl"
$RepoStatePath = Join-Path $RootDir "reports\watchdog\trade_firing_alert_state.json"
$PostSwitchboardScript = Join-Path $RootDir "scripts\post_to_switchboard.py"
$PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$PythonExe = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python"
}
$SupervisorDir = Join-Path $env:LOCALAPPDATA "TradingBotsSupervisor"
$LocalAlertPath = Join-Path $SupervisorDir "trade_firing_alerts.jsonl"
$StatePath = Join-Path $SupervisorDir "trade_firing_alert_state.json"
$DuplicateCooldownSeconds = 600
$DesktopAlertsEnabled = @("1", "true", "yes", "on") -contains ([string]$env:TRADING_BOTS_ENABLE_DESKTOP_ALERTS).Trim().ToLower()

function Append-JsonLine([string]$Path, [hashtable]$Payload) {
    New-Item -ItemType Directory -Force -Path (Split-Path $Path -Parent) | Out-Null
    Add-Content -Path $Path -Value (($Payload | ConvertTo-Json -Compress))
}

function Show-DesktopAlert([string]$Title, [string]$Message) {
    if (-not $DesktopAlertsEnabled) {
        return
    }
    try {
        $escapedTitle = $Title.Replace("'", "''")
        $escapedMessage = $Message.Replace("'", "''")
        $popupCommand = "Add-Type -AssemblyName System.Windows.Forms; [void][System.Windows.Forms.MessageBox]::Show('$escapedMessage','$escapedTitle')"
        Start-Process -FilePath $PowerShellExe -WindowStyle Hidden -ArgumentList @(
            '-NoProfile',
            '-WindowStyle', 'Hidden',
            '-Command', $popupCommand
        ) | Out-Null
    }
    catch {
    }
}

function Post-SwitchboardAlert([string]$Message) {
    if (-not (Test-Path $PostSwitchboardScript)) {
        return
    }
    try {
        & $PythonExe $PostSwitchboardScript '@trade-firing-guard' $Message | Out-Null
    }
    catch {
    }
}

function Read-State() {
    if (-not (Test-Path $StatePath)) {
        return @{
            active_keys = @()
            last_events = @{}
            last_clean_check_at = ''
        }
    }
    try {
        $raw = Get-Content $StatePath -Raw | ConvertFrom-Json
        $lastEvents = @{}
        if ($raw.last_events) {
            foreach ($prop in $raw.last_events.PSObject.Properties) {
                $lastEvents[[string]$prop.Name] = [string]$prop.Value
            }
        }
        return @{
            active_keys = @($raw.active_keys)
            last_events = $lastEvents
            last_clean_check_at = [string]$raw.last_clean_check_at
        }
    }
    catch {
        return @{
            active_keys = @()
            last_events = @{}
            last_clean_check_at = ''
        }
    }
}

function Write-State([string[]]$ActiveKeys, [hashtable]$LastEvents, [string]$LastCleanCheckAt, [pscustomobject[]]$ActiveAnomalies, [pscustomobject[]]$Cooldowns) {
    New-Item -ItemType Directory -Force -Path $SupervisorDir | Out-Null
    New-Item -ItemType Directory -Force -Path (Split-Path $RepoStatePath -Parent) | Out-Null
    $evaluatedAt = [DateTime]::UtcNow.ToString('o')
    $payload = @{
        updated_at = $evaluatedAt
        last_evaluated_at = $evaluatedAt
        evaluation_status = if ($ActiveKeys.Count -gt 0) { 'anomaly_active' } else { 'clean' }
        cooldown_window_seconds = $DuplicateCooldownSeconds
        active_keys = $ActiveKeys
        active_anomalies = @($ActiveAnomalies)
        active_anomaly_count = $ActiveKeys.Count
        last_clean_check_at = $LastCleanCheckAt
        cooldowns = @($Cooldowns)
        last_events = $LastEvents
    }
    $json = $payload | ConvertTo-Json -Depth 6
    Set-Content -Path $StatePath -Value $json -Encoding UTF8
    Set-Content -Path $RepoStatePath -Value $json -Encoding UTF8
}

function Should-EmitEvent([hashtable]$LastEvents, [string]$EventKey) {
    $previous = [string]$LastEvents[$EventKey]
    if ([string]::IsNullOrWhiteSpace($previous)) {
        return $true
    }
    try {
        $previousDt = [DateTimeOffset]::Parse($previous)
        return (([DateTimeOffset]::UtcNow - $previousDt).TotalSeconds -ge $DuplicateCooldownSeconds)
    }
    catch {
        return $true
    }
}

function Mark-Event([hashtable]$LastEvents, [string]$EventKey) {
    $LastEvents[$EventKey] = [DateTime]::UtcNow.ToString('o')
}

function Build-Cooldowns([hashtable]$LastEvents, [hashtable]$CurrentKeys) {
    $rows = @()
    foreach ($entry in $LastEvents.GetEnumerator()) {
        $eventKey = [string]$entry.Key
        $emittedAt = [string]$entry.Value
        if ([string]::IsNullOrWhiteSpace($eventKey) -or [string]::IsNullOrWhiteSpace($emittedAt)) {
            continue
        }
        try {
            $emittedDt = [DateTimeOffset]::Parse($emittedAt)
        }
        catch {
            continue
        }
        $nextAllowedDt = $emittedDt.AddSeconds($DuplicateCooldownSeconds)
        $remainingSeconds = [Math]::Ceiling(($nextAllowedDt - [DateTimeOffset]::UtcNow).TotalSeconds)
        $segments = $eventKey.Split('|', 3)
        $transition = if ($segments.Length -gt 0) { [string]$segments[0] } else { '' }
        $lane = if ($segments.Length -gt 1) { [string]$segments[1] } else { '' }
        $alertCode = if ($segments.Length -gt 2) { [string]$segments[2] } else { '' }
        if ($remainingSeconds -le 0) {
            continue
        }
        $rows += [pscustomobject]@{
            event_key = $eventKey
            transition = $transition
            lane = $lane
            alert_code = $alertCode
            active = $CurrentKeys.ContainsKey(($lane + "|" + $alertCode))
            last_emitted_at = $emittedDt.ToString('o')
            next_allowed_at = $nextAllowedDt.ToString('o')
            remaining_seconds = [int]$remainingSeconds
        }
    }
    return @(
        $rows |
            Sort-Object `
                @{ Expression = { if ($_.active) { 0 } else { 1 } } }, `
                @{ Expression = { -1 * [int]$_.remaining_seconds } }, `
                @{ Expression = { [string]$_.event_key } }
    )
}

if (-not (Test-Path $ReportPath)) {
    throw "Missing execution monitor report: $ReportPath"
}

$report = Get-Content $ReportPath -Raw | ConvertFrom-Json
$rows = @($report.rows)
$anomalies = @()
foreach ($row in $rows) {
    $executionAlert = [string]$row.execution_alert
    $parityAlert = [string]$row.parity_alert
    if ([string]::IsNullOrWhiteSpace($executionAlert) -and [string]::IsNullOrWhiteSpace($parityAlert)) {
        continue
    }
    $alertCode = if (-not [string]::IsNullOrWhiteSpace($executionAlert)) { $executionAlert } else { $parityAlert }
    $severity = if ($executionAlert -eq 'probable_missed_open') { 'critical' } else { 'warning' }
    $anomalies += [pscustomobject]@{
        key = ([string]$row.lane + "|" + $alertCode)
        lane = [string]$row.lane
        kind = [string]$row.kind
        execution_alert = $executionAlert
        raw_execution_alert = [string]$row.raw_execution_alert
        execution_evidence_quality = [string]$row.execution_evidence_quality
        parity_alert = $parityAlert
        severity = $severity
        trigger_now = [string]$row.trigger_now
        trigger_age_seconds = $row.trigger_age_seconds
        watchdog_status = [string]$row.watchdog_status
        notes = [string]$row.notes
    }
}

$previousState = Read-State
$previousKeys = @{}
foreach ($key in @($previousState.active_keys)) {
    $previousKeys[[string]$key] = $true
}
$lastEvents = @{}
foreach ($entry in $previousState.last_events.GetEnumerator()) {
    $lastEvents[[string]$entry.Key] = [string]$entry.Value
}
$lastCleanCheckAt = [string]$previousState.last_clean_check_at
$currentKeys = @{}
foreach ($anomaly in $anomalies) {
    $currentKeys[[string]$anomaly.key] = $true
}

foreach ($anomaly in $anomalies) {
    if ($previousKeys.ContainsKey($anomaly.key)) {
        continue
    }
    $eventKey = "detected|" + $anomaly.key
    if (-not (Should-EmitEvent -LastEvents $lastEvents -EventKey $eventKey)) {
        continue
    }
    $message = "lane=$($anomaly.lane); severity=$($anomaly.severity); execution=$($anomaly.execution_alert); raw_execution=$($anomaly.raw_execution_alert); evidence=$($anomaly.execution_evidence_quality); parity=$($anomaly.parity_alert); trigger=$($anomaly.trigger_now); age_s=$($anomaly.trigger_age_seconds); watchdog=$($anomaly.watchdog_status)"
    $payload = @{
        ts_utc = [DateTime]::UtcNow.ToString('o')
        event_type = 'trade_firing_anomaly_detected'
        severity = $anomaly.severity
        lane = $anomaly.lane
        kind = $anomaly.kind
        execution_alert = $anomaly.execution_alert
        raw_execution_alert = $anomaly.raw_execution_alert
        execution_evidence_quality = $anomaly.execution_evidence_quality
        parity_alert = $anomaly.parity_alert
        trigger_now = $anomaly.trigger_now
        trigger_age_seconds = $anomaly.trigger_age_seconds
        watchdog_status = $anomaly.watchdog_status
        notes = $anomaly.notes
        repo_root = $RootDir
    }
    Append-JsonLine -Path $LocalAlertPath -Payload $payload
    Append-JsonLine -Path $RepoAlertPath -Payload $payload
    Post-SwitchboardAlert ("[trade-firing] detected | " + $message)
    Show-DesktopAlert -Title "TradingBots trade firing anomaly" -Message $message
    Mark-Event -LastEvents $lastEvents -EventKey $eventKey
}

foreach ($previousKey in $previousKeys.Keys) {
    if ($currentKeys.ContainsKey($previousKey)) {
        continue
    }
    $eventKey = "recovered|" + $previousKey
    if (-not (Should-EmitEvent -LastEvents $lastEvents -EventKey $eventKey)) {
        continue
    }
    $parts = $previousKey.Split('|', 2)
    $lane = if ($parts.Length -gt 0) { $parts[0] } else { '' }
    $alertCode = if ($parts.Length -gt 1) { $parts[1] } else { '' }
    $payload = @{
        ts_utc = [DateTime]::UtcNow.ToString('o')
        event_type = 'trade_firing_anomaly_recovered'
        severity = 'info'
        lane = $lane
        recovered_alert = $alertCode
        repo_root = $RootDir
    }
    Append-JsonLine -Path $LocalAlertPath -Payload $payload
    Append-JsonLine -Path $RepoAlertPath -Payload $payload
    Post-SwitchboardAlert ("[trade-firing] recovered | lane=" + $lane + "; alert=" + $alertCode)
    Mark-Event -LastEvents $lastEvents -EventKey $eventKey
}

$activeAnomalies = @(
    $anomalies |
        Sort-Object lane, severity |
        ForEach-Object {
            $alertCode = if (-not [string]::IsNullOrWhiteSpace($_.execution_alert)) { $_.execution_alert } else { $_.parity_alert }
            [pscustomobject]@{
                key = [string]$_.key
                lane = [string]$_.lane
                kind = [string]$_.kind
                alert_code = [string]$alertCode
                severity = [string]$_.severity
                raw_execution_alert = [string]$_.raw_execution_alert
                execution_evidence_quality = [string]$_.execution_evidence_quality
                trigger_now = [string]$_.trigger_now
                trigger_age_seconds = $_.trigger_age_seconds
                watchdog_status = [string]$_.watchdog_status
                notes = [string]$_.notes
            }
        }
)
if ($activeAnomalies.Count -eq 0) {
    $lastCleanCheckAt = [DateTime]::UtcNow.ToString('o')
}
$cooldowns = Build-Cooldowns -LastEvents $lastEvents -CurrentKeys $currentKeys

Write-State `
    -ActiveKeys @($currentKeys.Keys | Sort-Object) `
    -LastEvents $lastEvents `
    -LastCleanCheckAt $lastCleanCheckAt `
    -ActiveAnomalies $activeAnomalies `
    -Cooldowns $cooldowns
