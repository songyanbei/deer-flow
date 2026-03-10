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
            & taskkill /PID $ProcessId /T /F 2>$null | Out-Null
        }
    }
    catch {
    }
}

function Get-ListeningProcessIds([int]$Port) {
    $owners = @()
    $lines = netstat -ano -p tcp | Select-String ":$Port"
    foreach ($line in $lines) {
        $parts = ($line.ToString() -split '\s+') | Where-Object { $_ }
        if ($parts.Length -ge 5 -and $parts[3] -eq 'LISTENING') {
            $ownerPid = 0
            if ([int]::TryParse($parts[4], [ref]$ownerPid)) {
                $owners += $ownerPid
            }
        }
    }
    return $owners | Sort-Object -Unique
}

function Stop-PortOwner([int]$Port) {
    foreach ($ownerPid in Get-ListeningProcessIds -Port $Port) {
        Stop-ProcessTree -ProcessId $ownerPid
    }
}

function Ensure-PortReleased([int]$Port, [int]$TimeoutSeconds = 15) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $owners = @(Get-ListeningProcessIds -Port $Port)
        if ($owners.Count -eq 0) {
            return
        }
        foreach ($ownerPid in $owners) {
            Stop-ProcessTree -ProcessId $ownerPid
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    $remaining = (Get-ListeningProcessIds -Port $Port) -join ', '
    throw "Port $Port is still in use by PID(s): $remaining"
}

if (Test-Path -LiteralPath $stateFile) {
    $state = Get-Content -Path $stateFile -Raw | ConvertFrom-Json
    foreach ($process in $state.processes) {
        Write-Info "Stopping $($process.name) (PID $($process.pid))..."
        Stop-ProcessTree -ProcessId ([int]$process.pid)
    }
}
else {
    Write-Info 'No state file found. Falling back to port-based cleanup.'
}

foreach ($port in 3000, 8001, 2024) {
    Stop-PortOwner -Port $port
    Ensure-PortReleased -Port $port
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

if (Test-Path -LiteralPath $stateFile) {
    Remove-Item -LiteralPath $stateFile -Force -ErrorAction SilentlyContinue
}

Write-Info 'All managed development services are stopped.'
