param([Parameter(Mandatory = $true)][string]$SetupExe)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows' -or -not $env:RUNNER_TEMP) {
    throw 'Este teste executa Setup e desinstalador e só pode rodar no runner Windows do GitHub Actions.'
}
$SetupExe = (Resolve-Path -LiteralPath $SetupExe).Path
$originalLocalAppData = $env:LOCALAPPDATA
$testRoot = Join-Path $env:RUNNER_TEMP ('markcontrol-setup-' + [Guid]::NewGuid().ToString('N'))
$env:LOCALAPPDATA = Join-Path $testRoot 'profile'
$profileDir = Join-Path $env:LOCALAPPDATA 'MarkControl'
$installDir = Join-Path $env:LOCALAPPDATA 'Programs\MarkControl'
$canonical = Join-Path $installDir 'MarkMouseTecladoControl.exe'
$uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{F7063A58-BC52-46D8-B1CE-479E87DF7281}_is1'
$version = (& python -c 'from markcontrol.version import __version__; print(__version__)').Trim()
if ($LASTEXITCODE -ne 0) { throw 'Não foi possível ler a versão esperada.' }
$windowTitle = "Mark Mouse e Teclado Control v$version"
$ownerPid = $null
$utf8 = [Text.UTF8Encoding]::new($false)
New-Item -ItemType Directory -Path $profileDir -Force | Out-Null

function Wait-Condition([scriptblock]$Condition, [string]$Message, [int]$Seconds = 60) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        if (& $Condition) { return }
        Start-Sleep -Milliseconds 200
    } while ((Get-Date) -lt $deadline)
    throw $Message
}

function Assert-Preferences {
    foreach ($name in $script:preferenceHashes.Keys) {
        $current = (Get-FileHash -LiteralPath (Join-Path $profileDir $name) -Algorithm SHA256).Hash
        if ($current -ne $script:preferenceHashes[$name]) { throw "A preferência $name foi alterada." }
    }
}

function Read-Owner {
    $file = Join-Path $profileDir 'desktop-instance.json'
    if (Test-Path -LiteralPath $file) { return Get-Content -LiteralPath $file -Raw | ConvertFrom-Json }
    return $null
}

function Owner-Alive([int]$ProcessId) {
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    return ($null -ne $process -and $process.Path -eq $canonical)
}

