param(
    [int]$ExistingChildPid = 0
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$ConfigPath = Join-Path $RootDir "configs\watchdog_groups.json"
$WatchdogDir = Join-Path $RootDir "reports\watchdog"
$LoopStatePath = Join-Path $WatchdogDir "crypto_watchdog_loop_state.json"
$LauncherStatePath = Join-Path $WatchdogDir "crypto_watchdog_launcher_state.json"
$LauncherEventsPath = Join-Path $WatchdogDir "crypto_watchdog_launcher_events.jsonl"
$ChildStdoutPath = Join-Path $WatchdogDir "crypto_watchdog_loop.out.log"
$ChildStderrPath = Join-Path $WatchdogDir "crypto_watchdog_loop.err.log"
$ScriptPath = Join-Path $RootDir "scripts\watch_penetration_lattice_runners.py"
$Python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}
$PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

function Start-HiddenPythonLoop([string[]]$ArgumentList, [string]$StdoutPath, [string]$StderrPath) {
    $escapedRootDir = $RootDir.Replace("'", "''")
    $escapedPython = $Python.Replace("'", "''")
    $escapedStdoutPath = $StdoutPath.Replace("'", "''")
    $escapedStderrPath = $StderrPath.Replace("'", "''")
    $quotedArgs = @()
    foreach ($arg in $ArgumentList) {
        $quotedArgs += ("'" + ([string]$arg).Replace("'", "''") + "'")
    }
    $joinedArgs = [string]::Join(" ", $quotedArgs)
    $command = "& { Set-Location '$escapedRootDir'; & '$escapedPython' $joinedArgs 1>> '$escapedStdoutPath' 2>> '$escapedStderrPath'; exit `$LASTEXITCODE }"

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe")
    $psi.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command $command"
    $psi.WorkingDirectory = $RootDir
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    $proc.Start() | Out-Null
    return $proc
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

function Get-ConfiguredLaneNames {
    if (-not (Test-Path $ConfigPath)) {
        throw "Missing watchdog config: $ConfigPath"
    }

    $raw = Get-Content $ConfigPath -Raw | ConvertFrom-Json
    $group = $raw.groups.crypto_watchdog
    if (-not $group) {
        throw "Missing crypto_watchdog group in $ConfigPath"
    }

    $lanes = @()
    foreach ($lane in @($group.lanes)) {
        if ($lane) {
            $lanes += [string]$lane
        }
    }
    if ($lanes.Count -eq 0) {
        throw "crypto_watchdog group has no lanes in $ConfigPath"
    }
    return $lanes
}

$LaneNames = Get-ConfiguredLaneNames

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

function Get-LoopNameFromCommandLine {
    param([string]$CommandLine)

    if (-not $CommandLine) {
        return ""
    }

    $pattern = '(?i)(^|\s)--loop-name(?:\s+|=)(?:"([^"]*)"|''([^'']*)''|([^\s]+))'
    $match = [regex]::Match($CommandLine, $pattern)
    if (-not $match.Success) {
        return ""
    }

    if ($match.Groups[2].Value) {
        return $match.Groups[2].Value
    }
    if ($match.Groups[3].Value) {
        return $match.Groups[3].Value
    }
    return $match.Groups[4].Value
}

function Get-LoopSnapshot {
    if (-not (Test-Path $LoopStatePath)) {
        return @{
            loop_state_present = $false
            loop_status = ""
            loop_updated_at = ""
            loop_state_age_seconds = -1.0
        }
    }

    try {
        $loopState = Get-Content $LoopStatePath -Raw | ConvertFrom-Json
    }
    catch {
        return @{
            loop_state_present = $false
            loop_status = "parse_failed"
            loop_updated_at = ""
            loop_state_age_seconds = -1.0
        }
    }

    $updatedAt = ""
    $ageSeconds = -1.0
    if ($loopState.updated_at) {
        $updatedAt = [string]$loopState.updated_at
        try {
            $ageSeconds = [math]::Max(0.0, ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($updatedAt)).TotalSeconds)
        }
        catch {
            $ageSeconds = -1.0
        }
    }

    return @{
        loop_state_present = $true
        loop_status = [string]$loopState.status
        loop_updated_at = $updatedAt
        loop_state_age_seconds = [math]::Round($ageSeconds, 1)
    }
}

New-Item -ItemType Directory -Force -Path $WatchdogDir | Out-Null

$launcherStartedAt = Get-UtcIsoNow
$child = $null
$commandPreview = @()
$launchMode = "spawned"

# Restart-rate-limit state (mirrors group-wrapper pattern)
$RestartWindowSeconds = 120
$RestartMaxAttempts = 3
$RestartBaseDelaySeconds = 2
$RestartLogFile = Join-Path $WatchdogDir "crypto_watchdog_restart_history.jsonl"

