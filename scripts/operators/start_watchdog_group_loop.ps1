param(
    [Parameter(Mandatory = $true)]
    [string]$GroupName,
    [int]$ExistingChildPid = 0
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$ConfigPath = Join-Path $RootDir "configs\watchdog_groups.json"
$Python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}
$PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$AutoRestartMinRuntimeSeconds = 1.0
$AutoRestartDelaySeconds = 2.0
$AutoRestartBackoffCapSeconds = 20.0
$AutoRestartMaxAttemptsPerWindow = 3
$AutoRestartWindowSeconds = 120.0
$ChildLogTailLines = 80

function Start-HiddenPythonLoop([string[]]$ArgumentList, [string]$StdoutPath, [string]$StderrPath) {
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
                } catch {
                    continue
                }
            }

            try {
                Set-Content -Path $candidate -Value "" -Encoding UTF8
            } catch {
            }

            return $candidate
        }

        return $Path
    }

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

function Get-GroupConfig([string]$Name) {
    if (-not (Test-Path $ConfigPath)) {
        throw "Missing watchdog config: $ConfigPath"
    }
    $raw = Get-Content $ConfigPath -Raw | ConvertFrom-Json
    $group = $raw.groups.$Name
    if (-not $group) {
        throw "Unknown watchdog group: $Name"
    }
    $lanes = @()
    foreach ($lane in @($group.lanes)) {
        if ($lane) {
            $lanes += [string]$lane
        }
    }
    if ($lanes.Count -eq 0) {
        throw "Watchdog group has no lanes: $Name"
    }
    return @{
        label = [string]$group.label
        lanes = $lanes
    }
}

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

