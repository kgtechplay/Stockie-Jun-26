param(
    [string]$TokenRedirectUrl = "",
    [int]$Lookback = 1,
    [switch]$SkipStockUniverse,
    [switch]$SkipOptionInstruments
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$env:PYTHONDONTWRITEBYTECODE = "1"

function Run-Step {
    param(
        [string]$Name,
        [string[]]$Args
    )

    Write-Host ""
    Write-Host "=== $Name ==="
    & python @Args
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

if ($TokenRedirectUrl) {
    Run-Step "Kite access token refresh" @("scripts/daily_get_kite_access_token.py", $TokenRedirectUrl)
} else {
    Write-Host "Skipping Kite token refresh. Run scripts/daily_get_kite_access_token.py manually first if today's token is not already saved."
}

if (-not $SkipStockUniverse) {
    Run-Step "Stock universe refresh" @("scripts/daily_fetch_stocks_universe.py")
}

if (-not $SkipOptionInstruments) {
    Run-Step "Option instrument refresh" @("scripts/daily_optionInstrument_refresh.py")
}

Run-Step "Daily market refresh" @("scripts/daily_market_refresh.py", "--lookback", "$Lookback")

Write-Host ""
Write-Host "Daily watched-instrument refresh complete."
