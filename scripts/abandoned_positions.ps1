# Abandoned Positions Autopsy — finds positions that opened but never closed
param([string]$EventFile)

$reader = [System.IO.StreamReader]::new($EventFile)
$openTickets = @{}
$closeTickets = @{}
$levelOpens = @{}
$levelCloses = @{}
$directionOpens = @{}
$directionCloses = @{}
$totalOpens = 0; $totalCloses = 0; $totalLines = 0
$openPnls = @(); $closePnls = @{}

while (($line = $reader.ReadLine()) -ne $null) {
    $totalLines++

    if ($line -match '"action":\s*"open_ticket"') {
        $ticket = "unknown"
        if ($line -match '"live_ticket":\s*(\d+)') { $ticket = $Matches[1] }
        $level = "0"
        if ($line -match '"level_idx":\s*(\d+)') { $level = $Matches[1] }
        $dir = "unknown"
        if ($line -match '"direction":\s*"(\w+)"') { $dir = $Matches[1] }
        $price = 0.0
        if ($line -match '"fill_price":\s*([0-9.e-]+)') { $price = [double]$Matches[1] }

        $totalOpens++
        $openTickets[$ticket] = @{level=$level; dir=$dir; price=$price; line=$line}

        if (-not $levelOpens.ContainsKey($level)) { $levelOpens[$level] = 0 }
        $levelOpens[$level]++

        if (-not $directionOpens.ContainsKey($dir)) { $directionOpens[$dir] = 0 }
        $directionOpens[$dir]++
    }

    if ($line -match '"action":\s*"close_ticket"') {
        $ticket = "unknown"
        if ($line -match '"ticket":\s*(\d+)') { $ticket = $Matches[1] }
        $pnl = 0.0
        if ($line -match '"realized_pnl":\s*([0-9.e-]+)') { $pnl = [double]$Matches[1] }
        $dir = "unknown"
        if ($line -match '"direction":\s*"(\w+)"') { $dir = $Matches[1] }

        $totalCloses++
        $closeTickets[$ticket] = $pnl

        $level = "0"
        if ($line -match '"level_idx":\s*(\d+)') { $level = $Matches[1] }
        if (-not $levelCloses.ContainsKey($level)) { $levelCloses[$level] = 0 }
        $levelCloses[$level]++

        if (-not $directionCloses.ContainsKey($dir)) { $directionCloses[$dir] = 0 }
        $directionCloses[$dir]++
    }

    if ($line -match '"action":\s*"anchor_reset"') {
        # Count resets
    }
}
$reader.Close()

# Find abandoned positions
$abandoned = @{}
$abandonedPnls = @()
$abandonedByLevel = @{}
$abandonedByDir = @{}
$abandonedCount = 0

foreach ($kv in $openTickets.GetEnumerator()) {
    $ticket = $kv.Key
    if (-not $closeTickets.ContainsKey($ticket)) {
        $abandonedCount++
        $lvl = $kv.Value.level
        $dir = $kv.Value.dir
        $price = $kv.Value.price

        if (-not $abandonedByLevel.ContainsKey($lvl)) { $abandonedByLevel[$lvl] = 0 }
        $abandonedByLevel[$lvl]++

        if (-not $abandonedByDir.ContainsKey($dir)) { $abandonedByDir[$dir] = 0 }
        $abandonedByDir[$dir]++
    }
}

Write-Output "=== ABANDONED POSITIONS AUTOPSY ==="
Write-Output "File: $([System.IO.Path]::GetFileName($EventFile))"
Write-Output "Events: $totalLines"
Write-Output "Opens: $totalOpens, Closes: $totalCloses"
Write-Output "Abandoned: $abandonedCount ($([math]::Round($abandonedCount / $totalOpens * 100, 1))%)"
Write-Output ""

Write-Output "--- Abandoned by Level ---"
$abandonedByLevel.GetEnumerator() | Sort-Object { [int]$_.Name } | ForEach-Object {
    $total = if ($levelOpens.ContainsKey($_.Name)) { $levelOpens[$_.Name] } else { 0 }
    $abandoned = $_.Value
    $pct = if ($total -gt 0) { [math]::Round($abandoned / $total * 100, 1) } else { 0 }
    Write-Output "  Level $($_.Name.PadLeft(3)): $abandoned abandoned of $total opens ($pct%)"
}
Write-Output ""

Write-Output "--- Abandoned by Direction ---"
$abandonedByDir.GetEnumerator() | Sort-Object { $_.Name } | ForEach-Object {
    $total = if ($directionOpens.ContainsKey($_.Name)) { $directionOpens[$_.Name] } else { 0 }
    $abandoned = $_.Value
    $pct = if ($total -gt 0) { [math]::Round($abandoned / $total * 100, 1) } else { 0 }
    Write-Output "  $($_.Name.PadRight(6)): $abandoned abandoned of $total opens ($pct%)"
}
Write-Output ""

# Close rates by level
Write-Output "--- Close Rate by Level ---"
$allLevels = @()
foreach ($k in $levelOpens.Keys) { if ($allLevels -notcontains $k) { $allLevels += $k } }
foreach ($k in $levelCloses.Keys) { if ($allLevels -notcontains $k) { $allLevels += $k } }
$allLevels | Sort-Object { [int]$_ } | ForEach-Object {
    $opens = if ($levelOpens.ContainsKey($_)) { $levelOpens[$_] } else { 0 }
    $closes = if ($levelCloses.ContainsKey($_)) { $levelCloses[$_] } else { 0 }
    $rate = if ($opens -gt 0) { [math]::Round($closes / $opens * 100, 1) } else { 0 }
    $abandoned = $opens - $closes
    Write-Output "  Level $($_.PadLeft(3)): $opens opens, $closes closes, $abandoned abandoned ($rate% close rate)"
}
