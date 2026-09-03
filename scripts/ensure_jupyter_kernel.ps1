# Re-register the researchOS Jupyter kernel after recreating .venv.
# Usage: powershell -File scripts/ensure_jupyter_kernel.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    throw "Missing venv at $py. Create it first: python -m venv .venv && .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
}

& $py -m pip install -U "ipykernel>=6" "jupyter_client>=8" | Out-Host
& $py -m ipykernel install --user --name researchOS --display-name "Python (researchOS)" | Out-Host

# Fix the venv's default python3 kernelspec: bare "python" resolves to system
# Python on PATH (no ipykernel) and causes Cursor to hang on "Restarting".
& $py -c @"
import json
from pathlib import Path
p = Path(r'$root') / '.venv' / 'share' / 'jupyter' / 'kernels' / 'python3' / 'kernel.json'
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps({
    'argv': [r'$py', '-Xfrozen_modules=off', '-m', 'ipykernel_launcher', '-f', '{connection_file}'],
    'display_name': 'Python (researchOS)',
    'language': 'python',
    'metadata': {'debugger': True, 'supported_encryption': 'curve'},
    'kernel_protocol_version': '5.5',
}, indent=1) + '\n', encoding='utf-8')
print(f'Wrote {p}')
"@

Write-Host "OK: researchOS kernel points at $py"
& $py -m jupyter kernelspec list
