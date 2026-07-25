$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found. Create .venv and install requirements.txt first."
}

Set-Location -LiteralPath $projectRoot
Write-Host "Portfolio + AI Twin: http://127.0.0.1:8765"
Write-Host "Press Ctrl+C to stop."
& $python -m uvicorn api.index:app --host 127.0.0.1 --port 8765 --reload
