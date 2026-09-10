$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repositoryRoot
& "$repositoryRoot\.venv\Scripts\python.exe" -m jupyter lab
exit $LASTEXITCODE
