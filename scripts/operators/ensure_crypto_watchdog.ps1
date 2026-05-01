$ErrorActionPreference = "Stop"

$RootDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$LoopStatePath = Join-Path $RootDir "reports\watchdog\crypto_watchdog_loop_state.json"
$LauncherScriptPath = Join-Path $RootDir "scripts\operators\start_crypto_watchdog_loop.ps1"
$LauncherStdoutPath = Join-Path $RootDir "reports\watchdog\crypto_watchdog_launcher.out.log"
$LauncherStderrPath = Join-Path $RootDir "reports\watchdog\crypto_watchdog_launcher.err.log"
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

function Get-LauncherProcess {
    Get-CimInstance Win32_Process |
        Where-Object {
            ($_.Name -eq "powershell.exe" -or $_.Name -eq "pwsh.exe") -and
            $_.CommandLine -and
            $_.CommandLine.ToLower().Contains($LauncherNeedle)
        } |
        Select-Object -First 1
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

New-Item -ItemType Directory -Force -Path (Split-Path $LoopStatePath -Parent) | Out-Null

$existing = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -eq "python.exe" -and
        $_.CommandLine -and
        $_.CommandLine -match [regex]::Escape("watch_penetration_lattice_runners.py") -and
        (Get-LoopNameFromCommandLine -CommandLine $_.CommandLine) -ieq "crypto_watchdog"
    } |
    Select-Object -First 1

if ($existing) {
    $launcher = Get-LauncherProcess
    if (-not $launcher) {
        $attachArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$LauncherScriptPath`" -ExistingChildPid $($existing.ProcessId)"
        $attachLaunch = Start-HiddenPowerShellProcess -ArgumentString $attachArgs -StdoutPath $LauncherStdoutPath -StderrPath $LauncherStderrPath
        $attachProcess = $attachLaunch.Process
        Start-Sleep -Seconds 1
        Write-Output ("crypto_watchdog launcher attached pid={0} child_pid={1}" -f $attachProcess.Id, $existing.ProcessId)
    }
    Write-Output ("crypto_watchdog already running pid={0}" -f $existing.ProcessId)
    if (Test-Path $LoopStatePath) {
        Get-Content $LoopStatePath
    }
    exit 0
}

if (-not (Test-Path $LauncherScriptPath)) {
    throw ("Missing launcher script: " + $LauncherScriptPath)
}

$launcherArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$LauncherScriptPath`""
$launch = Start-HiddenPowerShellProcess -ArgumentString $launcherArgs -StdoutPath $LauncherStdoutPath -StderrPath $LauncherStderrPath
$process = $launch.Process

Start-Sleep -Seconds 2

Write-Output ("crypto_watchdog launcher started pid={0}" -f $process.Id)
if (Test-Path $LoopStatePath) {
    Get-Content $LoopStatePath
}
