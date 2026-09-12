# Launch both research projects using the root environment.
param([switch]$Check, [string]$Python)
$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $repositoryRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw "Missing Python environment: $Python" }
$previousPythonPath = $env:PYTHONPATH
$projectPaths = @((Join-Path $repositoryRoot 'gaming\src'), (Join-Path $repositoryRoot 'vehicle\src'))
$env:PYTHONPATH = ($projectPaths + @($previousPythonPath) | Where-Object { $_ }) -join [IO.Path]::PathSeparator
try {
    if ($Check) {
        & $Python -B -c "import variant_gaming, vehicle_tracker; print('Both research packages import successfully')"
    } else {
        & $Python -m jupyter lab --notebook-dir=$repositoryRoot
    }
    if ($LASTEXITCODE -ne 0) { throw "Python exited with code $LASTEXITCODE" }
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
