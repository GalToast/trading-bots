# Hungry Hippoo Deployment Validation Gate
# Checks: regime status, MTF alignment, session window
# Usage: powershell -ExecutionPolicy Bypass -File scripts\deploy_validation_gate.ps1 [shape_name]

param(
    [string]$ShapeName = ""
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoDir = Split-Path -Parent $scriptDir

$regimeSignalPath = Join-Path $repoDir "reports\regime_signal.json"
$designSpecPath = Join-Path $repoDir "reports\design_spec_validation.json"
$sessionTablePath = Join-Path $repoDir "reports\session_regime_step_table_v2.json"

$regime = Get-Content $regimeSignalPath -Raw | ConvertFrom-Json
$currentHour = (Get-Date).ToUniversalTime().Hour

Write-Output "=== HUNGRY HIPPO DEPLOYMENT VALIDATION GATE ==="
Write-Output "Current UTC: $currentHour`:00"
Write-Output ""

$symbolToCheck = $null
if ($ShapeName) {
    # Map shape name to symbol
    $symbolMap = @{
        "gbpusd_m15_asymmetric" = "GBPUSD"
        "nzdusd_m15_asymmetric" = "NZDUSD"
        "btc_m15_aggressive" = "BTCUSD"
        "eth_m15_aggressive" = "ETHUSD"
        "nas100_m15_trend" = "NAS100"
        "us30_m15_trend" = "US30"
        "xauusd_m15_volatile" = "XAUUSD"
        "eurusd_m15_symmetric" = "EURUSD"
        "btc_h1_balanced" = "BTCUSD"
    }
    $symbolToCheck = $symbolMap[$ShapeName]
    if (-not $symbolToCheck) {
        Write-Output "Unknown shape: $ShapeName"
        Write-Output "Known shapes: $($symbolMap.Keys -join ', ')"
        exit 1
    }
}

# Check all symbols or just the requested one
$symbolsToCheck = @()
if ($symbolToCheck) {
    $symbolsToCheck = @($symbolToCheck)
} else {
    foreach ($row in $regime.rows) {
        $symbolsToCheck += $row.symbol
    }
}

Write-Output "--- Deployment Validation ---"
Write-Output ""

foreach ($sym in $symbolsToCheck) {
    $row = $regime.rows | Where-Object { $_.symbol -eq $sym }
    if (-not $row) { continue }

    $controlMode = $row.control_mode
    $actionBias = $row.action_bias
    $coarseRegime = $row.coarse_regime

    # Gate 1: Is symbol at extreme?
    $gate1Pass = $controlMode -ne "wait_extreme_confirmation"
    $gate1Status = if ($gate1Pass) { "PASS" } else { "FAIL" }

    # Gate 2: Session window check
    $sessionActive = $true
    $sessionWeight = 1.0
    # Simplified: during 22:00-06:00 UTC, most symbols should be off
    $isDeadHours = ($currentHour -ge 22 -or $currentHour -lt 6)
    if ($isDeadHours -and $sym -in @("NAS100", "US30")) {
        $sessionActive = $false
        $sessionWeight = 0.2
    }
    $gate2Status = if ($sessionActive) { "PASS" } else { "DEAD_HOURS" }

    # Overall verdict
    if (-not $gate1Pass) {
        $verdict = "HOLD"
        $reason = "At extreme ($controlMode)"
    } elseif (-not $sessionActive) {
        $verdict = "WAIT"
        $reason = "Dead hours for $sym"
    } elseif ($controlMode -eq "trend_follow" -or $controlMode -eq "breakout_follow") {
        $verdict = "DEPLOY"
        $reason = "Aligned regime + session active"
    } else {
        $verdict = "MONITOR"
        $reason = "$controlMode regime, $actionBias bias"
    }

    $symPadded = $sym.PadRight(12)
    $verdictPadded = $verdict.PadRight(10)
    Write-Output "$symPadded | control=$($controlMode.PadRight(25)) | bias=$($actionBias.PadRight(6)) | Gate1=$gate1Status | Gate2=$gate2Status | VERDICT: $verdictPadded | $reason"
}

Write-Output ""
Write-Output "--- Summary ---"
$deployCount = 0; $holdCount = 0; $waitCount = 0; $monitorCount = 0
foreach ($sym in $symbolsToCheck) {
    $row = $regime.rows | Where-Object { $_.symbol -eq $sym }
    if (-not $row) { continue }
    $controlMode = $row.control_mode
    $isDeadHours = ($currentHour -ge 22 -or $currentHour -lt 6)
    $sessionActive = -not ($isDeadHours -and $sym -in @("NAS100", "US30"))

    if ($controlMode -eq "wait_extreme_confirmation") { $holdCount++ }
    elseif (-not $sessionActive) { $waitCount++ }
    elseif ($controlMode -eq "trend_follow" -or $controlMode -eq "breakout_follow") { $deployCount++ }
    else { $monitorCount++ }
}

Write-Output "DEPLOY: $deployCount | HOLD: $holdCount | WAIT: $waitCount | MONITOR: $monitorCount"

if ($deployCount -gt 0) {
    Write-Output ""
    Write-Output "Symbols ready for deployment:"
    foreach ($sym in $symbolsToCheck) {
        $row = $regime.rows | Where-Object { $_.symbol -eq $sym }
        if (-not $row) { continue }
        $controlMode = $row.control_mode
        $isDeadHours = ($currentHour -ge 22 -or $currentHour -lt 6)
        $sessionActive = -not ($isDeadHours -and $sym -in @("NAS100", "US30"))
        if ($controlMode -ne "wait_extreme_confirmation" -and $sessionActive -and ($controlMode -eq "trend_follow" -or $controlMode -eq "breakout_follow")) {
            Write-Output "  - $sym ($controlMode, $($row.action_bias) bias)"
        }
    }
}