function Test-RestartRateLimit {
    if (-not (Test-Path $RestartLogFile)) {
        return $true  # allow, no history
    }
    try {
        $now = [DateTimeOffset]::UtcNow
        $recentCount = 0
        Get-Content $RestartLogFile -Raw | ConvertFrom-Json -ErrorAction Stop | ForEach-Object {
            $entry = $_
            if ($entry.ts_utc) {
                try {
                    $entryTime = [DateTimeOffset]::Parse([string]$entry.ts_utc)
                    $age = ($now - $entryTime).TotalSeconds
                    if ($age -le $RestartWindowSeconds) {
                        $recentCount++
                    }
                } catch {}
            }
        }
        if ($recentCount -ge $RestartMaxAttempts) {
            return $false
        }
        return $true
    }
    catch {
        return $true  # allow on parse error
    }
}

function Get-RestartBackoffSeconds {
    if (-not (Test-Path $RestartLogFile)) {
        return $RestartBaseDelaySeconds
    }
    try {
        $now = [DateTimeOffset]::UtcNow
        $recentCount = 0
        Get-Content $RestartLogFile -Raw | ConvertFrom-Json -ErrorAction Stop | ForEach-Object {
            $entry = $_
            if ($entry.ts_utc) {
                try {
                    $entryTime = [DateTimeOffset]::Parse([string]$entry.ts_utc)
                    $age = ($now - $entryTime).TotalSeconds
                    if ($age -le $RestartWindowSeconds) {
                        $recentCount++
                    }
                } catch {}
            }
        }
        # Exponential backoff: 2s, 4s, 8s, capped at 20s
        $delay = [math]::Min($RestartBaseDelaySeconds * [math]::Pow(2, $recentCount), 20)
        return [int]$delay
    }
    catch {
        return $RestartBaseDelaySeconds
    }
}

function Write-RestartLog {
    param([string]$Reason)
    $entry = @{
        ts_utc = Get-UtcIsoNow
        reason = $Reason
        wrapper_pid = $PID
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $RestartLogFile -Parent) | Out-Null
    Add-Content -Path $RestartLogFile -Value (($entry | ConvertTo-Json -Compress))
}

if ($ExistingChildPid -gt 0) {
    try {
        $existingProcess = Get-CimInstance Win32_Process |
            Where-Object {
                $_.ProcessId -eq $ExistingChildPid -and
                $_.Name -eq "python.exe" -and
                $_.CommandLine -and
                $_.CommandLine -match [regex]::Escape("watch_penetration_lattice_runners.py") -and
                (Get-LoopNameFromCommandLine -CommandLine $_.CommandLine) -ieq "crypto_watchdog"
            } |
            Select-Object -First 1

        if (-not $existingProcess) {
            throw "Target child pid $ExistingChildPid not found as python crypto_watchdog process."
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
            launcher_started_at = $launcherStartedAt
            launcher_finished_at = Get-UtcIsoNow
            python = $Python
            script_path = $ScriptPath
            command = @("attach_existing_pid", [string]$ExistingChildPid)
            exit_code = $null
            reason = $_.Exception.Message
            launch_mode = "attached"
        }
        $failure = Merge-Hashtable -Base $failure -Extra (Get-LoopSnapshot)
        Write-JsonFile -Path $LauncherStatePath -Payload $failure
        $failureEvent = Merge-Hashtable -Base @{
            ts_utc = Get-UtcIsoNow
            event_type = "attach_failed"
            wrapper_pid = $PID
            child_pid = $ExistingChildPid
            reason = $_.Exception.Message
            python = $Python
            command = @("attach_existing_pid", [string]$ExistingChildPid)
            launch_mode = "attached"
        } -Extra (Get-LoopSnapshot)
        Append-JsonLine -Path $LauncherEventsPath -Payload $failureEvent
        throw
    }
}
else {
    $argList = @(
        "scripts/watch_penetration_lattice_runners.py",
        "--repair",
        "--loop",
        "--interval-seconds", "30",
        "--loop-name", "crypto_watchdog",
        "--report-json", "reports/watchdog/crypto_watchdog_report.json",
        "--report-md", "reports/watchdog/crypto_watchdog_report.md",
        "--events-jsonl", "reports/watchdog/crypto_watchdog_events.jsonl",
        "--loop-state-json", "reports/watchdog/crypto_watchdog_loop_state.json",
        "--lanes"
    ) + $LaneNames
    $commandPreview = @($Python) + $argList

    try {
        $child = Start-HiddenPythonLoop -ArgumentList $argList -StdoutPath $ChildStdoutPath -StderrPath $ChildStderrPath
    }
    catch {
        $failure = @{
            status = "launch_failed"
            wrapper_pid = $PID
            child_pid = 0
            launcher_started_at = $launcherStartedAt
            launcher_finished_at = Get-UtcIsoNow
            python = $Python
            script_path = $ScriptPath
            command = $commandPreview
            exit_code = $null
            reason = $_.Exception.Message
            launch_mode = "spawned"
        }
        $failure = Merge-Hashtable -Base $failure -Extra (Get-LoopSnapshot)
        Write-JsonFile -Path $LauncherStatePath -Payload $failure
        $failureEvent = Merge-Hashtable -Base @{
            ts_utc = Get-UtcIsoNow
            event_type = "launch_failed"
            wrapper_pid = $PID
            child_pid = 0
            reason = $_.Exception.Message
            python = $Python
            command = $commandPreview
            launch_mode = "spawned"
        } -Extra (Get-LoopSnapshot)
        Append-JsonLine -Path $LauncherEventsPath -Payload $failureEvent
        throw
    }
}

