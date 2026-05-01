param(
    [int]$ExistingChildPid = 0
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}
$PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$WatchdogDir = Join-Path $RootDir "reports\watchdog"
$ChildStdoutPath = Join-Path $WatchdogDir "shared_price_feeder.out.log"
$ChildStderrPath = Join-Path $WatchdogDir "shared_price_feeder.err.log"
$HeartbeatPath = Join-Path $RootDir "reports\shared_price_feeder_heartbeat.json"
$LauncherStatePath = Join-Path $WatchdogDir "shared_price_feeder_launcher_state.json"
$LauncherEventsPath = Join-Path $WatchdogDir "shared_price_feeder_launcher_events.jsonl"
$ScriptPath = Join-Path $RootDir "scripts\shared_price_feeder.py"
$CommandNeedle = "scripts/shared_price_feeder.py".ToLowerInvariant()
$AutoRestartDelaySeconds = 2.0
$AutoRestartBackoffCapSeconds = 20.0
$AutoRestartMaxAttemptsPerWindow = 5
$AutoRestartWindowSeconds = 120.0
$ChildLogTailLines = 80

function Get-UtcIsoNow {
    [DateTime]::UtcNow.ToString("o")
}

function Write-JsonFile([string]$Path, [hashtable]$Payload) {
    New-Item -ItemType Directory -Force -Path (Split-Path $Path -Parent) | Out-Null
    Set-Content -Path $Path -Value ($Payload | ConvertTo-Json -Depth 8) -Encoding UTF8
}

function Merge-Hashtable([hashtable]$Base, [hashtable]$Extra) {
    foreach ($key in $Extra.Keys) {
        $Base[$key] = $Extra[$key]
    }
    return $Base
}

function Append-JsonLine([string]$Path, [hashtable]$Payload) {
    New-Item -ItemType Directory -Force -Path (Split-Path $Path -Parent) | Out-Null
    Add-Content -Path $Path -Value (($Payload | ConvertTo-Json -Compress))
}

function Read-FileTail([string]$Path, [int]$LineCount) {
    if (-not (Test-Path $Path)) {
        return ""
    }
    if ($LineCount -le 0) {
        $LineCount = 80
    }
    try {
        return ((Get-Content -Path $Path -Tail $LineCount -ErrorAction Stop) -join "`n")
    }
    catch {
        return ""
    }
}

function Resolve-LogPath {
    param([string]$Path)

    $directory = Split-Path $Path -Parent
    $base = [System.IO.Path]::GetFileNameWithoutExtension($Path)
    $ext = [System.IO.Path]::GetExtension($Path)

    for ($suffix = 0; $suffix -lt 50; $suffix++) {
        $candidate = if ($suffix -eq 0) { $Path } else { Join-Path $directory ("${base}.${suffix}${ext}") }
        if (-not (Test-Path $directory)) {
            New-Item -ItemType Directory -Force -Path $directory | Out-Null
        }

        if (Test-Path $candidate) {
            try {
                $stream = [System.IO.File]::Open($candidate, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::ReadWrite)
                $stream.Close()
            }
            catch {
                continue
            }
        }

        try {
            Set-Content -Path $candidate -Value "" -Encoding UTF8
        }
        catch {
        }

        return $candidate
    }

    return $Path
}

function Start-HiddenPython([string[]]$ArgumentList, [string]$StdoutPath, [string]$StderrPath) {
    $resolvedStdout = Resolve-LogPath -Path $StdoutPath
    $resolvedStderr = Resolve-LogPath -Path $StderrPath

    New-Item -ItemType Directory -Force -Path (Split-Path $StdoutPath -Parent) | Out-Null
    return Start-Process `
        -FilePath $Python `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $RootDir `
        -RedirectStandardOutput $resolvedStdout `
        -RedirectStandardError $resolvedStderr `
        -WindowStyle Hidden `
        -PassThru
}

