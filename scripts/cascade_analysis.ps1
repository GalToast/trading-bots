$reader = [System.IO.StreamReader]::new($args[0])
$closes = @()
while (($line = $reader.ReadLine()) -ne $null) {
    if ($line -match '"action":\s*"close_ticket"') {
        $pnl = 0.0; $entry = 0.0; $exit = 0.0; $dir = "?"; $ts = ""
        if ($line -match '"realized_pnl":\s*([0-9.e-]+)') { $pnl = [double]$Matches[1] }
        if ($line -match '"entry_price":\s*([0-9.e-]+)') { $entry = [double]$Matches[1] }
        if ($line -match '"exit_price":\s*([0-9.e-]+)') { $exit = [double]$Matches[1] }
        if ($line -match '"direction":\s*"(\w+)"') { $dir = $Matches[1] }
        if ($line -match '"ts_utc":\s*"([^"]+)"') { $ts = $Matches[1] }
        $closes += @{pnl=$pnl; entry=$entry; exit=$exit; dir=$dir; ts=$ts}
    }
}
$reader.Close()

Write-Output "Total closes: $($closes.Count)"
Write-Output ""
Write-Output "--- Cascade Analysis (closes within 60 sec) ---"

$cascadeCount = 0; $cascadeTotal = 0; $singleCount = 0
$i = 0
while ($i -lt $closes.Count) {
    $current = $closes[$i]
    $j = $i + 1
    $group = 1
    while ($j -lt $closes.Count) {
        $t1 = [DateTime]::Parse($current.ts)
        $t2 = [DateTime]::Parse($closes[$j].ts)
        if (($t2 - $t1).TotalSeconds -le 60) {
            $group++
            $j++
        } else { break }
    }
    if ($group -gt 1) {
        $cascadeCount++
        $cascadeTotal += $group
        $tsShort = $current.ts.Substring(0, 19)
        Write-Output "  CASCADE of $group closes at $tsShort"
        $pnlSum = 0.0; $minPnl = 999999.0; $maxPnl = -999999.0
        for ($k = $i; $k -lt $j; $k++) {
            $p = $closes[$k].pnl
            $pnlSum += $p
            if ($p -lt $minPnl) { $minPnl = $p }
            if ($p -gt $maxPnl) { $maxPnl = $p }
        }
        Write-Output "    PnL range: `$$([math]::Round($minPnl,2)) to `$$([math]::Round($maxPnl,2)) total=`$$([math]::Round($pnlSum,2))"
    } else {
        $singleCount++
    }
    $i = $j
}

Write-Output ""
Write-Output "Cascades: $cascadeCount ($cascadeTotal closes)"
Write-Output "Singles: $singleCount"
if ($closes.Count -gt 0) {
    Write-Output "Close rate in cascades: $([math]::Round($cascadeTotal / $closes.Count * 100, 1))%"
}
