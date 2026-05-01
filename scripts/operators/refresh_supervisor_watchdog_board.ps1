$ErrorActionPreference = "Stop"

$RootDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$ExecutionMonitorScriptPath = Join-Path $RootDir "scripts\build_execution_monitor_report.py"
$TradeFiringBoardScriptPath = Join-Path $RootDir "scripts\build_trade_firing_board.py"
$TradeFiringAlertScriptPath = Join-Path $RootDir "scripts\operators\emit_trade_firing_alerts.ps1"
$BoardScriptPath = Join-Path $RootDir "scripts\build_supervisor_watchdog_board.py"
$LedgerScriptPath = Join-Path $RootDir "scripts\build_watchdog_incident_ledger.py"
$FinalityScriptPath = Join-Path $RootDir "scripts\build_supervision_finality_board.py"
$PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$Python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

if (-not (Test-Path $ExecutionMonitorScriptPath)) {
    throw "Missing execution monitor builder: $ExecutionMonitorScriptPath"
}
if (-not (Test-Path $TradeFiringBoardScriptPath)) {
    throw "Missing trade firing board builder: $TradeFiringBoardScriptPath"
}
if (-not (Test-Path $TradeFiringAlertScriptPath)) {
    throw "Missing trade firing alert script: $TradeFiringAlertScriptPath"
}
if (-not (Test-Path $BoardScriptPath)) {
    throw "Missing supervisor board builder: $BoardScriptPath"
}
if (-not (Test-Path $LedgerScriptPath)) {
    throw "Missing incident ledger builder: $LedgerScriptPath"
}
if (-not (Test-Path $FinalityScriptPath)) {
    throw "Missing supervision finality builder: $FinalityScriptPath"
}

function Invoke-PythonScript([string]$ScriptPath, [string]$Label) {
    & $Python $ScriptPath *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "$Label exited with code $LASTEXITCODE"
    }
}

function Invoke-PowerShellScript([string]$ScriptPath, [string]$Label) {
    & $ScriptPath
    if ($LASTEXITCODE -ne 0) {
        throw "$Label exited with code $LASTEXITCODE"
    }
}

Push-Location $RootDir
$refreshMutex = New-Object System.Threading.Mutex($false, "Local\TradingBotsSupervisorWatchdogBoardRefresh")
$refreshLockTaken = $false
try {
    $refreshLockTaken = $refreshMutex.WaitOne(0, $false)
    if (-not $refreshLockTaken) {
        exit 0
    }

    Invoke-PythonScript -ScriptPath $ExecutionMonitorScriptPath -Label "Execution monitor refresh"
    Invoke-PythonScript -ScriptPath $TradeFiringBoardScriptPath -Label "Trade firing board refresh"
    Invoke-PowerShellScript -ScriptPath $TradeFiringAlertScriptPath -Label "Trade firing alert refresh"
    Invoke-PythonScript -ScriptPath $BoardScriptPath -Label "Board refresh"
    Invoke-PythonScript -ScriptPath $LedgerScriptPath -Label "Incident ledger refresh"
    Invoke-PythonScript -ScriptPath $FinalityScriptPath -Label "Supervision finality refresh"
}
finally {
    if ($refreshLockTaken) {
        $refreshMutex.ReleaseMutex() | Out-Null
    }
    $refreshMutex.Dispose()
    Pop-Location
}