function Get-ArgumentValueFromCommandLine {
    param(
        [string]$CommandLine,
        [string]$FlagName
    )

    if (-not $CommandLine -or -not $FlagName) {
        return ""
    }

    $pattern = '(?i)(^|\s)' + [regex]::Escape($FlagName) + '(?:\s+|=)(?:"([^"]*)"|''([^'']*)''|([^\s]+))'
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

function Test-CommandLineHasSwitch {
    param(
        [string]$CommandLine,
        [string]$FlagName
    )

    if (-not $CommandLine -or -not $FlagName) {
        return $false
    }

    $pattern = '(?i)(^|\s)' + [regex]::Escape($FlagName) + '(?=\s|$)'
    return [regex]::IsMatch($CommandLine, $pattern)
}

function Normalize-CommandValue {
    param([string]$Value)

    return ([string]($Value -or "")).Trim().Replace("\", "/").ToLowerInvariant()
}

function Test-LoopProcessContract {
    param(
        [string]$CommandLine,
        [string]$TargetLoopName,
        [string[]]$ExpectedLoopStateValues,
        [string[]]$ExpectedReportJsonValues,
        [string[]]$ExpectedEventsValues
    )

    if ((Get-LoopNameFromCommandLine -CommandLine $CommandLine) -ine $TargetLoopName) {
        return $false
    }
    if (-not (Test-CommandLineHasSwitch -CommandLine $CommandLine -FlagName "--loop")) {
        return $false
    }
    if (-not (Test-CommandLineHasSwitch -CommandLine $CommandLine -FlagName "--repair")) {
        return $false
    }

    $loopStateValue = Normalize-CommandValue (Get-ArgumentValueFromCommandLine -CommandLine $CommandLine -FlagName "--loop-state-json")
    if (-not $loopStateValue) {
        return $false
    }
    $allowedLoopStateValues = @($ExpectedLoopStateValues | ForEach-Object { Normalize-CommandValue $_ } | Where-Object { $_ })
    if ($allowedLoopStateValues.Count -gt 0 -and -not ($allowedLoopStateValues -contains $loopStateValue)) {
        return $false
    }

    $reportJsonValue = Normalize-CommandValue (Get-ArgumentValueFromCommandLine -CommandLine $CommandLine -FlagName "--report-json")
    if (-not $reportJsonValue) {
        return $false
    }
    $allowedReportJsonValues = @($ExpectedReportJsonValues | ForEach-Object { Normalize-CommandValue $_ } | Where-Object { $_ })
    if ($allowedReportJsonValues.Count -gt 0 -and -not ($allowedReportJsonValues -contains $reportJsonValue)) {
        return $false
    }

    $eventsValue = Normalize-CommandValue (Get-ArgumentValueFromCommandLine -CommandLine $CommandLine -FlagName "--events-jsonl")
    if (-not $eventsValue) {
        return $false
    }
    $allowedEventsValues = @($ExpectedEventsValues | ForEach-Object { Normalize-CommandValue $_ } | Where-Object { $_ })
    if ($allowedEventsValues.Count -gt 0 -and -not ($allowedEventsValues -contains $eventsValue)) {
        return $false
    }

    return $true
}

function Get-ExistingLoopProcess {
    param(
        [string]$TargetLoopName,
        [string[]]$ExpectedLoopStateValues = @(),
        [string[]]$ExpectedReportJsonValues = @(),
        [string[]]$ExpectedEventsValues = @(),
        [int]$ExcludePid = 0
    )

    $candidates = Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq "python.exe" -and
            $_.CommandLine -and
            $_.CommandLine -match [regex]::Escape("watch_penetration_lattice_runners.py")
        }

    foreach ($candidate in $candidates) {
        if ($ExcludePid -gt 0 -and [int]$candidate.ProcessId -eq $ExcludePid) {
            continue
        }
        if (
            Test-LoopProcessContract `
                -CommandLine $candidate.CommandLine `
                -TargetLoopName $TargetLoopName `
                -ExpectedLoopStateValues $ExpectedLoopStateValues `
                -ExpectedReportJsonValues $ExpectedReportJsonValues `
                -ExpectedEventsValues $ExpectedEventsValues
        ) {
            return $candidate
        }
    }

    return $null
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

$group = Get-GroupConfig -Name $GroupName
$ScriptPath = Join-Path $RootDir "scripts\watch_penetration_lattice_runners.py"
$WatchdogDir = Join-Path $RootDir "reports\watchdog"
$LoopStateRelative = "reports/watchdog/${GroupName}_loop_state.json"
$ReportJsonRelative = "reports/watchdog/${GroupName}_report.json"
$ReportMdRelative = "reports/watchdog/${GroupName}_report.md"
$EventsRelative = "reports/watchdog/${GroupName}_events.jsonl"
$LauncherStatePath = Join-Path $WatchdogDir "${GroupName}_launcher_state.json"
$LauncherEventsPath = Join-Path $WatchdogDir "${GroupName}_launcher_events.jsonl"
$ChildStdoutPath = Join-Path $WatchdogDir "${GroupName}_loop.out.log"
$ChildStderrPath = Join-Path $WatchdogDir "${GroupName}_loop.err.log"
$LoopStatePath = Join-Path $RootDir $LoopStateRelative
$LoopLockPath = Join-Path $WatchdogDir "${GroupName}_loop_state.lock"
$ExpectedLoopStateValues = @($LoopStateRelative, $LoopStatePath)
$ExpectedReportJsonValues = @($ReportJsonRelative, (Join-Path $RootDir $ReportJsonRelative))
$ExpectedEventsValues = @($EventsRelative, (Join-Path $RootDir $EventsRelative))

function Get-LoopSnapshot {
    if (-not (Test-Path $LoopStatePath)) {
        return @{
            loop_state_present = $false
            loop_status = ""
            loop_updated_at = ""
            loop_pid = 0
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
        loop_pid = [int]($loopState.pid -or 0)
        loop_updated_at = $updatedAt
        loop_state_age_seconds = [math]::Round($ageSeconds, 1)
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
                $_.CommandLine -match [regex]::Escape("watch_penetration_lattice_runners.py")
            } |
            Select-Object -First 1

        if (-not $existingProcess) {
            throw "Target child pid $ExistingChildPid not found as python watchdog process."
        }

        $attachedLoopName = Get-LoopNameFromCommandLine -CommandLine $existingProcess.CommandLine
        if ($attachedLoopName -ine $GroupName) {
            throw ("Target child pid {0} loop-name mismatch: expected {1}, found {2}" -f $ExistingChildPid, $GroupName, $attachedLoopName)
        }
        if (-not (Test-LoopProcessContract -CommandLine $existingProcess.CommandLine -TargetLoopName $GroupName -ExpectedLoopStateValues $ExpectedLoopStateValues -ExpectedReportJsonValues $ExpectedReportJsonValues -ExpectedEventsValues $ExpectedEventsValues)) {
            throw ("Target child pid {0} does not match the expected watchdog contract for {1}" -f $ExistingChildPid, $GroupName)
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
            group_name = $GroupName
        }
        $failure = Merge-Hashtable -Base $failure -Extra (Get-LoopSnapshot)
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
            group_name = $GroupName
        } -Extra (Get-LoopSnapshot)
        Append-JsonLine -Path $LauncherEventsPath -Payload $failureEvent
        throw
    }
}
else {
    $existingLoop = Get-ExistingLoopProcess -TargetLoopName $GroupName -ExpectedLoopStateValues $ExpectedLoopStateValues -ExpectedReportJsonValues $ExpectedReportJsonValues -ExpectedEventsValues $ExpectedEventsValues
    if ($existingLoop) {
        $child = Get-Process -Id ([int]$existingLoop.ProcessId) -ErrorAction Stop
        $commandPreview = @("attach_existing_pid", [string]$existingLoop.ProcessId)
        $launchMode = "attached"
    }
    else {
    $argList = @(
        "scripts/watch_penetration_lattice_runners.py",
        "--repair",
        "--loop",
        "--interval-seconds", "30",
        "--loop-name", $GroupName,
        "--report-json", $ReportJsonRelative,
        "--report-md", $ReportMdRelative,
        "--events-jsonl", $EventsRelative,
        "--loop-state-json", $LoopStateRelative,
        "--lanes"
    ) + $group.lanes
    $commandPreview = @($Python) + $argList

    try {
        $child = Start-HiddenPythonLoop -ArgumentList $argList -StdoutPath $ChildStdoutPath -StderrPath $ChildStderrPath
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
            group_name = $GroupName
        }
        $failure = Merge-Hashtable -Base $failure -Extra (Get-LoopSnapshot)
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
            group_name = $GroupName
        } -Extra (Get-LoopSnapshot)
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
    group_name = $GroupName
}
$restartState = Get-RestartContext -StatePath $LauncherStatePath
$startedState = Merge-Hashtable -Base $startedState -Extra (Get-LoopSnapshot)
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
    group_name = $GroupName
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
$autoRestartReason = ""
$autoRestartLimitReached = $false
$nextRestartWindowStartUtc = (Get-UtcIsoNow)
$nextRestartFailuresInWindow = [int]$restartState.auto_restart_failures_in_window
$nextRestartDelaySeconds = $AutoRestartDelaySeconds
$childOutTail = Read-FileTail -Path $ChildStdoutPath -LineCount $ChildLogTailLines
$childErrTail = Read-FileTail -Path $ChildStderrPath -LineCount $ChildLogTailLines

if (
    $unexpectedExit -and
    $launchMode -eq "spawned" -and
    $runtimeSeconds -ge $AutoRestartMinRuntimeSeconds
) {
    $nextRestartFailuresInWindow = $nextRestartFailuresInWindow + 1
    $nextRestartWindowStartUtc = $restartState.auto_restart_window_start_utc
    if ($snapshot.loop_status -eq "starting") {
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
        $recoveredLoop = Get-ExistingLoopProcess -TargetLoopName $GroupName -ExpectedLoopStateValues $ExpectedLoopStateValues -ExpectedReportJsonValues $ExpectedReportJsonValues -ExpectedEventsValues $ExpectedEventsValues -ExcludePid $child.Id
        if ($recoveredLoop) {
            $autoRestartReason = "loop_already_recovered"
            $nextRestartFailuresInWindow = 0
            $nextRestartDelaySeconds = $AutoRestartDelaySeconds
            $nextRestartWindowStartUtc = (Get-UtcIsoNow)
        }
        else {
            try {
                Start-Sleep -Seconds $nextRestartDelaySeconds
                $recoveredLoop = Get-ExistingLoopProcess -TargetLoopName $GroupName -ExpectedLoopStateValues $ExpectedLoopStateValues -ExpectedReportJsonValues $ExpectedReportJsonValues -ExpectedEventsValues $ExpectedEventsValues -ExcludePid $child.Id
                if ($recoveredLoop) {
                    $autoRestartReason = "loop_already_recovered"
                    $nextRestartFailuresInWindow = 0
                    $nextRestartDelaySeconds = $AutoRestartDelaySeconds
                    $nextRestartWindowStartUtc = (Get-UtcIsoNow)
                }
                else {
                    $restartArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -GroupName $GroupName"
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
    group_name = $GroupName
    auto_restart_requested = $autoRestartRequested
    auto_restart_wrapper_pid = $autoRestartWrapperPid
    auto_restart_error = $autoRestartError
    auto_restart_reason = $autoRestartReason
    auto_restart_delay_seconds = $nextRestartDelaySeconds
    auto_restart_failures_in_window = $nextRestartFailuresInWindow
    auto_restart_window_start_utc = $nextRestartWindowStartUtc
    auto_restart_limit_reached = $autoRestartLimitReached
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
    group_name = $GroupName
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
