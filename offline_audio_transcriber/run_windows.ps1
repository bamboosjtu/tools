param(
    [Parameter(Mandatory=$true)]
    [string]$InputFile,
    [string]$Model = "small",
    [string]$OutputDir = "transcript_output",
    [string]$ModelsDir = "$PSScriptRoot\models",
    [int]$DownloadRetries = 20,
    [int]$DownloadTimeout = 120
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found in PATH. Install Python 3.10 or later first."
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw "ffmpeg was not found in PATH. Install it with: winget install Gyan.FFmpeg"
}

python -m pip install -r "$PSScriptRoot\requirements.txt"
python "$PSScriptRoot\transcribe_audio.py" `
    "$InputFile" `
    --model "$Model" `
    --models-dir "$ModelsDir" `
    --download-retries $DownloadRetries `
    --download-timeout $DownloadTimeout `
    --language zh `
    --output-dir "$OutputDir" `
    --glossary "$PSScriptRoot\glossary.example.txt"

if ($LASTEXITCODE -ne 0) {
    throw "Transcription failed with exit code $LASTEXITCODE"
}
