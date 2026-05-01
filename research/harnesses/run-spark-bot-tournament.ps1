param(
    [ValidateSet("alpaca", "oanda", "xhigh", "all")]
    [string]$Broker = "all",

    [int]$DurationSeconds = 300,

    [int]$PollSeconds = 30,

    [int]$MaxParallel = 1
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) '..\..')).Path
$manifestPath = Join-Path $root "spark-bot-manifest.json"
$harnessPath = Join-Path $root "scripts/benchmarks/benchmark_trading_bot.py"
$dotEnvPath = Join-Path $root ".env"

if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw "Manifest not found: $manifestPath"
}

if (-not (Test-Path -LiteralPath $harnessPath)) {
    throw "Harness not found: $harnessPath"
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json

function Resolve-EnvironmentValue {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $Value
    }

    $resolved = $Value
    $resolved = [regex]::Replace(
        $resolved,
        '\\$\{([A-Za-z_][A-Za-z0-9_]*)\}',
        {
            param($match)
            $name = $match.Groups[1].Value
            $envValue = Get-EnvValue -Name $name
            if ($null -eq $envValue) {
                Write-Warning "Manifest token $name is not set; using empty string for this value."
                return ""
            }
            return $envValue
        }
    )

    $resolved = [regex]::Replace(
        $resolved,
        '(?<!\$)\$(?!\{)([A-Za-z_][A-Za-z0-9_]*)',
        {
            param($match)
            $name = $match.Groups[1].Value
            $envValue = Get-EnvValue -Name $name
            if ($null -eq $envValue) {
                Write-Warning "Manifest token $name is not set; using empty string for this value."
                return ""
            }
            return $envValue
        }
    )

    return $resolved
}

function Get-DotEnvDictionary {
    param([string]$Path)

    $values = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $values
    }

    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) {
            return
        }

        $separator = $line.IndexOf("=")
        if ($separator -lt 1) {
            return
        }

        $key = $line.Substring(0, $separator).Trim()
        $value = $line.Substring($separator + 1).Trim()
        if ($key) {
            $values[$key] = $value
        }
    }

    return $values
}

$dotEnvValues = Get-DotEnvDictionary -Path $dotEnvPath

function Get-EnvValue {
    param([string]$Name)

    if ([string]::IsNullOrWhiteSpace($Name)) {
        return $null
    }

    $envValue = [Environment]::GetEnvironmentVariable($Name)
    if ($null -ne $envValue) {
        return $envValue
    }

    if ($dotEnvValues.ContainsKey($Name)) {
        return $dotEnvValues[$Name]
    }

    return $null
}

function Normalize-BotSpec {
    param([Parameter(Mandatory = $true)]$BotSpec)

    if ($BotSpec -is [string]) {
        return @{
            bot  = $BotSpec
            label = $BotSpec
            env = @{}
        }
    }

    if (-not $BotSpec.PSObject.Properties.Name.Contains("bot")) {
        throw "Invalid manifest entry: expected bot string or object with bot field"
    }

    $env = @{}
    if ($BotSpec.PSObject.Properties.Name.Contains("env")) {
        foreach ($item in $BotSpec.env.PSObject.Properties) {
            if ($null -eq $item.Value) {
                $env[$item.Name] = ""
                continue
            }
            if ($item.Value -is [string]) {
                $env[$item.Name] = Resolve-EnvironmentValue -Value $item.Value
            } else {
                $env[$item.Name] = [string]$item.Value
            }
        }
    }

    return @{
        bot = [string]$BotSpec.bot
        label = if ($BotSpec.PSObject.Properties.Name.Contains("label")) { [string]$BotSpec.label } else { [string]$BotSpec.bot }
        env = $env
    }
}

function Get-BotSpecList {
    param([string]$BrokerName)

    $rawList = $manifest.$BrokerName
    if (-not $rawList) {
        return @()
    }

    $output = @()
    foreach ($entry in $rawList) {
        $output += ,(Normalize-BotSpec -BotSpec $entry)
    }
    return $output
}

function New-BaseEnvironment {
    $envVars = @{}
    Get-ChildItem Env: | ForEach-Object { $envVars[$_.Name] = $_.Value }
    foreach ($entry in $dotEnvValues.GetEnumerator()) {
        if ($entry.Key -and -not [string]::IsNullOrWhiteSpace($entry.Key) -and -not $envVars.ContainsKey($entry.Key)) {
            $envVars[$entry.Key] = $entry.Value
        }
    }
    return $envVars
}