function Invoke-Setup([int]$Iteration) {
    $log = Join-Path $testRoot "setup-$Iteration.log"
    $arguments = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /NOSTART /DIR=`"$installDir`" /LOG=`"$log`""
    $process = Start-Process -FilePath $SetupExe -ArgumentList $arguments -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(120000)) { throw "O Setup $Iteration excedeu o tempo limite." }
    if ($process.ExitCode -ne 0) {
        if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 35 | Write-Host }
        throw "O Setup $Iteration falhou ($($process.ExitCode))."
    }
    if (-not (Test-Path -LiteralPath $canonical) -or -not (Test-Path -LiteralPath (Join-Path $installDir 'unins000.exe'))) {
        throw 'O Setup não instalou o executável e o desinstalador.'
    }
    $entries = @(Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall' |
        Where-Object { $_.PSChildName -eq '{F7063A58-BC52-46D8-B1CE-479E87DF7281}_is1' })
    if ($entries.Count -ne 1) { throw 'A instalação não tem uma entrada única no Windows.' }
    $entry = Get-ItemProperty -LiteralPath $uninstallKey
    if ($entry.DisplayVersion -ne $version -or $entry.DisplayName -ne $windowTitle -or
        $entry.InstallLocation.TrimEnd('\') -ne $installDir.TrimEnd('\')) {
        throw 'A entrada em Aplicativos Instalados não corresponde à versão e à pasta canônica.'
    }
    $shell = New-Object -ComObject WScript.Shell
    foreach ($shortcutPath in @(
        (Join-Path ([Environment]::GetFolderPath('Programs')) 'Mark Mouse e Teclado Control\Mark Mouse e Teclado Control.lnk'),
        (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Mark Mouse e Teclado Control.lnk')
    )) {
        if (-not (Test-Path -LiteralPath $shortcutPath)) { throw 'O Setup não criou o atalho esperado.' }
        if ($shell.CreateShortcut($shortcutPath).TargetPath -ne $canonical) { throw 'O atalho não aponta à instalação canônica.' }
    }
    Assert-Preferences
}

try {
    & python -c @'
from pathlib import Path
from markcontrol.config import Config, write_json
from markcontrol.certs import data_dir, tls_identity
folder = data_dir()
cfg = dict(Config.defaults(), name="CI fixture", auto_update=False, agent_enabled=False, devices=["a" * 64])
write_json(folder / "config.json", cfg)
write_json(folder / "diagnostic-consent.json", {"version": 2, "enabled": False})
write_json(folder / "known_hosts.json", {})
write_json(folder / "history.json", [])
tls_identity("127.0.0.1", [])
'@
    if ($LASTEXITCODE -ne 0) { throw 'Não foi possível criar o perfil isolado de teste.' }
    $script:preferenceHashes = @{}
    foreach ($name in @('config.json', 'diagnostic-consent.json', 'known_hosts.json', 'history.json', 'identity.pem')) {
        $script:preferenceHashes[$name] = (Get-FileHash -LiteralPath (Join-Path $profileDir $name) -Algorithm SHA256).Hash
    }
    foreach ($iteration in 1..2) {
        $previousOwner = $ownerPid
        Invoke-Setup $iteration
        if ($previousOwner -and (Owner-Alive $previousOwner)) { throw 'A reinstalação deixou a instância anterior aberta.' }
        Start-Process -FilePath $canonical -WindowStyle Hidden | Out-Null
        Wait-Condition {
            $owner = Read-Owner
            if ($owner -and (Owner-Alive $owner.pid)) {
                $window = Get-Process -Id $owner.pid -ErrorAction SilentlyContinue
                return ($window.MainWindowTitle -eq $windowTitle)
            }
            return $false
        } 'A janela instalada não abriu com a versão visível.'
        $ownerPid = (Read-Owner).pid
        Assert-Preferences
    }
    $window = Get-Process -Id $ownerPid
    if (-not $window.CloseMainWindow()) { throw 'A janela não aceitou o pedido de recolher.' }
    Start-Sleep -Milliseconds 700
    if (-not (Owner-Alive $ownerPid)) { throw 'O aplicativo encerrou ao recolher.' }
    $second = Start-Process -FilePath $canonical -WindowStyle Hidden -PassThru
    $third = Start-Process -FilePath $canonical -WindowStyle Hidden -PassThru
    foreach ($duplicate in @($second, $third)) {
        if (-not $duplicate.WaitForExit(30000) -or $duplicate.ExitCode -ne 0) { throw 'A abertura repetida não reutilizou a instância.' }
    }
    Wait-Condition {
        $visible = @(Get-Process -Name MarkMouseTecladoControl -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -eq $canonical -and $_.MainWindowTitle -eq $windowTitle })
        return ($visible.Count -eq 1 -and (Read-Owner).pid -eq $ownerPid)
    } 'A abertura repetida criou outra janela ou mudou o dono do IPC.'
    Assert-Preferences
    & python -c 'from markcontrol.installation import notify; from markcontrol.certs import data_dir; assert notify(data_dir(), "shutdown")["accepted"]'
    if ($LASTEXITCODE -ne 0) { throw 'A instância não aceitou o encerramento do teste.' }
    Wait-Condition { -not (Owner-Alive $ownerPid) } 'A instância do teste não encerrou.'
    $uninstaller = Start-Process -FilePath (Join-Path $installDir 'unins000.exe') `
        -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART' -WindowStyle Hidden -PassThru
    if (-not $uninstaller.WaitForExit(120000) -or $uninstaller.ExitCode -ne 0) { throw 'O desinstalador falhou.' }
    Wait-Condition { -not (Test-Path -LiteralPath $canonical) } 'O desinstalador não removeu o programa.'
    if (Test-Path -LiteralPath $uninstallKey) { throw 'A entrada do programa permaneceu após desinstalar.' }
    Assert-Preferences
    Write-Host 'Setup real aprovado: duas instalações, perfil preservado, registro único, atalhos, versão, instância única e desinstalação.'
} finally {
    if (Test-Path -LiteralPath $canonical) {
        & python -c 'from markcontrol.installation import notify; from markcontrol.certs import data_dir; notify(data_dir(), "shutdown")' 2>$null
    }
    Get-Process -Name MarkMouseTecladoControl -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $canonical } | Stop-Process -Force -ErrorAction SilentlyContinue
    $env:LOCALAPPDATA = $originalLocalAppData
}
