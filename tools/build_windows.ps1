param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot
$buildPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $buildPython)) {
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Creating the Python 3.12 build environment failed' }
}
& $buildPython -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Installing pinned build dependencies failed' }
& $buildPython tools/collect_licenses.py
if ($LASTEXITCODE -ne 0) { throw 'License collection failed' }
& $buildPython -m pytest -m 'not docker and not browser' -q
if ($LASTEXITCODE -ne 0) { throw 'Acceptance tests failed' }
& $buildPython -m PyInstaller --noconfirm DistributedExec.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
$builtExecutable = Join-Path $projectRoot 'dist\DistributedExec\DistributedExec.exe'
$smokeProcess = Start-Process -FilePath $builtExecutable -ArgumentList 'smoke' -WindowStyle Hidden -Wait -PassThru
if ($smokeProcess.ExitCode -ne 0) { throw 'Packaged application smoke test failed; see per-user logs' }
Write-Output "Built and smoke-tested: $builtExecutable"
