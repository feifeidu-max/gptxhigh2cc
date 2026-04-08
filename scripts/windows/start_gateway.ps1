param(
    [string]$ApiKey = "",
    [string]$BaseUrl = "https://airouter.service.itstudio.club/v1",
    [string]$Model = "gpt-5.4",
    [string]$ReasoningEffort = "xhigh",
    [int]$Port = 8787,
    [int]$StreamPingInterval = 5,
    [int]$StreamIdleTimeout = 300,
    [string]$Debug = "1",
    [string]$DebugPet = "auto"
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptDir "..\.."))
$gatewayScript = Join-Path $repoRoot "src\cc2open_gateway.py"

if (-not (Test-Path $gatewayScript)) {
    Write-Error "Gateway script not found: $gatewayScript"
    exit 1
}

if ([string]::IsNullOrWhiteSpace($ApiKey)) {
    if (-not [string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
        $ApiKey = $env:OPENAI_API_KEY
    } else {
        $ApiKey = Read-Host "Enter OPENAI_API_KEY"
    }
}

if ([string]::IsNullOrWhiteSpace($ApiKey)) {
    Write-Error "OPENAI_API_KEY is required."
    exit 1
}

$env:OPENAI_API_KEY = $ApiKey
$env:OPENAI_BASE_URL = $BaseUrl
$env:OPENAI_MODEL = $Model
$env:OPENAI_REASONING_EFFORT = $ReasoningEffort
$env:CC2OPEN_PORT = "$Port"
$env:CC2OPEN_STREAM_PING_INTERVAL = "$StreamPingInterval"
$env:CC2OPEN_STREAM_IDLE_TIMEOUT = "$StreamIdleTimeout"
$env:CC2OPEN_DEBUG = "$Debug"
$env:CC2OPEN_DEBUG_PET = "$DebugPet"

Write-Host "Starting cc2open gateway..." -ForegroundColor Cyan
Write-Host "Base URL: $BaseUrl"
Write-Host "Model: $Model"
Write-Host "Reasoning effort: $ReasoningEffort"
Write-Host "Port: $Port"
Write-Host "Stream ping interval: $StreamPingInterval s"
Write-Host "Stream idle timeout: $StreamIdleTimeout s"
Write-Host "Debug: $Debug"
Write-Host "Debug pet: $DebugPet"

python -S $gatewayScript
