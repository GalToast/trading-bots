Set-StrictMode -Version Latest

function ConvertTo-VbsStringLiteral([string]$Value) {
    return $Value.Replace('"', '""')
}

function Format-HiddenTaskArgument([string]$Value) {
    if ($Value -match '[\s"]') {
        return '"' + $Value.Replace('"', '""') + '"'
    }
    return $Value
}

function New-HiddenPowerShellTaskAction {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LauncherPath,

        [Parameter(Mandatory = $true)]
        [string]$PowerShellExe,

        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,

        [string[]]$ScriptArguments = @()
    )

    $wscriptExe = Join-Path $env:SystemRoot "System32\wscript.exe"
    if (-not (Test-Path $wscriptExe)) {
        throw "Missing Windows Script Host executable: $wscriptExe"
    }
    if (-not (Test-Path $PowerShellExe)) {
        throw "Missing PowerShell executable: $PowerShellExe"
    }
    if (-not (Test-Path $ScriptPath)) {
        throw "Missing scheduled script target: $ScriptPath"
    }

    $commandSegments = @(
        ('"{0}"' -f $PowerShellExe),
        '-NoProfile',
        '-ExecutionPolicy',
        'Bypass',
        '-File',
        ('"{0}"' -f $ScriptPath)
    )
    foreach ($arg in $ScriptArguments) {
        $commandSegments += Format-HiddenTaskArgument -Value $arg
    }
    $commandText = $commandSegments -join ' '
    $launcherDir = Split-Path $LauncherPath -Parent
    if ($launcherDir) {
        New-Item -ItemType Directory -Force -Path $launcherDir | Out-Null
    }

    $launcherLines = @(
        'Dim shell',
        'Set shell = CreateObject("WScript.Shell")',
        ('shell.Run "{0}", 0, False' -f (ConvertTo-VbsStringLiteral -Value $commandText))
    )
    Set-Content -Path $LauncherPath -Value ($launcherLines -join "`r`n") -Encoding ASCII

    return New-ScheduledTaskAction -Execute $wscriptExe -Argument ('"{0}"' -f $LauncherPath)
}
