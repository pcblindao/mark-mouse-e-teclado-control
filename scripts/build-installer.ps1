param(
    [string]$AppExe,
    [string]$OutputDir,
    [string]$PythonExe = 'python',
    [string]$IsccExe
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $AppExe) {
    foreach ($candidate in @('dist\markcontrol-windows-x64.exe', 'dist\Mark Mouse e Teclado Control.exe')) {
        $candidatePath = Join-Path $repoRoot $candidate
        if (Test-Path -LiteralPath $candidatePath -PathType Leaf) {
            $AppExe = $candidatePath
            break
        }
    }
}
if (-not $AppExe -or -not (Test-Path -LiteralPath $AppExe -PathType Leaf)) {
    throw 'Compile o executável Windows antes de gerar o instalador.'
}
$AppExe = (Resolve-Path -LiteralPath $AppExe).Path
if (-not $OutputDir) { $OutputDir = Join-Path $repoRoot 'dist' }
$OutputDir = [IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
if (-not $IsccExe) {
    foreach ($candidate in @(
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $IsccExe = $candidate
            break
        }
    }
    if (-not $IsccExe) {
        $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($command) { $IsccExe = $command.Source }
    }
}
if (-not $IsccExe -or -not (Test-Path -LiteralPath $IsccExe -PathType Leaf)) {
    throw 'Inno Setup 6 não foi encontrado. Informe -IsccExe com o caminho do ISCC.exe.'
}
$versionSource = Get-Content -LiteralPath (Join-Path $repoRoot 'markcontrol\version.py') -Raw
if ($versionSource -notmatch '__version__\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"') {
    throw 'A versão do aplicativo não foi encontrada.'
}
$appVersion = $Matches[1]
$checkVersion = @'
import marshal, re, sys, types
from PyInstaller.archive.readers import CArchiveReader
archive = CArchiveReader(sys.argv[1])
pyz = archive.open_embedded_archive(next(name for name in archive.toc if name.endswith('.pyz')))
code = pyz.extract('markcontrol.version')
versions = [value for value in code.co_consts if isinstance(value, str) and re.fullmatch(r'\d+\.\d+\.\d+', value)]
if len(versions) != 1:
    raise SystemExit('Executável sem versão reconhecida')
def strings(code):
    for value in code.co_consts:
        if isinstance(value, str):
            yield value
        elif isinstance(value, types.CodeType):
            yield from strings(value)
entry = marshal.loads(archive.extract('start'))
if not {'--prepare-install', '--register-install', '--after-install'} <= set(strings(entry)):
    raise SystemExit('Executável sem os comandos necessários ao Setup')
print(versions[0])
'@
$frozenVersion = & $PythonExe -c $checkVersion $AppExe
if ($LASTEXITCODE -ne 0) {
    throw 'O executável não contém todos os recursos exigidos pelo Setup. Recompile os arquivos atuais.'
}
if ($frozenVersion.Trim() -ne $appVersion) {
    throw 'A versão do executável difere do código. Recompile antes de gerar o Setup.'
}
& $IsccExe "/DAppVersion=$appVersion" "/DAppBinary=$AppExe" "/DOutputPath=$OutputDir" (Join-Path $repoRoot 'installer\MarkControl.iss')
if ($LASTEXITCODE -ne 0) { throw "O compilador Inno Setup falhou ($LASTEXITCODE)." }
$setupPath = Join-Path $OutputDir "MarkMouseTecladoControl-Setup-$appVersion.exe"
if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) { throw 'O instalador não foi gerado.' }
Write-Output $setupPath
