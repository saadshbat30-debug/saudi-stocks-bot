# Runs the bot inside its own virtual environment (.venv) so it never changes
# the Python packages used by your other projects.
# Usage:  powershell -ExecutionPolicy Bypass -File start.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating isolated environment (.venv)..."
    py -m venv .venv
}
$python = ".venv\Scripts\python.exe"

$stamp = ".venv\.requirements-installed"
if (-not (Test-Path $stamp) -or (Get-Item "requirements.txt").LastWriteTime -gt (Get-Item $stamp).LastWriteTime) {
    Write-Host "Installing bot packages inside .venv only..."
    & $python -m pip install --disable-pip-version-check -q -r requirements.txt
    New-Item -ItemType File -Force $stamp | Out-Null
}

# Load keys from .env (KEY=value per line) so you don't retype them each time.
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ""
    Write-Host "Created .env - open it, put your SAHMK_API_KEY and TELEGRAM_BOT_TOKEN, save, then run start.ps1 again."
    notepad .env
    exit 1
}
Get-Content ".env" -Encoding UTF8 | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$' -and $matches[2] -ne "") {
        Set-Item -Path "Env:$($matches[1])" -Value $matches[2].Trim()
    }
}

& $python run_polling.py