function Start-HiddenWrapperProcess([string]$ArgumentString) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PowerShellExe
    $psi.Arguments = $ArgumentString
    $psi.WorkingDirectory = $RootDir
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    $proc.Start() | Out-Null
    return $proc
}

function Get-ExistingFeederProcess {
    param([int]$ExcludePid = 0)

    $candidates = Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq "python.exe" -and
            $_.CommandLine -and
            $_.CommandLine.ToLowerInvariant().Contains($CommandNeedle)
        }

    foreach ($candidate in $candidates) {
        if ($ExcludePid -gt 0 -and [int]$candidate.ProcessId -eq $ExcludePid) {
            continue
        }
        return $candidate
    }

    return $null
}

function Get-HeartbeatSnapshot {
    if (-not (Test-Path $HeartbeatPath)) {
        return @{
            heartbeat_present = $false
            heartbeat_at = ""
            heartbeat_age_seconds = -1.0
            feeder_pid = 0
            heartbeat_cycle = 0
            heartbeat_symbols_updated = 0
            heartbeat_symbols_total = 0
        }
    }

    try {
        $heartbeat = Get-Content $HeartbeatPath -Raw | ConvertFrom-Json
    }
    catch {
        return @{
            heartbeat_present = $false
            heartbeat_at = ""
            heartbeat_age_seconds = -1.0
            feeder_pid = 0
            heartbeat_cycle = 0
            heartbeat_symbols_updated = 0
            heartbeat_symbols_total = 0
            heartbeat_status = "parse_failed"
        }
    }

    $heartbeatAt = ""
    $ageSeconds = -1.0
    if ($heartbeat.heartbeat_at) {
        $heartbeatAt = [string]$heartbeat.heartbeat_at
        try {
            $ageSeconds = [math]::Max(0.0, ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($heartbeatAt)).TotalSeconds)
        }
        catch {
            $ageSeconds = -1.0
        }
    }

    return @{
        heartbeat_present = $true
        heartbeat_at = $heartbeatAt
        heartbeat_age_seconds = [math]::Round($ageSeconds, 1)
        feeder_pid = [int]$heartbeat.feeder_pid
        heartbeat_cycle = [int]$heartbeat.cycle
        heartbeat_symbols_updated = [int]$heartbeat.symbols_updated
        heartbeat_symbols_total = [int]$heartbeat.symbols_total
    }
}

function Get-RestartContext([string]$StatePath) {
    $now = Get-UtcIsoNow
    $fresh = @{
        auto_restart_window_start_utc = $now
        auto_restart_failures_in_window = 0
        auto_restart_attempt_delay_seconds = $AutoRestartDelaySeconds
    }

    if (-not (Test-Path $StatePath)) {
        return $fresh
    }

    try {
        $state = Get-Content -Path $StatePath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        return $fresh
    }

    if (-not $state.auto_restart_window_start_utc -or $null -eq $state.auto_restart_failures_in_window) {
        return $fresh
    }

    try {
        $windowStart = [DateTimeOffset]::Parse($state.auto_restart_window_start_utc)
        $ageSeconds = ([DateTimeOffset]::UtcNow - $windowStart).TotalSeconds
        if ($ageSeconds -gt $AutoRestartWindowSeconds) {
            return $fresh
        }
    }
    catch {
        return $fresh
    }

    $failures = 0
    try {
        $failures = [int]$state.auto_restart_failures_in_window
    }
    catch {
        $failures = 0
    }
    if ($failures -lt 0) {
        $failures = 0
    }

    $delaySeconds = $AutoRestartDelaySeconds
    try {
        if ($state.auto_restart_attempt_delay_seconds) {
            $delaySeconds = [double]$state.auto_restart_attempt_delay_seconds
        }
    }
    catch {
        $delaySeconds = $AutoRestartDelaySeconds
    }

    if ($delaySeconds -lt $AutoRestartDelaySeconds) {
        $delaySeconds = $AutoRestartDelaySeconds
    }
    if ($delaySeconds -gt $AutoRestartBackoffCapSeconds) {
        $delaySeconds = $AutoRestartBackoffCapSeconds
    }

    return @{
        auto_restart_window_start_utc = ([DateTimeOffset]::Parse($state.auto_restart_window_start_utc)).ToString("o")
        auto_restart_failures_in_window = $failures
        auto_restart_attempt_delay_seconds = $delaySeconds
    }
}

