$ErrorActionPreference = "Stop"

$RootDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$RefreshScriptPath = Join-Path $RootDir "scripts\refresh_fx_operator_surfaces.py"
$PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$Python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}
if (-not (Test-Path $PowerShellExe)) {
    $PowerShellExe = "powershell"
}

if (-not (Test-Path $RefreshScriptPath)) {
    throw "Missing FX operator refresh script: $RefreshScriptPath"
}

function Invoke-HiddenPythonScript([string]$ScriptPath, [string]$Label) {
    $escapedRootDir = $RootDir.Replace("'", "''")
    $escapedPython = $Python.Replace("'", "''")
    $escapedScriptPath = $ScriptPath.Replace("'", "''")
    $command = "& { Set-Location '$escapedRootDir'; & '$escapedPython' '$escapedScriptPath' *> `$null; exit `$LASTEXITCODE }"
    $process = Start-Process -FilePath $PowerShellExe `
        -ArgumentList @(
            '-NoProfile',
            '-WindowStyle', 'Hidden',
            '-ExecutionPolicy', 'Bypass',
            '-Command', $command
        ) `
        -WorkingDirectory $RootDir `
        -WindowStyle Hidden `
        -PassThru `
        -Wait
    if ($process.ExitCode -ne 0) {
        throw "$Label exited with code $($process.ExitCode)"
    }
}

Push-Location $RootDir
try {
    Invoke-HiddenPythonScript -ScriptPath $RefreshScriptPath -Label "FX operator refresh"
}
finally {
    Pop-Location
}
