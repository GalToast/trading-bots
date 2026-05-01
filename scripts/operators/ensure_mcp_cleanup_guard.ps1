param(
    [double]$MinAgeSeconds = 120
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$ScriptPath = Join-Path $RootDir "mcp_process_cleanup.py"
$ReportPath = Join-Path $RootDir "reports\watchdog\mcp_process_cleanup_report.json"
$EventsPath = Join-Path $RootDir "reports\watchdog\mcp_process_cleanup_events.jsonl"

if (-not (Test-Path $ScriptPath)) {
    throw "Missing cleanup script: $ScriptPath"
}

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    throw "python is not on PATH"
}

& $pythonCmd.Source $ScriptPath --min-age-seconds $MinAgeSeconds --json-out $ReportPath --events-jsonl $EventsPath | Out-Null