New-Item -ItemType Directory -Force -Path $WatchdogDir | Out-Null

$launcherStartedAt = Get-UtcIsoNow
$child = $null
$commandPreview = @()
$launchMode = "spawned"

if ($ExistingChildPid -gt 0) {
    try {
        $existingProcess = Get-CimInstance Win32_Process |
            Where-Object {
                $_.ProcessId -eq $ExistingChildPid -and
                $_.Name -eq "python.exe" -and
                $_.CommandLine -and
                $_.CommandLine.ToLowerInvariant().Contains($CommandNeedle)
            } |
            Select-Object -First 1

        if (-not $existingProcess) {
            throw "Target child pid $ExistingChildPid not found as shared_price_feeder python process."
        }

        $child = Get-Process -Id $ExistingChildPid -ErrorAction Stop
        $commandPreview = @("attach_existing_pid", [string]$ExistingChildPid)
        $launchMode = "attached"
    }
    catch {
        $failure = @{
            status = "attach_failed"
            wrapper_pid = $PID
            child_pid = $ExistingChildPid
            child_stdout_path = $ChildStdoutPath
            child_stderr_path = $ChildStderrPath
            launcher_started_at = $launcherStartedAt
            launcher_finished_at = Get-UtcIsoNow
            python = $Python
            script_path = $ScriptPath
            command = @("attach_existing_pid", [string]$ExistingChildPid)
            exit_code = $null
            reason = $_.Exception.Message
            launch_mode = "attached"
        }
        $failure = Merge-Hashtable -Base $failure -Extra (Get-HeartbeatSnapshot)
        Write-JsonFile -Path $LauncherStatePath -Payload $failure
        $failureEvent = Merge-Hashtable -Base @{
            ts_utc = Get-UtcIsoNow
            event_type = "attach_failed"
            wrapper_pid = $PID
            child_pid = $ExistingChildPid
            child_stdout_path = $ChildStdoutPath
            child_stderr_path = $ChildStderrPath
            reason = $_.Exception.Message
            python = $Python
            command = @("attach_existing_pid", [string]$ExistingChildPid)
            launch_mode = "attached"
        } -Extra (Get-HeartbeatSnapshot)
        Append-JsonLine -Path $LauncherEventsPath -Payload $failureEvent
        throw
    }
}
else {
    $existing = Get-ExistingFeederProcess
    if ($existing) {
        $child = Get-Process -Id ([int]$existing.ProcessId) -ErrorAction Stop
        $commandPreview = @("attach_existing_pid", [string]$existing.ProcessId)
        $launchMode = "attached"
    }
    else {
        $argList = @("scripts/shared_price_feeder.py")
        $commandPreview = @($Python) + $argList

        try {
            $child = Start-HiddenPython -ArgumentList $argList -StdoutPath $ChildStdoutPath -StderrPath $ChildStderrPath
        }
        catch {
            $failure = @{
                status = "launch_failed"
                wrapper_pid = $PID
                child_pid = 0
                child_stdout_path = $ChildStdoutPath
                child_stderr_path = $ChildStderrPath
                launcher_started_at = $launcherStartedAt
                launcher_finished_at = Get-UtcIsoNow
                python = $Python
                script_path = $ScriptPath
                command = $commandPreview
                exit_code = $null
                reason = $_.Exception.Message
                launch_mode = "spawned"
            }
            $failure = Merge-Hashtable -Base $failure -Extra (Get-HeartbeatSnapshot)
            Write-JsonFile -Path $LauncherStatePath -Payload $failure
            $failureEvent = Merge-Hashtable -Base @{
                ts_utc = Get-UtcIsoNow
                event_type = "launch_failed"
                wrapper_pid = $PID
                child_pid = 0
                child_stdout_path = $ChildStdoutPath
                child_stderr_path = $ChildStderrPath
                reason = $_.Exception.Message
                python = $Python
                command = $commandPreview
                launch_mode = "spawned"
            } -Extra (Get-HeartbeatSnapshot)
            Append-JsonLine -Path $LauncherEventsPath -Payload $failureEvent
            throw
        }
    }
}

