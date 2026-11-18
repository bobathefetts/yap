; Yap installer (Inno Setup 6). Built by share.bat (tools\make_release.py).
;
; Installs for the current user only (no admin prompt) into %LOCALAPPDATA%\Programs\Yap.
; Yap can write there, so it can download its models on first launch.
;
; Small installer: Yap-Setup.exe alone holds the app and AI engine. The models are downloaded
; on first launch (only the ones that PC needs).
; Offline installer: when "models" and "cuda" folders sit next to Yap-Setup.exe (as in
; Yap-Setup-Offline.zip), they are installed too and nothing is downloaded.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define Root ".."
#define Dist Root + "\dist\Yap"

[Setup]
AppId={{70CEBACE-E41F-4513-BD28-1F22BF381339}
AppName=Yap
AppVersion={#AppVersion}
AppVerName=Yap {#AppVersion}
AppPublisher=Yap
AppComments=Free, private push-to-talk dictation
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Yap installer
DefaultDirName={autopf}\Yap
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; Room for the models (downloaded on first launch, or copied from the offline bundle).
ExtraDiskSpaceRequired=4000000000
OutputDir={#Root}\release
OutputBaseFilename=Yap-Setup
SetupIconFile={#Root}\assets\yap.ico
UninstallDisplayIcon={app}\Yap.exe
UninstallDisplayName=Yap
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
; Yap is closed by [Code] below instead (it has no unsaved work).
CloseApplications=no

[Tasks]
Name: "startup"; Description: "Start Yap when I sign in to Windows (recommended, so it is always ready)"
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[InstallDelete]
; Remove the previous version's libraries so an upgrade never mixes versions.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#Dist}\Yap.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Dist}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Dist}\engine\*"; DestDir: "{app}\engine"; Flags: ignoreversion recursesubdirs createallsubdirs
; Offline bundle only: copied from next to Yap-Setup.exe when present.
Source: "{src}\models\*"; DestDir: "{app}\models"; Flags: external recursesubdirs createallsubdirs skipifsourcedoesntexist ignoreversion
Source: "{src}\cuda\*"; DestDir: "{app}\cuda"; Flags: external recursesubdirs createallsubdirs skipifsourcedoesntexist ignoreversion

[Icons]
Name: "{autoprograms}\Yap"; Filename: "{app}\Yap.exe"; WorkingDir: "{app}"; Comment: "Yap: local dictation"
Name: "{autodesktop}\Yap"; Filename: "{app}\Yap.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Yap"; ValueData: """{app}\Yap.exe"""; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\Yap.exe"; Description: "Start Yap now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Models and libraries Yap downloaded itself after installation.
Type: filesandordirs; Name: "{app}\models"
Type: filesandordirs; Name: "{app}\cuda"
Type: filesandordirs; Name: "{app}\engine"
Type: filesandordirs; Name: "{app}\downloads"
Type: dirifempty; Name: "{app}"

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';

procedure CloseYap;
var
  Code: Integer;
begin
  { Yap keeps no unsaved work, and its AI engine always closes with it. }
  if Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM Yap.exe', '', SW_HIDE,
          ewWaitUntilTerminated, Code) and (Code = 0) then
    Sleep(1000);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CloseYap;
  Result := '';
end;

function HasOfflineModels: Boolean;
begin
  Result := DirExists(ExpandConstant('{src}\models'));
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfo;
  if MemoTasksInfo <> '' then
    Result := Result + NewLine + NewLine + MemoTasksInfo;
  Result := Result + NewLine + NewLine + 'AI models:' + NewLine;
  if HasOfflineModels then
    Result := Result + Space + 'Included, nothing needs to be downloaded.'
  else
    Result := Result + Space + 'Downloaded the first time Yap starts: about 2 GB,' + NewLine +
              Space + 'or 3.5 GB on PCs with an NVIDIA graphics card.';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Value, Data: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    CloseYap;
    { Yap can also add its sign-in entry itself (tray menu): remove it if it points here. }
    if RegQueryStringValue(HKCU, RunKey, 'Yap', Value) and
       (Pos(Lowercase(ExpandConstant('{app}')), Lowercase(Value)) > 0) then
      RegDeleteValue(HKCU, RunKey, 'Yap');
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    Data := ExpandConstant('{%USERPROFILE}\.yap');
    if DirExists(Data) and
       (SuppressibleMsgBox('Also delete your Yap settings and dictation history?' + #13#10#13#10 +
          Data, mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES) then
    begin
      DeleteFile(Data + '\config.json');
      DeleteFile(Data + '\history.jsonl');
      DelTree(Data + '\*.log*', False, True, False);
      DelTree(Data + '\sounds', True, True, True);
      RemoveDir(Data);
    end;
  end;
end;