function Remove-InvalidAlpacaProfile {
    param(
        [hashtable]$Environment,
        [string]$BrokerName
    )

    if ($BrokerName -ne "alpaca" -and $BrokerName -ne "xhigh") {
        return
    }

    if (-not $Environment.ContainsKey("ALPACA_PROFILE")) {
        return
    }

    $profile = $Environment["ALPACA_PROFILE"]
    if ([string]::IsNullOrWhiteSpace($profile)) {
        $Environment.Remove("ALPACA_PROFILE")
        return
    }

    $normalized = [regex]::Replace($profile, "[^A-Za-z0-9_]", "_").Trim("_")
    $candidateProfiles = @(
        $profile,
        $normalized,
        $normalized.ToUpperInvariant(),
        $normalized.ToLowerInvariant(),
        $normalized.Trim("_"),
        $normalized.Trim("_").ToUpperInvariant(),
        $normalized.Trim("_").ToLowerInvariant()
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique

    $hasProfileCredentials = $false
    foreach ($candidate in $candidateProfiles) {
        $apiKey = "ALPACA_API_KEY_$candidate"
        $secretKey = "ALPACA_SECRET_KEY_$candidate"
        if ($Environment.ContainsKey($apiKey) -and $Environment.ContainsKey($secretKey)) {
            if (-not [string]::IsNullOrWhiteSpace($Environment[$apiKey]) -and -not [string]::IsNullOrWhiteSpace($Environment[$secretKey])) {
                $hasProfileCredentials = $true
                break
            }
        }
    }

    $hasBaseCredentials = `
        $Environment.ContainsKey("ALPACA_API_KEY") -and
        $Environment.ContainsKey("ALPACA_SECRET_KEY") -and
        -not [string]::IsNullOrWhiteSpace($Environment["ALPACA_API_KEY"]) -and
        -not [string]::IsNullOrWhiteSpace($Environment["ALPACA_SECRET_KEY"])

    if (-not $hasProfileCredentials -and -not $hasBaseCredentials) {
        Write-Warning "Removing ALPACA_PROFILE '$profile' because no matching account-suffixed credentials were found and no base ALPACA_API_KEY/ALPACA_SECRET_KEY are present."
        $Environment.Remove("ALPACA_PROFILE")
    }
}

function Start-BenchmarkProcess {
    param(
        [string]$BrokerArg,
        [string]$BrokerName,
        [string]$BotPath,
        [hashtable]$EnvOverrides,
        [string]$Label,
        [string]$RunId
    )

    $arguments = @(
        $harnessPath,
        "--broker", $BrokerArg,
        "--bot", $BotPath,
        "--duration", $DurationSeconds,
        "--poll", $PollSeconds,
        "--run-id", $RunId
    )

    Write-Host ""
    Write-Host "=== Starting $BrokerName :: $Label ===" -ForegroundColor Cyan

    $environment = New-BaseEnvironment
    foreach ($entry in $EnvOverrides.GetEnumerator()) {
        if ([string]::IsNullOrWhiteSpace($entry.Value)) {
            continue
        }
        $environment[$entry.Key] = [string]$entry.Value
    }

    Remove-InvalidAlpacaProfile -Environment $environment -BrokerName $BrokerName

    if ($environment.ContainsKey("ALPACA_PROFILE") -and -not [string]::IsNullOrWhiteSpace($environment["ALPACA_PROFILE"])) {
        $arguments += @("--alpaca-profile", $environment["ALPACA_PROFILE"])
    }

    function Convert-ProcessArgument {
        param([Parameter(Mandatory=$true)][string]$Value)

        if ($Value -match '[\s"]') {
            return '"' + ($Value -replace '"', '""') + '"'
        }
        return $Value
    }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = (Get-Command python).Path
    $psi.Arguments = [string]::Join(" ", ($arguments | ForEach-Object { Convert-ProcessArgument $_ }))
    $psi.WorkingDirectory = $root
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    foreach ($pair in $environment.GetEnumerator()) {
        if ($pair.Key -and $null -ne $pair.Value) {
            $psi.EnvironmentVariables[$pair.Key] = [string]$pair.Value
        }
    }

    return [System.Diagnostics.Process]::Start($psi)
}

function Invoke-BenchmarkBatch {
    param(
        [string]$BrokerName,
        [object[]]$BotSpecs
    )

    if (-not $BotSpecs -or $BotSpecs.Count -eq 0) {
        Write-Host "No bots configured for $BrokerName." -ForegroundColor Yellow
        return
    }

    $jobs = New-Object System.Collections.Generic.List[object]
    $index = 0

    while ($index -lt $BotSpecs.Count -or $jobs.Count -gt 0) {
        while ($index -lt $BotSpecs.Count -and $jobs.Count -lt $MaxParallel) {
            $spec = $BotSpecs[$index]
            $index++

            $botPath = Join-Path $root $spec.bot
            if (-not (Test-Path -LiteralPath $botPath)) {
                Write-Warning "Bot not found, skipping: $($spec.bot)"
                continue
            }

            $brokerArg = if ($BrokerName -eq "xhigh") { "alpaca" } else { $BrokerName }
            $safeLabel = ($spec.label -replace '[^A-Za-z0-9_\\-]', '-')
            $runId = '{0:yyyyMMdd-HHmmssfff}-{1}' -f (Get-Date), $safeLabel

            $proc = Start-BenchmarkProcess -BrokerArg $brokerArg -BrokerName $BrokerName -BotPath $botPath -EnvOverrides $spec.env -Label $spec.label -RunId $runId
            $jobs.Add([pscustomobject]@{
                    Bot    = $spec.label
                    Broker = $BrokerName
                    Proc   = $proc
                    RunId  = $runId
                })
        }

        for ($i = $jobs.Count - 1; $i -ge 0; $i--) {
            $job = $jobs[$i]
            if ($job.Proc.HasExited) {
                Write-Host "Completed $($job.Broker) :: $($job.Bot) (ExitCode=$($job.Proc.ExitCode))" -ForegroundColor Green
                $jobs.RemoveAt($i)
            }
        }

        if ($jobs.Count -gt 0 -and $index -lt $BotSpecs.Count) {
            Start-Sleep -Seconds 1
        }
        elseif ($jobs.Count -gt 0) {
            Start-Sleep -Milliseconds 500
        }
    }
}

if ($Broker -in @("alpaca", "all")) {
    $botSpecs = Get-BotSpecList -BrokerName "alpaca"
    Invoke-BenchmarkBatch -BrokerName "alpaca" -BotSpecs $botSpecs
}

if ($Broker -in @("xhigh", "all")) {
    $botSpecs = Get-BotSpecList -BrokerName "xhigh"
    Invoke-BenchmarkBatch -BrokerName "xhigh" -BotSpecs $botSpecs
}

if ($Broker -in @("oanda", "all")) {
    $botSpecs = Get-BotSpecList -BrokerName "oanda"
    Invoke-BenchmarkBatch -BrokerName "oanda" -BotSpecs $botSpecs
}
