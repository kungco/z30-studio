$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
try {
    $z30Python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $z30Python)) {
        Write-Host 'Preparing Z30 Studio (first start only)...'
        $z30Command = Get-Command python -ErrorAction SilentlyContinue
        if ($z30Command) {
            & $z30Command.Source -m venv (Join-Path $PSScriptRoot '.venv')
        } else {
            $z30Launcher = Get-Command py -ErrorAction SilentlyContinue
            if (-not $z30Launcher) { throw 'Python 3.12+ (64-bit) is required: https://www.python.org/downloads/' }
            & $z30Launcher.Source -3 -m venv (Join-Path $PSScriptRoot '.venv')
        }
        if ($LASTEXITCODE -ne 0) { throw 'Could not create Python environment.' }
    }
    & $z30Python -c "import sys,struct; sys.exit(0 if sys.version_info >= (3,12) and struct.calcsize('P') == 8 else 1)"
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ (64-bit) is required. Install a supported Python and recreate .venv.' }
    & $z30Python -c "import importlib.util,sys; sys.exit(0 if all(importlib.util.find_spec(p) for p in ['rawpy','numpy','PIL','tifffile']) else 1)"
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Installing RAW decoder. Internet is needed for this first setup.'
        & $z30Python -m pip install --only-binary=:all: -r (Join-Path $PSScriptRoot 'requirements.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check connection and retry.' }
    }
    & $z30Python (Join-Path $PSScriptRoot 'desktop.py')
    exit $LASTEXITCODE
} catch {
    Write-Host $_ -ForegroundColor Red
    exit 1
}
