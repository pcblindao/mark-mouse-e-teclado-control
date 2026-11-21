; Stable current-user installation. Keep AppId unchanged across releases.
#ifndef AppVersion
  #define AppVersion "0.7.1"
#endif
#ifndef AppBinary
  #define AppBinary "..\dist\markcontrol-windows-x64.exe"
#endif
#ifndef OutputPath
  #define OutputPath "..\dist"
#endif

[Setup]
AppId={{F7063A58-BC52-46D8-B1CE-479E87DF7281}
AppName=Mark Mouse e Teclado Control
AppVersion={#AppVersion}
AppVerName=Mark Mouse e Teclado Control v{#AppVersion}
VersionInfoVersion={#AppVersion}.0
VersionInfoTextVersion={#AppVersion}
AppPublisher=MKT com Marcos
AppPublisherURL=https://mktcommarcos.com.br/
AppSupportURL=https://mktcommarcos.com.br/aplicacoes/mark-mouse-e-teclado-control/
AppUpdatesURL=https://mktcommarcos.com.br/aplicacoes/mark-mouse-e-teclado-control/
DefaultDirName={code:InstallDirectory}
DisableDirPage=yes
UsePreviousAppDir=no
DefaultGroupName=Mark Mouse e Teclado Control
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
DisableWelcomePage=yes
DisableReadyPage=yes
DisableFinishedPage=yes
ShowLanguageDialog=no
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\MarkMouseTecladoControl.exe
UninstallDisplayName=Mark Mouse e Teclado Control v{#AppVersion}
OutputDir={#OutputPath}
OutputBaseFilename=MarkMouseTecladoControl-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
SetupLogging=yes
SetupMutex=Local\MarkMouseTecladoControl-Setup
CloseApplications=yes
CloseApplicationsFilter=MarkMouseTecladoControl.exe
RestartApplications=no
AlwaysRestart=no
RestartIfNeededByRun=no

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Files]
Source: "{#AppBinary}"; DestName: "MarkControl-preinstall.exe"; Flags: dontcopy
Source: "{#AppBinary}"; DestDir: "{app}"; DestName: "MarkMouseTecladoControl.exe"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\Mark Mouse e Teclado Control\Mark Mouse e Teclado Control"; Filename: "{app}\MarkMouseTecladoControl.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\Mark Mouse e Teclado Control"; Filename: "{app}\MarkMouseTecladoControl.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\MarkMouseTecladoControl.exe"; Parameters: "--after-install --autostart"; WorkingDir: "{app}"; Flags: nowait; Check: ShouldStartApplication

[UninstallDelete]
; Only program-owned installation artifacts; never the separate MarkControl profile.
Type: files; Name: "{app}\installation.json"
Type: files; Name: "{app}\.bootstrap-previous.exe"
Type: filesandordirs; Name: "{app}\.MarkMouseTecladoControl.exe.updates"

[Code]
function InstallDirectory(Param: String): String;
var
  Base: String;
begin
  Base := GetEnv('LOCALAPPDATA');
  if Base = '' then
    Base := ExpandConstant('{localappdata}');
  Result := AddBackslash(Base) + 'Programs\MarkControl';
end;

function ShouldStartApplication: Boolean;
var
  I: Integer;
begin
  Result := True;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/NOSTART') = 0 then
      Result := False;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
begin
  Result := '';
  if CompareText(AddBackslash(ExpandFileName(WizardDirValue)),
      AddBackslash(ExpandFileName(InstallDirectory('')))) <> 0 then
  begin
    Result := 'A instalação usa a pasta fixa do aplicativo neste perfil do Windows.';
    Exit;
  end;
  ExtractTemporaryFile('MarkControl-preinstall.exe');
  if not Exec(ExpandConstant('{tmp}\MarkControl-preinstall.exe'), '--prepare-install',
      ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) then
    Result := 'Não foi possível preparar a atualização do aplicativo.'
  else if ExitCode <> 0 then
    Result := 'A cópia anterior não encerrou. Feche o aplicativo e tente novamente.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ExitCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    if not Exec(ExpandConstant('{app}\MarkMouseTecladoControl.exe'), '--register-install',
        ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) then
      RaiseException('Não foi possível registrar a instalação do aplicativo.');
    if ExitCode <> 0 then
      RaiseException('O aplicativo não confirmou o registro da instalação.');
  end;
end;

function InitializeUninstall: Boolean;
var
  ProgramFile: String;
  HelperFile: String;
  ExitCode: Integer;
begin
  Result := False;
  if CheckForMutexes('Local\MarkMouseTecladoControl-Setup') then
    Exit;
  CreateMutex('Local\MarkMouseTecladoControl-Setup');
  ProgramFile := ExpandConstant('{app}\MarkMouseTecladoControl.exe');
  if FileExists(ProgramFile) then
  begin
    HelperFile := ExpandConstant('{tmp}\MarkControl-uninstall-prep.exe');
    if not FileCopy(ProgramFile, HelperFile, False) then
      Exit;
    Result := Exec(HelperFile, '--prepare-install', ExpandConstant('{tmp}'),
      SW_HIDE, ewWaitUntilTerminated, ExitCode) and (ExitCode = 0);
    DeleteFile(HelperFile);
  end
  else
    Result := True;
end;
