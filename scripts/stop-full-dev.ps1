[CmdletBinding()]
param(
    [switch]$Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimeDir = Join-Path $repoRoot '.dev-runtime'
$stateFile = Join-Path $runtimeDir 'services.json'

function Write-Info([string]$Message) {
    if (-not $Quiet) {
        Write-Host $Message
    }
}

function Stop-ProcessTree([int]$ProcessId) {
    try {
        $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if ($null -ne $proc) {
            & taskkill /PID $ProcessId /T /F | Out-Null
        }
    }
    catch {
    }
}

function Stop-PortOwner([int]$Port) {
    $lines = netstat -ano -p tcp | Select-String ":$Port"
    foreach ($line in $lines) {
        $parts = ($line.ToString() -split '\s+') | Where-Object { $_ }
        if ($parts.Length -ge 5 -and $parts[3] -eq 'LISTENING') {
            $ownerPid = 0
            if ([int]::TryParse($parts[4], [ref]$ownerPid)) {
                Stop-ProcessTree -ProcessId $ownerPid
            }
        }
    }
}

if (Test-Path -LiteralPath $stateFile) {
    $state = Get-Content -Path $stateFile -Raw | ConvertFrom-Json
    foreach ($process in $state.processes) {
        Write-Info "Stopping $($process.name) (PID $($process.pid))..."
        Stop-ProcessTree -ProcessId ([int]$process.pid)
    }
    Remove-Item -LiteralPath $stateFile -Force -ErrorAction SilentlyContinue
}
else {
    Write-Info 'No state file found. Falling back to port-based cleanup.'
}

foreach ($port in 3000, 8001, 2024) {
    Stop-PortOwner -Port $port
}

if (Get-Command docker -ErrorAction SilentlyContinue) {
    try {
        $containers = docker ps -q --filter "name=deer-flow-sandbox"
        if ($containers) {
            Write-Info 'Stopping sandbox containers...'
            $containers | ForEach-Object { docker stop $_ | Out-Null }
        }
    }
    catch {
    }
}

Write-Info 'All managed development services are stopped.'


