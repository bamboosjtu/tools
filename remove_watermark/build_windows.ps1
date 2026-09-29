param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$projectDir = $PSScriptRoot

Push-Location $projectDir
try {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw "Python was not found in PATH."
    }

    if (-not $SkipInstall) {
        python -m pip install --upgrade -r requirements-build.txt
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install build dependencies."
        }
    }

    python -m PyInstaller --noconfirm --clean .\RemoveWatermark.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed with exit code $LASTEXITCODE."
    }

    $exe = Join-Path $projectDir "dist\RemoveWatermark.exe"
    if (-not (Test-Path $exe)) {
        throw "Expected output not found: $exe"
    }

    $smokeProcess = Start-Process `
        -FilePath $exe `
        -ArgumentList "--smoke-test" `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    if ($smokeProcess.ExitCode -ne 0) {
        $smokeError = Join-Path $env:TEMP "RemoveWatermark-smoke-test-error.txt"
        if (Test-Path $smokeError) {
            Get-Content $smokeError
        }
        throw "The packaged application failed its smoke test."
    }

    Write-Host "Build verified: $exe"
}
finally {
    Pop-Location
}
