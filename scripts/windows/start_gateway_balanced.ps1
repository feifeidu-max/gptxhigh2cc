param(
    [string]$ApiKey = "",
    [string]$BaseUrl = "",
    [string]$Model = "gpt-5.4",
    [int]$Port = 8787
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$starter = Join-Path $scriptDir "start_gateway.ps1"

if (-not (Test-Path $starter)) {
    Write-Error "Starter script not found: $starter"
    exit 1
}

$starterArgs = @{
    Model              = $Model
    ReasoningEffort    = "high"
    Port               = $Port
    StreamPingInterval = 4
    StreamIdleTimeout  = 300
    Debug              = "1"
    DebugPet           = "auto"
}

if (-not [string]::IsNullOrWhiteSpace($ApiKey)) {
    $starterArgs.ApiKey = $ApiKey
}

if (-not [string]::IsNullOrWhiteSpace($BaseUrl)) {
    $starterArgs.BaseUrl = $BaseUrl
}

& $starter @starterArgs
