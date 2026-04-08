param(
    [string]$ApiKey = "",
    [string]$BaseUrl = "https://airouter.service.itstudio.club/v1",
    [string]$Model = "gpt-5.4",
    [int]$Port = 8787
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$starter = Join-Path $scriptDir "start_gateway.ps1"

if (-not (Test-Path $starter)) {
    Write-Error "Starter script not found: $starter"
    exit 1
}

& $starter `
    -ApiKey $ApiKey `
    -BaseUrl $BaseUrl `
    -Model $Model `
    -ReasoningEffort "xhigh" `
    -Port $Port `
    -StreamPingInterval 5 `
    -StreamIdleTimeout 300 `
    -Debug "1" `
    -DebugPet "auto"
