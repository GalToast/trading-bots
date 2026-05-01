$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    throw "Administrator privileges are required to configure WER LocalDumps under HKLM. Re-run this script from an elevated PowerShell session."
}

$SupervisorDir = Join-Path $env:LOCALAPPDATA "TradingBotsSupervisor"
$DumpDir = Join-Path $SupervisorDir "CrashDumps"
$StatePath = Join-Path $SupervisorDir "python_crash_dump_config.json"
$WerBaseKey = "HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps"
$PythonKey = Join-Path $WerBaseKey "python.exe"

New-Item -ItemType Directory -Force -Path $DumpDir | Out-Null
New-Item -Path $WerBaseKey -Force | Out-Null
New-Item -Path $PythonKey -Force | Out-Null

New-ItemProperty -Path $PythonKey -Name DumpFolder -PropertyType ExpandString -Value $DumpDir -Force | Out-Null
New-ItemProperty -Path $PythonKey -Name DumpCount -PropertyType DWord -Value 10 -Force | Out-Null
New-ItemProperty -Path $PythonKey -Name DumpType -PropertyType DWord -Value 2 -Force | Out-Null

try {
    Restart-Service -Name WerSvc -ErrorAction Stop
}
catch {
    Write-Warning ("WerSvc restart skipped: " + $_.Exception.Message)
}

$state = @{
    configured_at_utc = [DateTime]::UtcNow.ToString("o")
    dump_dir = $DumpDir
    registry_key = $PythonKey
    dump_count = 10
    dump_type = 2
    process_name = "python.exe"
}

Set-Content -Path $StatePath -Value ($state | ConvertTo-Json -Depth 6) -Encoding UTF8

Write-Output "Configured WER LocalDumps for python.exe"
Write-Output ("Dump directory: " + $DumpDir)
Write-Output ("Registry key: " + $PythonKey)
Get-ItemProperty -Path $PythonKey | Select-Object DumpFolder, DumpCount, DumpType | Format-List
