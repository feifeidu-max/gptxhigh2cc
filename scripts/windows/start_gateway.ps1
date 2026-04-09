param(
    [string]$ApiKey = "",
    [string]$BaseUrl = "",
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
$statePath = Join-Path $scriptDir ".cc2open_state.json"
$defaultBaseUrl = "https://airouter.service.itstudio.club/v1"

if (-not (Test-Path $gatewayScript)) {
    Write-Error "Gateway script not found: $gatewayScript"
    exit 1
}

function Get-PersistedSettings {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return @{}
    }

    try {
        $raw = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop
        if ([string]::IsNullOrWhiteSpace($raw)) {
            return @{}
        }

        $data = $raw | ConvertFrom-Json -ErrorAction Stop
        $settings = @{}

        if ($null -ne $data.openai_api_key) {
            $settings.ApiKey = [string]$data.openai_api_key
        }

        if ($null -ne $data.openai_base_url) {
            $settings.BaseUrl = ([string]$data.openai_base_url).Trim().TrimEnd("/")
        }

        return $settings
    } catch {
        Write-Warning "Failed to load persisted gateway settings from ${Path}: $($_.Exception.Message)"
        return @{}
    }
}

function Save-PersistedSettings {
    param(
        [string]$Path,
        [string]$SavedApiKey,
        [string]$SavedBaseUrl
    )

    $directory = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    $payload = [ordered]@{
        openai_api_key  = $SavedApiKey
        openai_base_url = $SavedBaseUrl
        updated_at      = (Get-Date).ToString("o")
    }

    $payload |
        ConvertTo-Json -Depth 4 |
        Set-Content -LiteralPath $Path -Encoding UTF8
}

$persisted = Get-PersistedSettings -Path $statePath

if ([string]::IsNullOrWhiteSpace($ApiKey)) {
    if (-not [string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
        $ApiKey = $env:OPENAI_API_KEY
    } elseif ($persisted.ContainsKey("ApiKey")) {
        $ApiKey = $persisted.ApiKey
    } else {
        $ApiKey = Read-Host "Enter OPENAI_API_KEY"
    }
}

if ([string]::IsNullOrWhiteSpace($ApiKey)) {
    Write-Error "OPENAI_API_KEY is required."
    exit 1
}

$ApiKey = $ApiKey.Trim()

if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
    if (-not [string]::IsNullOrWhiteSpace($env:OPENAI_BASE_URL)) {
        $BaseUrl = $env:OPENAI_BASE_URL
    } elseif ($persisted.ContainsKey("BaseUrl")) {
        $BaseUrl = $persisted.BaseUrl
    } else {
        $BaseUrl = $defaultBaseUrl
    }
}

$BaseUrl = $BaseUrl.Trim().TrimEnd("/")
Save-PersistedSettings -Path $statePath -SavedApiKey $ApiKey -SavedBaseUrl $BaseUrl

$env:OPENAI_API_KEY = $ApiKey
$env:OPENAI_BASE_URL = $BaseUrl
$env:OPENAI_MODEL = $Model
$env:OPENAI_REASONING_EFFORT = $ReasoningEffort
$env:CC2OPEN_PORT = "$Port"
$env:CC2OPEN_STREAM_PING_INTERVAL = "$StreamPingInterval"
$env:CC2OPEN_STREAM_IDLE_TIMEOUT = "$StreamIdleTimeout"
$env:CC2OPEN_DEBUG = "$Debug"
$env:CC2OPEN_DEBUG_PET = "$DebugPet"
$env:CC2OPEN_STATE_PATH = $statePath

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
