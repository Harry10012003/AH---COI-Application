[CmdletBinding()]
param(
    [string]$Destination = (Join-Path ([Environment]::GetFolderPath("Desktop")) ("TEST_COI_IT_DEPLOY_{0}.zip" -f (Get-Date -Format "yyyyMMdd_HHmmss")))
)

$ErrorActionPreference = "Stop"

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$destinationPath = [IO.Path]::GetFullPath($Destination)
$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$stageBase = Join-Path $tempBase ("test-coi-deploy-" + [guid]::NewGuid().ToString("N"))
$stageRoot = Join-Path $stageBase "TEST_COI_IT_DEPLOY"

$projectPrefix = $projectRoot.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if ($destinationPath.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Destination ZIP must be outside the project folder."
}
if (-not $stageBase.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use a staging location outside the system temporary folder."
}

$excludedDirectories = @(
    (Join-Path $projectRoot ".git"),
    (Join-Path $projectRoot ".pytest_cache"),
    (Join-Path $projectRoot ".venv"),
    (Join-Path $projectRoot "venv"),
    (Join-Path $projectRoot "env"),
    (Join-Path $projectRoot "node_modules"),
    (Join-Path $projectRoot "data\cache"),
    (Join-Path $projectRoot "data\exports"),
    (Join-Path $projectRoot "leftover_sql_excel")
)

try {
    New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null

    # Keep deployable source and reference templates, but never carry runtime
    # snapshots, issued data, logs, virtual environments, credentials or legacy
    # stand-alone Excel automation into an IT handover.
    $roboArgs = @(
        $projectRoot,
        $stageRoot,
        "/E",
        "/XD"
    ) + $excludedDirectories + @(
        "/XF",
        "*.pyc", "*.pyo", "*.pyd", "*.log", "*.db", "*.sqlite", "*.sqlite3", "*.zip", ".env", ".env.*",
        "/NFL", "/NDL", "/NJH", "/NJS", "/NP"
    )
    & robocopy @roboArgs | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "Robocopy failed with exit code $LASTEXITCODE."
    }

    # .env.example is intentionally restored after excluding all .env files.
    # It contains placeholders only and is required by the deployment guide.
    Copy-Item -LiteralPath (Join-Path $projectRoot ".env.example") -Destination (Join-Path $stageRoot ".env.example") -Force

    $manifestPath = Join-Path $stageRoot "DEPLOYMENT_MANIFEST.txt"
    $manifestFiles = Get-ChildItem -LiteralPath $stageRoot -Recurse -File |
        Sort-Object FullName |
        ForEach-Object { $_.FullName.Substring($stageRoot.Length + 1) }
    $manifestFiles + "DEPLOYMENT_MANIFEST.txt" |
        Set-Content -LiteralPath $manifestPath -Encoding utf8

    $destinationDirectory = Split-Path -Parent $destinationPath
    New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null
    if (Test-Path -LiteralPath $destinationPath) {
        Remove-Item -LiteralPath $destinationPath -Force
    }
    Compress-Archive -LiteralPath $stageRoot -DestinationPath $destinationPath -CompressionLevel Optimal -Force

    $hash = (Get-FileHash -LiteralPath $destinationPath -Algorithm SHA256).Hash
    Write-Host "Deployment ZIP created: $destinationPath"
    Write-Host "SHA256: $hash"
}
finally {
    if (Test-Path -LiteralPath $stageBase) {
        Remove-Item -LiteralPath $stageBase -Recurse -Force
    }
}
