param(
    [switch]$SkipInstall,
    [switch]$SkipArchive
)

$ErrorActionPreference = "Stop"
$projectDir = $PSScriptRoot

Push-Location $projectDir
try {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw "Python was not found in PATH."
    }
    if (-not (Test-Path ".\models\ggml-small.bin")) {
        throw "The bundled model is missing: models\ggml-small.bin"
    }

    if (-not $SkipInstall) {
        python -m pip install --upgrade -r requirements-build.txt
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install build dependencies."
        }
    }

    python -m PyInstaller --noconfirm --clean .\OfflineAudioTranscriber.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed with exit code $LASTEXITCODE."
    }

    $exe = Join-Path $projectDir "dist\OfflineAudioTranscriber\OfflineAudioTranscriber.exe"
    $smokeProcess = Start-Process `
        -FilePath $exe `
        -ArgumentList "--smoke-test" `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    if ($smokeProcess.ExitCode -ne 0) {
        $smokeError = Join-Path $env:TEMP "OfflineAudioTranscriber-smoke-test-error.txt"
        if (Test-Path $smokeError) {
            Get-Content $smokeError
        }
        throw "The packaged application failed its smoke test."
    }

    if (-not $SkipArchive) {
        $archive = Join-Path $projectDir "dist\OfflineAudioTranscriber-windows-x64.zip"
        if (Test-Path $archive) {
            Remove-Item -LiteralPath $archive -Force
        }
        Compress-Archive `
            -Path ".\dist\OfflineAudioTranscriber" `
            -DestinationPath $archive `
            -CompressionLevel Optimal
        Write-Host "Portable archive created: $archive"
    }

    Write-Host "Build verified: $exe"
}
finally {
    Pop-Location
}
