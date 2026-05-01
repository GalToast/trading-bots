# Autonomous race monitor - RAW vs PRICE
$RootDir = $PSScriptRoot
while ($RootDir -and -not (Test-Path (Join-Path $RootDir 'mt5_bot_v10.py'))) {
    $parent = Split-Path $RootDir -Parent
    if (-not $parent -or $parent -eq $RootDir) {
        break
    }
    $RootDir = $parent
}

$WorkerLog = Join-Path $RootDir 'mt5_canonical_worker_out.log'
$Scratchpad = Join-Path $RootDir 'docs\agent-scratchpad.md'

$i = 0
while ($true) {
    $i++
    $time = Get-Date -Format "HH:mm:ss"
    Write-Host "=== Race Check $time | iter $i ==="
    
    # Check for trades
    Get-Content $WorkerLog -Tail 20 | Select-String "ACTIVE|OPEN \[(RAW|PRICE)|EXIT|WIN_BAG"
    
    # Check scratchpad
    Write-Host "--- Scratchpad ---"
    Get-Content $Scratchpad -Tail 5
    
    Write-Host ""
    Start-Sleep 30
}