$startedState = @{
    status = "running"
    wrapper_pid = $PID
    child_pid = $child.Id
    child_stdout_path = $ChildStdoutPath
    child_stderr_path = $ChildStderrPath
    launcher_started_at = $launcherStartedAt
    launcher_finished_at = ""
    python = $Python
    script_path = $ScriptPath
    command = $commandPreview
    exit_code = $null
    reason = ""
    launch_mode = $launchMode
}
$restartState = Get-RestartContext -StatePath $LauncherStatePath
$startedState = Merge-Hashtable -Base $startedState -Extra (Get-HeartbeatSnapshot)
Write-JsonFile -Path $LauncherStatePath -Payload $startedState
$startEvent = Merge-Hashtable -Base @{
    ts_utc = Get-UtcIsoNow
    event_type = if ($launchMode -eq "attached") { "attached_existing_child" } else { "child_started" }
    wrapper_pid = $PID
    child_pid = $child.Id
    child_stdout_path = $ChildStdoutPath
    child_stderr_path = $ChildStderrPath
    python = $Python
    command = $commandPreview
    launch_mode = $launchMode
} -Extra (Get-HeartbeatSnapshot)
Append-JsonLine -Path $LauncherEventsPath -Payload $startEvent

$null = $child.WaitForExit()
$child.Refresh()
$exitCode = $child.ExitCode
$finishedAt = Get-UtcIsoNow
$runtimeSeconds = [math]::Round(([DateTimeOffset]::Parse($finishedAt) - [DateTimeOffset]::Parse($launcherStartedAt)).TotalSeconds, 1)
$snapshot = Get-HeartbeatSnapshot
$unexpectedExit = ($null -eq $exitCode -or $exitCode -ne 0)
$autoRestartRequested = $false
$autoRestartWrapperPid = 0
$autoRestartError = ""
$autoRestartReason = ""
$autoRestartLimitReached = $false
$nextRestartWindowStartUtc = (Get-UtcIsoNow)
$nextRestartFailuresInWindow = [int]$restartState.auto_restart_failures_in_window
$nextRestartDelaySeconds = $AutoRestartDelaySeconds
$childOutTail = Read-FileTail -Path $ChildStdoutPath -LineCount $ChildLogTailLines
$childErrTail = Read-FileTail -Path $ChildStderrPath -LineCount $ChildLogTailLines

