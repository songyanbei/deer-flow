[CmdletBinding()]
param(
    [string]$OutputDir = ".\dist\offline",
    [string]$Platform = "linux/amd64",
    [string]$BundleName = "deer-flow-offline-linux-amd64"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$outputRoot = Join-Path $repoRoot $OutputDir
$bundleRoot = Join-Path $outputRoot $BundleName
$imagesDir = Join-Path $bundleRoot "images"
$stagingCompose = Join-Path $bundleRoot "docker-compose.yaml"
$archivePath = Join-Path $outputRoot ($BundleName + ".zip")

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing required command: $Name"
    }
}

Require-Command "docker"

docker version | Out-Null
docker compose version | Out-Null

if (Test-Path $bundleRoot) {
    Remove-Item -LiteralPath $bundleRoot -Recurse -Force
}
if (Test-Path $archivePath) {
    Remove-Item -LiteralPath $archivePath -Force
}

New-Item -ItemType Directory -Path $imagesDir -Force | Out-Null

$images = @(
    @{
        Name = "deer-flow/backend:offline"
        Dockerfile = "docker/offline/backend.Dockerfile"
        Tar = "deer-flow-backend-offline.tar"
    },
    @{
        Name = "deer-flow/frontend:offline"
        Dockerfile = "docker/offline/frontend.Dockerfile"
        Tar = "deer-flow-frontend-offline.tar"
    },
    @{
        Name = "deer-flow/nginx:offline"
        Dockerfile = "docker/offline/nginx.Dockerfile"
        Tar = "deer-flow-nginx-offline.tar"
    }
)

foreach ($image in $images) {
    Write-Host "Building $($image.Name) for $Platform ..."
    docker build `
        --platform $Platform `
        -f (Join-Path $repoRoot $image.Dockerfile) `
        -t $image.Name `
        $repoRoot

    $tarPath = Join-Path $imagesDir $image.Tar
    Write-Host "Saving $($image.Name) to $tarPath ..."
    docker save -o $tarPath $image.Name
}

Copy-Item -LiteralPath (Join-Path $repoRoot "install.sh") -Destination (Join-Path $bundleRoot "install.sh")
Copy-Item -LiteralPath (Join-Path $repoRoot "docker/docker-compose-offline.yaml") -Destination $stagingCompose
Copy-Item -LiteralPath (Join-Path $repoRoot "docs/offline-install.md") -Destination (Join-Path $bundleRoot "README-offline.md")

Compress-Archive -Path (Join-Path $bundleRoot "*") -DestinationPath $archivePath

Write-Host ""
Write-Host "Offline bundle is ready:"
Write-Host "  Bundle folder: $bundleRoot"
Write-Host "  Archive file : $archivePath"
Write-Host ""
Write-Host "Send the zip to the Linux server, unzip it, then run:"
Write-Host "  chmod +x install.sh && ./install.sh"
