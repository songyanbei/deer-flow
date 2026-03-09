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
$logDir = Join-Path $runtimeDir 'logs'
$stateFile = Join-Path $runtimeDir 'services.json'

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

Ensure-Directory $runtimeDir
Ensure-Directory $logDir

if (-not $NoStop) {
    & (Join-Path $PSScriptRoot 'stop-full-dev.ps1') -Quiet
}

$langgraphLog = Join-Path $logDir 'langgraph.log'
$gatewayLog = Join-Path $logDir 'gateway.log'
$frontendLog = Join-Path $logDir 'frontend.log'

$backendDir = Join-Path $repoRoot 'backend'
$frontendDir = Join-Path $repoRoot 'frontend'

$langgraphCmd = "cd /d `"$backendDir`" && uv run langgraph dev --no-browser --allow-blocking --no-reload --port $LangGraphPort > `"$langgraphLog`" 2>&1"
$gatewayCmd = "cd /d `"$backendDir`" && uv run uvicorn src.gateway.app:app --host 127.0.0.1 --port $GatewayPort > `"$gatewayLog`" 2>&1"
$frontendCmd = "cd /d `"$frontendDir`" && set NEXT_PUBLIC_BACKEND_BASE_URL=http://127.0.0.1:$GatewayPort && set NEXT_PUBLIC_LANGGRAPH_BASE_URL=http://127.0.0.1:$LangGraphPort && pnpm.cmd exec next dev --hostname 127.0.0.1 --port $FrontendPort > `"$frontendLog`" 2>&1"

Write-Host 'Starting LangGraph...'
$langgraphProcess = Start-LoggedProcess -Name 'LangGraph' -Command $langgraphCmd
Wait-ForPort -Name 'LangGraph' -Port $LangGraphPort -TimeoutSeconds 90

Write-Host 'Starting Gateway...'
$gatewayProcess = Start-LoggedProcess -Name 'Gateway' -Command $gatewayCmd
Wait-ForPort -Name 'Gateway' -Port $GatewayPort -TimeoutSeconds 60

Write-Host 'Starting Frontend (non-turbo)...'
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


