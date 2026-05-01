param(
    [Parameter(Mandatory = $true)]
    [string]$GroupName,
    [switch]$ReloadIfDrift,
    [switch]$ForceReload,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$ConfigPath = Join-Path $RootDir "configs\watchdog_groups.json"
$LoopStatePath = Join-Path $RootDir "reports\watchdog\${GroupName}_loop_state.json"
$LauncherScriptPath = Join-Path $RootDir "scripts\operators\start_watchdog_group_loop.ps1"
$LauncherStdoutPath = Join-Path $RootDir "reports\watchdog\${GroupName}_launcher.out.log"
$LauncherStderrPath = Join-Path $RootDir "reports\watchdog\${GroupName}_launcher.err.log"
$LoopLockPath = Join-Path $RootDir "reports\watchdog\${GroupName}_loop_state.lock"
$LoopStateRelative = "reports/watchdog/${GroupName}_loop_state.json"
$ReportJsonRelative = "reports/watchdog/${GroupName}_report.json"
$EventsRelative = "reports/watchdog/${GroupName}_events.jsonl"
$ReportJsonPath = Join-Path $RootDir $ReportJsonRelative
$EventsPath = Join-Path $RootDir $EventsRelative
$PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$LauncherNeedle = $LauncherScriptPath.ToLower()

function Start-HiddenPowerShellProcess([string]$ArgumentString, [string]$StdoutPath, [string]$StderrPath) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PowerShellExe
    $psi.Arguments = $ArgumentString
    $psi.WorkingDirectory = $RootDir
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    $proc.Start() | Out-Null

    $stdout = $proc.StandardOutput.ReadToEndAsync()
    $stderr = $proc.StandardError.ReadToEndAsync()
    Start-Sleep -Milliseconds 250

    if (-not $proc.HasExited) {
        return [pscustomobject]@{ Process = $proc; ExitCode = $null }
    }

    $proc.WaitForExit()
    $stdoutText = $stdout.GetAwaiter().GetResult()
    $stderrText = $stderr.GetAwaiter().GetResult()
    if ($stdoutText) {
        Set-Content -Path $StdoutPath -Value $stdoutText -Encoding UTF8
    }
    if ($stderrText) {
        Set-Content -Path $StderrPath -Value $stderrText -Encoding UTF8
    }
    return [pscustomobject]@{ Process = $proc; ExitCode = $proc.ExitCode }
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
    return $group
}

function Get-LauncherProcesses {
    Get-CimInstance Win32_Process |
        Where-Object {
            ($_.Name -eq "powershell.exe" -or $_.Name -eq "pwsh.exe") -and
            $_.CommandLine -and
            $_.CommandLine.ToLower().Contains($LauncherNeedle) -and
            $_.CommandLine.ToLower().Contains($GroupName.ToLower())
        }
}

