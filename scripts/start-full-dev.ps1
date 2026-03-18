[CmdletBinding()]
param(
    [switch]$NoStop,
    [int]$LangGraphPort = 2024,
    [int]$GatewayPort = 8001,
    [int]$FrontendPort = 3000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimeDir = Join-Path $repoRoot '.dev-runtime'
$stateFile = Join-Path $runtimeDir 'services.json'
$envFile = Join-Path $repoRoot '.env'
$backendDir = Join-Path $repoRoot 'backend'
$frontendDir = Join-Path $repoRoot 'frontend'
$backendLogDir = Join-Path $backendDir 'logs'
$frontendLogDir = Join-Path $frontendDir 'logs'

function Ensure-Directory([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path | Out-Null
    }
}

function Test-TcpPort([string]$HostName, [int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(500)) {
            return $false
        }
        $client.EndConnect($iar)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Wait-ForPort([string]$Name, [int]$Port, [int]$TimeoutSeconds = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpPort -HostName '127.0.0.1' -Port $Port) {
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "$Name did not start listening on 127.0.0.1:$Port within $TimeoutSeconds seconds."
}

function Start-LoggedProcess([string]$Name, [string]$Command) {
    $process = Start-Process -FilePath 'cmd.exe' -ArgumentList '/d', '/c', $Command -WorkingDirectory $repoRoot -PassThru -WindowStyle Hidden
    if (-not $process -or $process.HasExited) {
        throw "Failed to start $Name."
    }
    return $process
}

function Import-DotEnvFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    foreach ($line in Get-Content -Path $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) {
            continue
        }

        $separatorIndex = $trimmed.IndexOf('=')
        if ($separatorIndex -lt 1) {
            continue
        }

        $key = $trimmed.Substring(0, $separatorIndex).Trim()
        $value = $trimmed.Substring($separatorIndex + 1).Trim()
        if ($value.Length -ge 2 -and (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        )) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [System.Environment]::SetEnvironmentVariable($key, $value, 'Process')
    }
}

Ensure-Directory $runtimeDir
Ensure-Directory $backendLogDir
Ensure-Directory $frontendLogDir
Import-DotEnvFile $envFile

if (-not $env:NEXT_PUBLIC_BACKEND_BASE_URL) {
    [System.Environment]::SetEnvironmentVariable(
        'NEXT_PUBLIC_BACKEND_BASE_URL',
        "http://127.0.0.1:$GatewayPort",
        'Process'
    )
}
if (-not $env:NEXT_PUBLIC_LANGGRAPH_BASE_URL) {
    [System.Environment]::SetEnvironmentVariable(
        'NEXT_PUBLIC_LANGGRAPH_BASE_URL',
        "http://127.0.0.1:$LangGraphPort",
        'Process'
    )
}

if (-not $NoStop) {
    & (Join-Path $PSScriptRoot 'stop-full-dev.ps1') -Quiet
}

$langgraphLog = Join-Path $backendLogDir 'langgraph.log'
$gatewayLog = Join-Path $backendLogDir 'gateway.log'
$frontendLog = Join-Path $frontendLogDir 'frontend.log'

$langgraphCmd = "cd /d `"$backendDir`" && uv run langgraph dev --no-browser --allow-blocking --no-reload --port $LangGraphPort > `"$langgraphLog`" 2>&1"
$gatewayCmd = "cd /d `"$backendDir`" && uv run uvicorn src.gateway.app:app --host 0.0.0.0 --port $GatewayPort > `"$gatewayLog`" 2>&1"
$frontendCmd = "cd /d `"$frontendDir`" && pnpm run dev > `"$frontendLog`" 2>&1"

Write-Host 'Starting LangGraph...'
$langgraphProcess = Start-LoggedProcess -Name 'LangGraph' -Command $langgraphCmd
Wait-ForPort -Name 'LangGraph' -Port $LangGraphPort -TimeoutSeconds 90

Write-Host 'Starting Gateway...'
$gatewayProcess = Start-LoggedProcess -Name 'Gateway' -Command $gatewayCmd
Wait-ForPort -Name 'Gateway' -Port $GatewayPort -TimeoutSeconds 60

Write-Host 'Starting Frontend...'
$frontendProcess = Start-LoggedProcess -Name 'Frontend' -Command $frontendCmd
Wait-ForPort -Name 'Frontend' -Port $FrontendPort -TimeoutSeconds 120

$state = [ordered]@{
    started_at = (Get-Date).ToString('s')
    mode = 'direct-no-nginx'
    ports = [ordered]@{
        langgraph = $LangGraphPort
        gateway = $GatewayPort
        frontend = $FrontendPort
    }
    processes = @(
        [ordered]@{ name = 'langgraph'; pid = $langgraphProcess.Id; log = $langgraphLog },
        [ordered]@{ name = 'gateway'; pid = $gatewayProcess.Id; log = $gatewayLog },
        [ordered]@{ name = 'frontend'; pid = $frontendProcess.Id; log = $frontendLog }
    )
}
$state | ConvertTo-Json -Depth 5 | Set-Content -Path $stateFile -Encoding utf8

Write-Host ''
Write-Host 'Development services are ready.'
Write-Host "Frontend : http://127.0.0.1:$FrontendPort"
Write-Host "Gateway  : http://127.0.0.1:$GatewayPort"
Write-Host "LangGraph: http://127.0.0.1:$LangGraphPort"
Write-Host ''
Write-Host 'Logs:'
Write-Host "  $frontendLog"
Write-Host "  $gatewayLog"
Write-Host "  $langgraphLog"
Write-Host ''
Write-Host 'Stop everything with:'
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot 'stop-full-dev.ps1')`""
