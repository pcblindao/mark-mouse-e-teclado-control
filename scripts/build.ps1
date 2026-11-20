$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot '..')
try {
    python -m pip install '.[build]'
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    python -m PyInstaller --noconfirm markcontrol.spec
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if (-not (Test-Path -LiteralPath 'dist\Mark Mouse e Teclado Control.exe' -PathType Leaf)) {
        throw 'O build não gerou dist\Mark Mouse e Teclado Control.exe.'
    }
} finally {
    Pop-Location
}