function Get-LoopStatePayload([string]$Path) {
    if (-not (Test-Path $Path)) {
        return $null
    }
    try {
        return Get-Content $Path -Raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Get-UniqueStringList([object[]]$Items) {
    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $values = New-Object 'System.Collections.Generic.List[string]'
    foreach ($item in @($Items)) {
        $text = [string]$item
        if (-not [string]::IsNullOrWhiteSpace($text) -and $seen.Add($text)) {
            [void]$values.Add($text)
        }
    }
    return @($values.ToArray())
}

function Convert-ToSafeString([object]$Value) {
    if ($null -eq $Value) {
        return ""
    }
    return [string]$Value
}

function Get-GroupMembershipDrift {
    param(
        [string[]]$ConfiguredLanes,
        [string]$LoopStatePath
    )

    $configured = @(Get-UniqueStringList -Items $ConfiguredLanes)
    $loopState = Get-LoopStatePayload -Path $LoopStatePath
    if (-not $loopState) {
        return @{
            loop_state_present = $false
            loop_status = ""
            loop_updated_at = ""
            configured_lanes = $configured
            running_lanes = @()
            missing_lanes = @()
            extra_lanes = @()
            drift = $false
        }
    }

    $running = @(Get-UniqueStringList -Items @($loopState.lanes))
    $missing = New-Object 'System.Collections.Generic.List[string]'
    foreach ($lane in $configured) {
        if (-not ($running -contains $lane)) {
            [void]$missing.Add($lane)
        }
    }

    $extra = New-Object 'System.Collections.Generic.List[string]'
    foreach ($lane in $running) {
        if (-not ($configured -contains $lane)) {
            [void]$extra.Add($lane)
        }
    }

    return @{
        loop_state_present = $true
        loop_status = Convert-ToSafeString -Value $loopState.status
        loop_updated_at = Convert-ToSafeString -Value $loopState.updated_at
        configured_lanes = $configured
        running_lanes = $running
        missing_lanes = @($missing.ToArray())
        extra_lanes = @($extra.ToArray())
        drift = ($missing.Count -gt 0 -or $extra.Count -gt 0)
    }
}

function Stop-TrackedProcess {
    param(
        [int]$ProcessId,
        [string]$Role
    )

    if ($ProcessId -le 0) {
        return
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction Stop
    Write-Output ("{0} stopped pid={1}" -f $Role, $ProcessId)
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
    $allowedLoopStateValues = @($ExpectedLoopStateValues | ForEach-Object { Normalize-CommandValue $_ } | Where-Object { $_ })
    if (-not $loopStateValue -or ($allowedLoopStateValues.Count -gt 0 -and -not ($allowedLoopStateValues -contains $loopStateValue))) {
        return $false
    }

    $reportJsonValue = Normalize-CommandValue (Get-ArgumentValueFromCommandLine -CommandLine $CommandLine -FlagName "--report-json")
    $allowedReportJsonValues = @($ExpectedReportJsonValues | ForEach-Object { Normalize-CommandValue $_ } | Where-Object { $_ })
    if (-not $reportJsonValue -or ($allowedReportJsonValues.Count -gt 0 -and -not ($allowedReportJsonValues -contains $reportJsonValue))) {
        return $false
    }

    $eventsValue = Normalize-CommandValue (Get-ArgumentValueFromCommandLine -CommandLine $CommandLine -FlagName "--events-jsonl")
    $allowedEventsValues = @($ExpectedEventsValues | ForEach-Object { Normalize-CommandValue $_ } | Where-Object { $_ })
    if (-not $eventsValue -or ($allowedEventsValues.Count -gt 0 -and -not ($allowedEventsValues -contains $eventsValue))) {
        return $false
    }

    return $true
}

function Get-ExistingLoopProcess {
    param(
        [string]$TargetLoopName,
        [string[]]$ExpectedLoopStateValues = @(),
        [string[]]$ExpectedReportJsonValues = @(),
        [string[]]$ExpectedEventsValues = @()
    )

    $candidates = Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq "python.exe" -and
            $_.CommandLine -and
            $_.CommandLine -match [regex]::Escape("watch_penetration_lattice_runners.py")
        }

    foreach ($candidate in $candidates) {
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

$group = Get-GroupConfig -Name $GroupName

New-Item -ItemType Directory -Force -Path (Split-Path $LoopStatePath -Parent) | Out-Null
$ExpectedLoopStateValues = @($LoopStateRelative, $LoopStatePath)
$ExpectedReportJsonValues = @($ReportJsonRelative, $ReportJsonPath)
$ExpectedEventsValues = @($EventsRelative, $EventsPath)
$driftInfo = Get-GroupMembershipDrift -ConfiguredLanes $group.lanes -LoopStatePath $LoopStatePath

$existing = Get-ExistingLoopProcess -TargetLoopName $GroupName -ExpectedLoopStateValues $ExpectedLoopStateValues -ExpectedReportJsonValues $ExpectedReportJsonValues -ExpectedEventsValues $ExpectedEventsValues
if (-not $existing -and (Test-Path $LoopLockPath)) {
    try {
        $lockData = Get-Content $LoopLockPath -Raw | ConvertFrom-Json
        if ([string]($lockData.loop_name -or "") -ieq [string]$GroupName) {
            $ownerPid = [int]($lockData.pid -or 0)
            if ($ownerPid -gt 0) {
                $existing = Get-CimInstance Win32_Process |
                    Where-Object {
                        $_.ProcessId -eq $ownerPid -and
                        $_.Name -eq "python.exe" -and
                        $_.CommandLine -and
                        $_.CommandLine -match [regex]::Escape("watch_penetration_lattice_runners.py") -and
                        (Test-LoopProcessContract -CommandLine $_.CommandLine -TargetLoopName $GroupName -ExpectedLoopStateValues $ExpectedLoopStateValues -ExpectedReportJsonValues $ExpectedReportJsonValues -ExpectedEventsValues $ExpectedEventsValues)
                    } |
                    Select-Object -First 1
            }
        }
    } catch {
    }
}

$launcherProcesses = @(Get-LauncherProcesses)
$reloadReason = ""
$reloadRequested = $false
if ($ForceReload) {
    $reloadRequested = $true
    $reloadReason = "force_reload"
} elseif ($ReloadIfDrift -and $existing -and $driftInfo.drift) {
    $reloadRequested = $true
    $reloadReason = "membership_drift"
}

if ($existing) {
    if ($reloadRequested) {
        $reloadPlan = @{
            group_name = $GroupName
            reload_reason = $reloadReason
            existing_child_pid = [int]$existing.ProcessId
            existing_launcher_pids = @($launcherProcesses | ForEach-Object { [int]$_.ProcessId })
            configured_lanes = @($driftInfo.configured_lanes)
            running_lanes = @($driftInfo.running_lanes)
            missing_lanes = @($driftInfo.missing_lanes)
            extra_lanes = @($driftInfo.extra_lanes)
            loop_state_present = [bool]$driftInfo.loop_state_present
            loop_status = [string]$driftInfo.loop_status
            loop_updated_at = [string]$driftInfo.loop_updated_at
            dry_run = [bool]$DryRun
        }
        $reloadPlan | ConvertTo-Json -Depth 6
        if ($DryRun) {
            if (Test-Path $LoopStatePath) {
                Get-Content $LoopStatePath
            }
            exit 0
        }

        foreach ($launcher in $launcherProcesses) {
            Stop-TrackedProcess -ProcessId ([int]$launcher.ProcessId) -Role "launcher"
        }
        Start-Sleep -Milliseconds 500
        Stop-TrackedProcess -ProcessId ([int]$existing.ProcessId) -Role "child"
        Start-Sleep -Seconds 1

        $launcherArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$LauncherScriptPath`" -GroupName $GroupName"
        $launch = Start-HiddenPowerShellProcess -ArgumentString $launcherArgs -StdoutPath $LauncherStdoutPath -StderrPath $LauncherStderrPath
        $process = $launch.Process

        Start-Sleep -Seconds 2

        Write-Output ("{0} launcher reloaded pid={1} replaced_child_pid={2} reason={3}" -f $GroupName, $process.Id, $existing.ProcessId, $reloadReason)
        if (Test-Path $LoopStatePath) {
            Get-Content $LoopStatePath
        }
        exit 0
    }

    if ($launcherProcesses.Count -eq 0) {
        $attachArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$LauncherScriptPath`" -GroupName $GroupName -ExistingChildPid $($existing.ProcessId)"
        $attachLaunch = Start-HiddenPowerShellProcess -ArgumentString $attachArgs -StdoutPath $LauncherStdoutPath -StderrPath $LauncherStderrPath
        $attachProcess = $attachLaunch.Process
        Start-Sleep -Seconds 1
        Write-Output ("{0} launcher attached pid={1} child_pid={2}" -f $GroupName, $attachProcess.Id, $existing.ProcessId)
    }
    Write-Output ("{0} already running pid={1}" -f $GroupName, $existing.ProcessId)
    if (Test-Path $LoopStatePath) {
        Get-Content $LoopStatePath
    }
    exit 0
}

if (-not (Test-Path $LauncherScriptPath)) {
    throw ("Missing launcher script: " + $LauncherScriptPath)
}

$launcherArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$LauncherScriptPath`" -GroupName $GroupName"
$launch = Start-HiddenPowerShellProcess -ArgumentString $launcherArgs -StdoutPath $LauncherStdoutPath -StderrPath $LauncherStderrPath
$process = $launch.Process

Start-Sleep -Seconds 2

Write-Output ("{0} launcher started pid={1}" -f $GroupName, $process.Id)
if (Test-Path $LoopStatePath) {
    Get-Content $LoopStatePath
}