$startedState = @{
    status = "running"
    wrapper_pid = $PID
    child_pid = $child.Id
    launcher_started_at = $launcherStartedAt
    launcher_finished_at = ""
    python = $Python
    script_path = $ScriptPath
    command = $commandPreview
    exit_code = $null
    reason = ""
    launch_mode = $launchMode
}
$startedState = Merge-Hashtable -Base $startedState -Extra (Get-LoopSnapshot)
Write-JsonFile -Path $LauncherStatePath -Payload $startedState
$startEvent = Merge-Hashtable -Base @{
    ts_utc = Get-UtcIsoNow
    event_type = if ($launchMode -eq "attached") { "attached_existing_child" } else { "child_started" }
    wrapper_pid = $PID
    child_pid = $child.Id
    python = $Python
    command = $commandPreview
    launch_mode = $launchMode
} -Extra (Get-LoopSnapshot)
Append-JsonLine -Path $LauncherEventsPath -Payload $startEvent

$null = $child.WaitForExit()
$child.Refresh()
$exitCode = $child.ExitCode
$finishedAt = Get-UtcIsoNow
$runtimeSeconds = [math]::Round(([DateTimeOffset]::Parse($finishedAt) - [DateTimeOffset]::Parse($launcherStartedAt)).TotalSeconds, 1)
$snapshot = Get-LoopSnapshot
$unexpectedExit = ($null -eq $exitCode -or $exitCode -ne 0)
$autoRestartRequested = $false
$autoRestartWrapperPid = 0
$autoRestartError = ""
$autoRestartSkippedReason = ""
if ($unexpectedExit -and $launchMode -eq "spawned" -and $runtimeSeconds -ge 30.0) {
    if (-not (Test-RestartRateLimit)) {
        $autoRestartSkippedReason = "restart_rate_limited"
        Write-RestartLog -Reason "rate_limited_max_$RestartMaxAttempts_in_${RestartWindowSeconds}s"
    }
    else {
        $backoffSec = Get-RestartBackoffSeconds
        Write-RestartLog -Reason "restarting_after_${runtimeSeconds}s_run_backoff_${backoffSec}s"
        try {
            Start-Sleep -Seconds $backoffSec
            $restartArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
            $restartWrapper = Start-HiddenWrapperProcess -ArgumentString $restartArgs
            $autoRestartRequested = $true
            $autoRestartWrapperPid = $restartWrapper.Id
        }
        catch {
            $autoRestartError = $_.Exception.Message
        }
    }
}
$exitState = @{
    status = if ($exitCode -eq 0) { "child_exited_clean" } else { "child_exited_unexpected" }
    wrapper_pid = $PID
    child_pid = $child.Id
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
    auto_restart_skipped_reason = $autoRestartSkippedReason
}
$exitState = Merge-Hashtable -Base $exitState -Extra $snapshot
Write-JsonFile -Path $LauncherStatePath -Payload $exitState
$exitEvent = Merge-Hashtable -Base @{
    ts_utc = $finishedAt
    event_type = "child_exited"
    wrapper_pid = $PID
    child_pid = $child.Id
    exit_code = $exitCode
    runtime_seconds = $runtimeSeconds
    auto_restart_requested = $autoRestartRequested
    auto_restart_wrapper_pid = $autoRestartWrapperPid
    auto_restart_error = $autoRestartError
    auto_restart_skipped_reason = $autoRestartSkippedReason
} -Extra $snapshot
Append-JsonLine -Path $LauncherEventsPath -Payload $exitEvent

exit $exitCode
