$ErrorActionPreference = "Stop"

Write-Host "[1/4] Create virtual environment (.venv_rknn_convert)"
python -m venv .venv_rknn_convert

Write-Host "[2/4] Upgrade pip/setuptools/wheel"
.\.venv_rknn_convert\Scripts\python.exe -m pip install --upgrade pip setuptools wheel

Write-Host "[3/4] Install RKNN conversion toolkit"
.\.venv_rknn_convert\Scripts\python.exe -m pip install rknn-toolkit2

Write-Host "[4/4] Clone RKNN model zoo (if not exists)"
if (!(Test-Path ".\rknn_model_zoo")) {
    git clone https://github.com/airockchip/rknn_model_zoo.git
} else {
    Write-Host "rknn_model_zoo already exists, skip clone."
}

Write-Host "Done. Activate with: .\.venv_rknn_convert\Scripts\Activate.ps1"
