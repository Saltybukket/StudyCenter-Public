#ifndef AppVersion
#define AppVersion "0.1.0b1"
#endif
#ifndef BundleDir
#define BundleDir "..\..\dist\windows\StudyCenter-Public"
#endif
#ifndef OutputDir
#define OutputDir "..\..\dist\windows"
#endif
[Setup]
AppId={{B60E9F91-11F3-4EA8-B973-5C6D65C7A59A}
AppName=StudyCenter-Public
AppVersion={#AppVersion}
AppPublisher=StudyCenter-Public contributors
DefaultDirName={localappdata}\Programs\StudyCenter-Public
DefaultGroupName=StudyCenter-Public
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
LicenseFile=..\..\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename=studycenter-public-{#AppVersion}-windows-x86_64-setup
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0
UninstallDisplayIcon={app}\StudyCenter-Public.exe
CloseApplications=no
RestartApplications=no
AppMutex=StudyCenterPublicInstallerOperation
[Tasks]
Name: "desktopicon"; Description: "Desktop shortcut"; Flags: unchecked
Name: "startup"; Description: "Start StudyCenter-Public when I sign in"; Flags: unchecked
[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\StudyCenter-Public"; Filename: "{app}\StudyCenter-Public.exe"; Parameters: "--data-dir ""{code:GetDataDirectory}"""
Name: "{group}\Uninstall StudyCenter-Public"; Filename: "{uninstallexe}"
Name: "{userdesktop}\StudyCenter-Public"; Filename: "{app}\StudyCenter-Public.exe"; Parameters: "--data-dir ""{code:GetDataDirectory}"""; Tasks: desktopicon
Name: "{userstartup}\StudyCenter-Public"; Filename: "{app}\StudyCenter-Public.exe"; Parameters: "--background --data-dir ""{code:GetDataDirectory}"""; Tasks: startup
[Run]
Filename: "{app}\StudyCenter-Public.exe"; Parameters: "--data-dir ""{code:GetDataDirectory}"""; Description: "Start StudyCenter-Public"; Flags: nowait postinstall skipifsilent
[Code]
var DataDirectoryPage: TInputDirWizardPage;
function GetDataDirectory(Param: String): String;
begin
  if Assigned(DataDirectoryPage) then Result := DataDirectoryPage.Values[0]
  else Result := GetPreviousData('DataDirectory', ExpandConstant('{localappdata}\StudyCenter-Public\Data'));
end;
procedure InitializeWizard;
begin
  DataDirectoryPage := CreateInputDirPage(wpSelectDir, 'User data folder',
    'Choose where your originals and personal decisions are stored.',
    'The program and user data need separate folders. Updates and uninstall keep this folder.', False, '');
  DataDirectoryPage.Add('');
  DataDirectoryPage.Values[0] := ExpandConstant('{param:DATADIR|' + GetPreviousData('DataDirectory', ExpandConstant('{localappdata}\StudyCenter-Public\Data')) + '}');
end;
procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'DataDirectory', GetDataDirectory(''));
end;
function NextButtonClick(CurPageID: Integer): Boolean;
var ProgramDirectory, DataDirectory: String;
begin
  Result := True;
  if CurPageID = DataDirectoryPage.ID then begin
    ProgramDirectory := Lowercase(AddBackslash(ExpandFileName(WizardDirValue)));
    DataDirectory := Lowercase(AddBackslash(ExpandFileName(GetDataDirectory(''))));
    if (Pos(ProgramDirectory, DataDirectory) = 1) or (Pos(DataDirectory, ProgramDirectory) = 1) then begin
      MsgBox('Choose user data outside the program folder.', mbError, MB_OK);
      Result := False;
    end;
    if (Pos('\\', DataDirectory) = 1) or (Pos('\onedrive', DataDirectory) > 0) or
       (Pos('\dropbox', DataDirectory) > 0) or (Pos('\google drive', DataDirectory) > 0) then begin
      MsgBox('Choose a local folder outside cloud synchronisation.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;
function DataDirectoryProblem: String;
var Search: TFindRec; Existing: Boolean; DataDirectory, Probe, ProgramDirectory, NormalData: String;
begin
  Result := '';
  DataDirectory := ExpandFileName(GetDataDirectory(''));
  ProgramDirectory := Lowercase(AddBackslash(ExpandFileName(WizardDirValue)));
  NormalData := Lowercase(AddBackslash(DataDirectory));
  if (Pos(ProgramDirectory, NormalData) = 1) or (Pos(NormalData, ProgramDirectory) = 1) then begin
    Result := 'Choose user data outside the program folder.'; exit;
  end;
  if (Pos('\\', NormalData) = 1) or (Pos('\onedrive', NormalData) > 0) or
     (Pos('\dropbox', NormalData) > 0) or (Pos('\google drive', NormalData) > 0) then begin
    Result := 'Choose a local folder outside cloud synchronisation.'; exit;
  end;
  Existing := False;
  if DirExists(DataDirectory) and not FileExists(AddBackslash(DataDirectory) + 'instance.json') then begin
    if FindFirst(AddBackslash(DataDirectory) + '*', Search) then begin
      try
        repeat
          if (Search.Name <> '.') and (Search.Name <> '..') then Existing := True;
        until not FindNext(Search);
      finally FindClose(Search); end;
    end;
    if Existing then begin Result := 'The selected data folder contains unrelated files. Choose an empty folder or an existing Public instance.'; exit; end;
  end;
  if not ForceDirectories(DataDirectory) then begin Result := 'The selected data folder cannot be created.'; exit; end;
  Probe := AddBackslash(DataDirectory) + '.studycenter-public-write-check-' + GetDateTimeString('yyyymmddhhnnsszzz', '', '');
  if FileExists(Probe) then begin Result := 'Temporary access-check filename already exists. Retry.'; exit; end;
  if not SaveStringToFile(Probe, 'Synthetic installation access check', False) then begin Result := 'The selected data folder is read-only.'; exit; end;
  if not DeleteFile(Probe) then Result := 'The selected data folder does not permit normal file removal.';
end;
function PrepareToInstall(var NeedsRestart: Boolean): String;
var ResultCode, Attempt: Integer; DataDirectory, DatabaseFile, BackupDirectory: String;
begin
  Result := DataDirectoryProblem;
  if Result <> '' then exit;
  DataDirectory := GetDataDirectory('');
  if FileExists(ExpandConstant('{app}\StudyCenter-Public.exe')) then begin
    if not Exec(ExpandConstant('{app}\StudyCenter-Public.exe'), '--quit --data-dir "' + GetDataDirectory('') + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      Result := 'Close StudyCenter-Public before updating.';
    for Attempt := 1 to 100 do begin
      if not FileExists(AddBackslash(DataDirectory) + 'runtime.json') then break;
      Sleep(100);
    end;
    if FileExists(AddBackslash(DataDirectory) + 'runtime.json') then
      Result := 'The selected Public instance is still running. Close it before updating.';
    if Result <> '' then exit;
    DatabaseFile := AddBackslash(DataDirectory) + 'studycenter-public.sqlite3';
    if FileExists(DatabaseFile) then begin
      BackupDirectory := AddBackslash(DataDirectory) + 'backups\before-update-' + GetDateTimeString('yyyymmddhhnnsszzz', '', '');
      if not ForceDirectories(BackupDirectory) or
         not FileCopy(DatabaseFile, AddBackslash(BackupDirectory) + 'studycenter-public.sqlite3', True) then
        Result := 'A backup of the existing database could not be created. Update cancelled; keep the current installation.';
    end;
  end;
end;
function InitializeUninstall: Boolean;
var ResultCode, Attempt: Integer; DataDirectory: String;
begin
  Result := False;
  DataDirectory := GetDataDirectory('');
  if FileExists(ExpandConstant('{app}\StudyCenter-Public.exe')) then begin
    if not Exec(ExpandConstant('{app}\StudyCenter-Public.exe'), '--quit --data-dir "' + DataDirectory + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then begin
      MsgBox('Close StudyCenter-Public before uninstalling.', mbError, MB_OK); exit;
    end;
    for Attempt := 1 to 100 do begin
      if not FileExists(AddBackslash(DataDirectory) + 'runtime.json') then break;
      Sleep(100);
    end;
    if FileExists(AddBackslash(DataDirectory) + 'runtime.json') then begin
      MsgBox('The selected Public instance is still running. Close it before uninstalling.', mbError, MB_OK); exit;
    end;
  end;
  Result := True;
  { User data and own credentials are retained. Explicit reset is in app settings. }
end;