if ($unexpectedExit) {
    $nextRestartFailuresInWindow = $nextRestartFailuresInWindow + 1
    $nextRestartWindowStartUtc = $restartState.auto_restart_window_start_utc
    if (-not $snapshot.heartbeat_present -or [double]$snapshot.heartbeat_age_seconds -gt 5.0) {
        $autoRestartReason = "startup_failure_like_state"
    }
    else {
        $autoRestartReason = "unexpected_exit"
    }

    if ($nextRestartFailuresInWindow -gt $AutoRestartMaxAttemptsPerWindow) {
        $autoRestartLimitReached = $true
        $autoRestartReason = "restart_limit_reached"
    }
    else {
        $nextRestartDelaySeconds = [math]::Min([math]::Max($AutoRestartDelaySeconds, $restartState.auto_restart_attempt_delay_seconds * 2), $AutoRestartBackoffCapSeconds)
        $recoveredProcess = Get-ExistingFeederProcess -ExcludePid $child.Id
        if ($recoveredProcess) {
            $autoRestartReason = "process_already_recovered"
            $nextRestartFailuresInWindow = 0
            $nextRestartDelaySeconds = $AutoRestartDelaySeconds
            $nextRestartWindowStartUtc = (Get-UtcIsoNow)
        }
        else {
            try {
                Start-Sleep -Seconds $nextRestartDelaySeconds
                $recoveredProcess = Get-ExistingFeederProcess -ExcludePid $child.Id
                if ($recoveredProcess) {
                    $autoRestartReason = "process_already_recovered"
                    $nextRestartFailuresInWindow = 0
                    $nextRestartDelaySeconds = $AutoRestartDelaySeconds
                    $nextRestartWindowStartUtc = (Get-UtcIsoNow)
                }
                else {
                    $restartArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
                    $restartWrapper = Start-HiddenWrapperProcess -ArgumentString $restartArgs
                    $autoRestartRequested = $true
                    $autoRestartWrapperPid = $restartWrapper.Id
                    $nextRestartWindowStartUtc = $restartState.auto_restart_window_start_utc
                }
            }
            catch {
                $autoRestartError = $_.Exception.Message
                $nextRestartDelaySeconds = $AutoRestartDelaySeconds
            }
        }
    }
}
elseif ($exitCode -eq 0) {
    $nextRestartFailuresInWindow = 0
    $nextRestartDelaySeconds = $AutoRestartDelaySeconds
    $nextRestartWindowStartUtc = (Get-UtcIsoNow)
}

if ($nextRestartFailuresInWindow -le 0) {
    $nextRestartWindowStartUtc = (Get-UtcIsoNow)
}

$exitState = @{
    status = if ($exitCode -eq 0) { "child_exited_clean" } else { "child_exited_unexpected" }
    wrapper_pid = $PID
    child_pid = $child.Id
    child_stdout_path = $ChildStdoutPath
    child_stderr_path = $ChildStderrPath
    child_stdout_tail = $childOutTail
    child_stderr_tail = $childErrTail
    launcher_started_at = $launcherStartedAt
    launcher_finished_at = $finishedAt
    python = $Python
    script_path = $ScriptPath
    command = $commandPreview
    exit_code = $exitCode
    reason = "child_process_exited"
    launch_mode = $launchMode
    auto_restart_requested = $autoRestartRequested
    auto_restart_wrapper_pid = $autoRestartWrapperPid
    auto_restart_error = $autoRestartError
    auto_restart_reason = $autoRestartReason
    auto_restart_delay_seconds = $nextRestartDelaySeconds
    auto_restart_failures_in_window = $nextRestartFailuresInWindow
    auto_restart_window_start_utc = $nextRestartWindowStartUtc
    auto_restart_limit_reached = $autoRestartLimitReached
    runtime_seconds = $runtimeSeconds
}
$exitState = Merge-Hashtable -Base $exitState -Extra $snapshot
Write-JsonFile -Path $LauncherStatePath -Payload $exitState
$exitEvent = Merge-Hashtable -Base @{
    ts_utc = $finishedAt
    event_type = "child_exited"
    wrapper_pid = $PID
    child_pid = $child.Id
    child_stdout_path = $ChildStdoutPath
    child_stderr_path = $ChildStderrPath
    exit_code = $exitCode
    runtime_seconds = $runtimeSeconds
    auto_restart_requested = $autoRestartRequested
    auto_restart_wrapper_pid = $autoRestartWrapperPid
    auto_restart_error = $autoRestartError
    auto_restart_reason = $autoRestartReason
    auto_restart_failures_in_window = $nextRestartFailuresInWindow
    auto_restart_window_start_utc = $nextRestartWindowStartUtc
    auto_restart_limit_reached = $autoRestartLimitReached
    auto_restart_delay_seconds = $nextRestartDelaySeconds
    child_stdout_tail = $childOutTail
    child_stderr_tail = $childErrTail
} -Extra $snapshot
Append-JsonLine -Path $LauncherEventsPath -Payload $exitEvent

exit $exitCode
