$reader = [System.IO.StreamReader]::new($args[0])
$closeTimes = @()
$openCount = 0; $closeCount = 0; $totalLines = 0
$levelOpenCount = @{}

while (($line = $reader.ReadLine()) -ne $null) {
    $totalLines++
    if ($line -match '"action":\s*"open_ticket"') {
        $openCount++
        $lvl = "0"
        if ($line -match '"level_idx":\s*(\d+)') { $lvl = $Matches[1] }
        if (-not $levelOpenCount.ContainsKey($lvl)) { $levelOpenCount[$lvl] = 0 }
        $levelOpenCount[$lvl]++
    }
    if ($line -match '"action":\s*"close_ticket"') {
        $closeCount++
        $ts = ""
        if ($line -match '"ts_utc":\s*"([^"]+)"') { $ts = $Matches[1] }
        if ($ts) { $closeTimes += $ts }
    }
}
$reader.Close()

Write-Output "=== CLOSE FREQUENCY ANALYSIS ==="
Write-Output "File: $([System.IO.Path]::GetFileName($args[0]))"
Write-Output "Events: $totalLines, Opens: $openCount, Closes: $closeCount"
if ($openCount -gt 0) {
    Write-Output "Close rate: $([math]::Round($closeCount / $openCount * 100, 1))%"
}
Write-Output ""

Write-Output "--- Opens by Level ---"
$levelOpenCount.GetEnumerator() | Sort-Object { [int]$_.Name } | ForEach-Object {
    Write-Output "  Level $($_.Name.PadLeft(2)): $($_.Value) opens"
}
Write-Output ""

if ($closeTimes.Count -gt 1) {
    Write-Output "--- Inter-Close Timing ($($closeTimes.Count) closes) ---"
    $prevTime = $null
    $intervals = @()
    foreach ($ts in $closeTimes) {
        $t = [DateTime]::Parse($ts)
        if ($prevTime) {
            $diff = ($t - $prevTime).TotalMinutes
            $intervals += $diff
        }
        $prevTime = $t
    }

    $sum = 0; $minVal = 999999; $maxVal = 0
    foreach ($i in $intervals) {
        $sum += $i
        if ($i -lt $minVal) { $minVal = $i }
        if ($i -gt $maxVal) { $maxVal = $i }
    }
    $avg = $sum / $intervals.Count

    Write-Output "  Avg interval: $([math]::Round($avg, 1)) min"
    Write-Output "  Min interval: $([math]::Round($minVal, 1)) min"
    Write-Output "  Max interval: $([math]::Round($maxVal, 1)) min"
    Write-Output ""

    Write-Output "--- Interval Distribution ---"
    $b1 = 0; $b2 = 0; $b3 = 0; $b4 = 0; $b5 = 0
    foreach ($i in $intervals) {
        if ($i -le 5) { $b1++ }
        elseif ($i -le 15) { $b2++ }
        elseif ($i -le 30) { $b3++ }
        elseif ($i -le 60) { $b4++ }
        else { $b5++ }
    }
    $n = $intervals.Count
    Write-Output "  0-5 min  : $b1 ($([math]::Round($b1/$n*100,1))%)"
    Write-Output "  5-15 min : $b2 ($([math]::Round($b2/$n*100,1))%)"
    Write-Output "  15-30 min: $b3 ($([math]::Round($b3/$n*100,1))%)"
    Write-Output "  30-60 min: $b4 ($([math]::Round($b4/$n*100,1))%)"
    Write-Output "  60+ min  : $b5 ($([math]::Round($b5/$n*100,1))%)"

    Write-Output ""
    Write-Output "--- Closes per Hour ---"
    $totalMins = 0
    if ($closeTimes.Count -gt 1) {
        $firstT = [DateTime]::Parse($closeTimes[0])
        $lastT = [DateTime]::Parse($closeTimes[$closeTimes.Count-1])
        $totalMins = ($lastT - $firstT).TotalMinutes
    }
    if ($totalMins -gt 0) {
        $cph = $closeTimes.Count / ($totalMins / 60)
        Write-Output "  Runtime: $([math]::Round($totalMins, 0)) min ($([math]::Round($totalMins/60, 1)) hours)"
        Write-Output "  Closes/hour: $([math]::Round($cph, 1))"
    }
} else {
    Write-Output "Not enough closes for interval analysis"
}
