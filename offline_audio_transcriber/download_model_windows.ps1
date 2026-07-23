param(
    [string]$Model = "small",
    [string]$ModelsDir = "$PSScriptRoot\models",
    [int]$Retries = 20
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) {
    throw "curl.exe was not found. Current Windows versions normally include it."
}

$hashes = @{
    "tiny" = "bd577a113a864445d4c299885e0cb97d4ba92b5f"
    "base" = "465707469ff3a37a2b9b8d8f89f2f99de7299dac"
    "small" = "55356645c2b361a969dfd0ef2c5a50d530afd8d5"
    "medium" = "fd9727b6e1217c2f614f9b698455c4ffd82463b4"
    "large-v3-turbo" = "4af2b29d7ec73d781377bfd1758ca957a807e941"
}

New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null
$target = Join-Path $ModelsDir "ggml-$Model.bin"
$partial = "$target.part"
$baseUrl = if ($env:WHISPER_MODEL_BASE_URL) {
    $env:WHISPER_MODEL_BASE_URL.TrimEnd("/")
} else {
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
}
$url = "$baseUrl/ggml-$Model.bin"

Write-Host "Downloading $Model to $partial"
Write-Host "Interrupted downloads will resume from the existing .part file."

& curl.exe `
    -L `
    -C - `
    --retry $Retries `
    --retry-delay 2 `
    --retry-all-errors `
    --connect-timeout 30 `
    --output $partial `
    $url

if ($LASTEXITCODE -ne 0) {
    throw "curl.exe failed with exit code $LASTEXITCODE. Run this script again to resume."
}

if ($hashes.ContainsKey($Model)) {
    $actual = (Get-FileHash -Path $partial -Algorithm SHA1).Hash.ToLowerInvariant()
    $expected = $hashes[$Model]
    if ($actual -ne $expected) {
        throw "SHA-1 mismatch. Expected $expected, got $actual. Partial file kept at $partial"
    }
    Write-Host "SHA-1 verified: $actual"
} else {
    Write-Warning "No built-in SHA-1 for model '$Model'; skipping checksum verification."
}

Move-Item -Force $partial $target
Write-Host "Model ready: $target"
