$reader = [System.IO.StreamReader]::new($args[0])
$closes = 0; $levelDepth = @{}; $totalLines = 0; $hourBuckets = @{}; $directionBuckets = @{}; $holdBars = @{}
while (($line = $reader.ReadLine()) -ne $null) {
    $totalLines++
    if ($line -match '"action":\s*"close_ticket"') {
        $closes++
        $depth = '0'
        if ($line -match '"level_idx":\s*(\d+)') { $depth = $Matches[1] }
        $pnl = 0.0; $dir = "unknown"; $bars = 0
        if ($line -match '"realized_pnl":\s*([0-9.e-]+)') { $pnl = [double]$Matches[1] }
        if ($line -match '"direction":\s*"(\w+)"') { $dir = $Matches[1] }
        if ($line -match '"hold_bars":\s*(\d+)') { $bars = [int]$Matches[1] }
        $hour = "unknown"
        if ($line -match '"ts_utc":\s*"(\d{4}-\d{2}-\d{2}T(\d{2}))') { $hour = $Matches[2] + "UTC" }
        if (-not $levelDepth.ContainsKey($depth)) { $levelDepth[$depth] = @{count=0; pnl=0.0} }
        $levelDepth[$depth].count++
        $levelDepth[$depth].pnl += $pnl
        if (-not $hourBuckets.ContainsKey($hour)) { $hourBuckets[$hour] = @{count=0; pnl=0.0} }
        $hourBuckets[$hour].count++
        $hourBuckets[$hour].pnl += $pnl
        if (-not $directionBuckets.ContainsKey($dir)) { $directionBuckets[$dir] = @{count=0; pnl=0.0} }
        $directionBuckets[$dir].count++
        $directionBuckets[$dir].pnl += $pnl
        $bucket = if ($bars -le 3) {"0-3"} elseif ($bars -le 10) {"4-10"} elseif ($bars -le 30) {"11-30"} elseif ($bars -le 100) {"31-100"} else {"100+"}
        if (-not $holdBars.ContainsKey($bucket)) { $holdBars[$bucket] = @{count=0; pnl=0.0} }
        $holdBars[$bucket].count++
        $holdBars[$bucket].pnl += $pnl
    }
}
$reader.Close()

Write-Output ("=== LATTICE AUTOPSY ===")
Write-Output ("File: " + [System.IO.Path]::GetFileName($args[0]))
Write-Output ("Total events: $totalLines, Closes: $closes")
Write-Output ("")

Write-Output ("--- Per-Level Reversal Analysis ---")
$levelDepth.GetEnumerator() | Sort-Object { [int]$_.Name } | ForEach-Object {
    $avg = $_.Value.pnl / $_.Value.count
    Write-Output ("  Level " + $_.Name.PadLeft(3) + ": closes=" + $_.Value.count.ToString().PadLeft(5) + " net=$" + [math]::Round($_.Value.pnl,2).ToString().PadLeft(10) + " avg=$" + [math]::Round($avg,2).ToString().PadLeft(8))
}

Write-Output ("")
Write-Output ("--- By Direction ---")
$directionBuckets.GetEnumerator() | Sort-Object { $_.Name } | ForEach-Object {
    $avg = $_.Value.pnl / $_.Value.count
    Write-Output ("  " + $_.Name.PadRight(6) + ": closes=" + $_.Value.count.ToString().PadLeft(5) + " net=$" + [math]::Round($_.Value.pnl,2).ToString().PadLeft(10) + " avg=$" + [math]::Round($avg,2).ToString().PadLeft(8))
}

Write-Output ("")
Write-Output ("--- Hold Bars Distribution ---")
$order = @("0-3","4-10","11-30","31-100","100+")
foreach ($b in $order) {
    if ($holdBars.ContainsKey($b)) {
        $avg = $holdBars[$b].pnl / $holdBars[$b].count
        Write-Output ("  " + $b.PadRight(7) + ": closes=" + $holdBars[$b].count.ToString().PadLeft(5) + " net=$" + [math]::Round($holdBars[$b].pnl,2).ToString().PadLeft(10) + " avg=$" + [math]::Round($avg,2).ToString().PadLeft(8))
    }
}

Write-Output ("")
Write-Output ("--- Hour-of-Day Distribution ---")
$hourBuckets.GetEnumerator() | Sort-Object { $_.Name } | ForEach-Object {
    $avg = $_.Value.pnl / $_.Value.count
    Write-Output ("  " + $_.Name.PadRight(7) + ": closes=" + $_.Value.count.ToString().PadLeft(5) + " net=$" + [math]::Round($_.Value.pnl,2).ToString().PadLeft(10) + " avg=$" + [math]::Round($avg,2).ToString().PadLeft(8))
}
