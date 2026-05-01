$reader = [System.IO.StreamReader]::new($args[0])
$closes = 0; $totalLines = 0
$levelData = @{}; $dirData = @{}; $hourData = @{}
$symbolData = @{}; $holdBars = @{}; $gapData = @{}; $sameBarCount = 0

while (($line = $reader.ReadLine()) -ne $null) {
    $totalLines++
    if ($line -match '"action":\s*"close_ticket"') {
        $closes++
        $depth = "0"; $dir = "?"; $sym = "?"; $pnl = 0.0; $bars = 0; $gap = "?"; $hour = "?"
        if ($line -match '"level_idx":\s*(\d+)') { $depth = $Matches[1] }
        if ($line -match '"direction":\s*"(\w+)"') { $dir = $Matches[1] }
        if ($line -match '"symbol":\s*"(\w+)"') { $sym = $Matches[1] }
        if ($line -match '"realized_pnl":\s*([0-9.e-]+)') { $pnl = [double]$Matches[1] }
        if ($line -match '"hold_bars":\s*(\d+)') { $bars = [int]$Matches[1] }
        if ($line -match '"close_gap":\s*(\d+)') { $gap = $Matches[1] }
        if ($line -match '"ts_utc":\s*"(\d{4}-\d{2}-\d{2}T(\d{2}))') { $hour = $Matches[2] }
        if ($line -match '"same_bar_exit":\s*true') { $sameBarCount++ }

        if (-not $levelData.ContainsKey($depth)) { $levelData[$depth] = @{count=0; pnl=0.0} }
        $levelData[$depth].count++; $levelData[$depth].pnl += $pnl

        $key = $sym + "_" + $dir
        if (-not $dirData.ContainsKey($key)) { $dirData[$key] = @{count=0; pnl=0.0} }
        $dirData[$key].count++; $dirData[$key].pnl += $pnl

        if (-not $hourData.ContainsKey($hour)) { $hourData[$hour] = @{count=0; pnl=0.0} }
        $hourData[$hour].count++; $hourData[$hour].pnl += $pnl

        if (-not $symbolData.ContainsKey($sym)) { $symbolData[$sym] = @{count=0; pnl=0.0} }
        $symbolData[$sym].count++; $symbolData[$sym].pnl += $pnl

        $bkt = if ($bars -le 3) {"0-3"} elseif ($bars -le 10) {"4-10"} else {"10+"}
        if (-not $holdBars.ContainsKey($bkt)) { $holdBars[$bkt] = @{count=0; pnl=0.0} }
        $holdBars[$bkt].count++; $holdBars[$bkt].pnl += $pnl

        if (-not $gapData.ContainsKey($gap)) { $gapData[$gap] = @{count=0; pnl=0.0} }
        $gapData[$gap].count++; $gapData[$gap].pnl += $pnl
    }
}
$reader.Close()

Write-Output "=== FX CLOSE POLICY MIXED AUTOPSY ==="
Write-Output "Events: $totalLines, Closes: $closes, SameBarExits: $sameBarCount"
Write-Output ""
Write-Output "--- By Symbol+Direction ---"
$dirData.GetEnumerator() | Sort-Object { $_.Name } | ForEach-Object {
    $avg = $_.Value.pnl / $_.Value.count
    $name = $_.Name; $cnt = $_.Value.count; $net = [math]::Round($_.Value.pnl, 2)
    Write-Output "  $name : closes=$cnt net=`$$net avg=`$([math]::Round($avg,2))"
}
Write-Output ""
Write-Output "--- By Symbol ---"
$symbolData.GetEnumerator() | Sort-Object { $_.Name } | ForEach-Object {
    $avg = $_.Value.pnl / $_.Value.count
    $name = $_.Name; $cnt = $_.Value.count; $net = [math]::Round($_.Value.pnl, 2)
    Write-Output "  $name : closes=$cnt net=`$$net avg=`$([math]::Round($avg,2))"
}
Write-Output ""
Write-Output "--- Hold Bars ---"
"0-3","4-10","10+" | ForEach-Object {
    if ($holdBars.ContainsKey($_)) {
        $v = $holdBars[$_]; $avg = $v.pnl / $v.count
        Write-Output "  $_ : closes=$($v.count) net=`$([math]::Round($v.pnl,2)) avg=`$([math]::Round($avg,2))"
    }
}
Write-Output ""
Write-Output "--- By Close Gap ---"
$gapData.GetEnumerator() | Sort-Object { $_.Name } | ForEach-Object {
    $avg = $_.Value.pnl / $_.Value.count
    $name = $_.Name; $cnt = $_.Value.count; $net = [math]::Round($_.Value.pnl, 2)
    Write-Output "  gap=$name : closes=$cnt net=`$$net avg=`$([math]::Round($avg,2))"
}
Write-Output ""
Write-Output "--- By Hour ---"
$hourData.GetEnumerator() | Sort-Object { $_.Name } | ForEach-Object {
    $avg = $_.Value.pnl / $_.Value.count
    $name = $_.Name; $cnt = $_.Value.count; $net = [math]::Round($_.Value.pnl, 2)
    Write-Output "  ${name}UTC : closes=$cnt net=`$$net avg=`$([math]::Round($avg,2))"
}
