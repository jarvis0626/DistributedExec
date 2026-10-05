param([string]$Python = '.venv\Scripts\python.exe')
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot
$compilerRoot = Join-Path $projectRoot 'local-data\inno-6.7.3'
$compiler = Join-Path $compilerRoot 'ISCC.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    $download = Join-Path $projectRoot 'local-data\innosetup-6.7.3.exe'
    New-Item -ItemType Directory -Force -Path (Split-Path $download) | Out-Null
    if (-not (Test-Path -LiteralPath $download)) {
        Invoke-WebRequest -Uri 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe' -OutFile $download
    }
    $expected = '9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732'
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $download).Hash.ToLowerInvariant() -ne $expected) {
        throw 'Inno Setup build tool checksum mismatch'
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $download
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|,\s*)O=Pyrsys B\.V\.(,|$)') {
        throw 'Inno Setup build tool signature mismatch'
    }
    $provision = Start-Process -FilePath $download -ArgumentList @('/CURRENTUSER', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', "/DIR=`"$compilerRoot`"") -WindowStyle Hidden -Wait -PassThru
    if ($provision.ExitCode -ne 0) { throw 'Installing the Inno Setup build tool failed' }
}
$version = & $Python -c 'from distributedexec import __version__; print(__version__)'
if ($LASTEXITCODE -ne 0) { throw 'Cannot read application version' }
& $compiler "/DAppVersion=$version" 'installer\DistributedExec.iss'
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed' }
$installer = Join-Path $projectRoot "dist\DistributedExec-$version-windows-x64-setup.exe"
$hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash  $(Split-Path $installer -Leaf)" | Set-Content -Encoding ascii -LiteralPath "$installer.sha256"
Write-Output "Release-ready installer: $installer"
