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
#if VER >= 0x07000000
SetupArchitecture=x86
#endif
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
[UninstallDelete]
Type: files; Name: "{app}\installation-files.txt"
Type: files; Name: "{app}\installation-marker.ini"
[Icons]
Name: "{group}\StudyCenter-Public"; Filename: "{app}\StudyCenter-Public.exe"; Parameters: "--data-dir ""{code:GetDataDirectory}"""
Name: "{group}\Uninstall StudyCenter-Public"; Filename: "{uninstallexe}"
Name: "{userdesktop}\StudyCenter-Public"; Filename: "{app}\StudyCenter-Public.exe"; Parameters: "--data-dir ""{code:GetDataDirectory}"""; Tasks: desktopicon
Name: "{userstartup}\StudyCenter-Public"; Filename: "{app}\StudyCenter-Public.exe"; Parameters: "--background --data-dir ""{code:GetDataDirectory}"""; Tasks: startup
[Run]
Filename: "{app}\StudyCenter-Public.exe"; Parameters: "--data-dir ""{code:GetDataDirectory}"""; Description: "Start StudyCenter-Public"; Flags: nowait postinstall skipifsilent
[Code]
#include BundleDir + "\SYSTEM-RUNTIME-REQUIREMENTS.iss"
#include "system_runtime.iss"
type TNativeFindData = record
  Attributes, CreationLow, CreationHigh, AccessLow, AccessHigh: LongWord;
  WriteLow, WriteHigh, SizeHigh, SizeLow, Reserved0, Reserved1: LongWord;
  FileName: array[0..259] of Char;
  AlternateName: array[0..13] of Char;
end;
type TSafeFindRec = record
  Handle: THandle;
  Data: TNativeFindData;
  Name: String;
  Attributes: LongWord;
  Error: LongInt;
end;
const OwnApplication = 'StudyCenter.Public';
  OwnUninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B60E9F91-11F3-4EA8-B973-5C6D65C7A59A}_is1';
var DataDirectoryPage: TInputDirWizardPage;
  DataDirectoryPageReady: Boolean;
function GetFileAttributesW(Name: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function NativeFindFirst(Name: String; var Data: TNativeFindData): THandle;
  external 'FindFirstFileW@kernel32.dll stdcall';
function NativeFindNext(Handle: THandle; var Data: TNativeFindData): Boolean;
  external 'FindNextFileW@kernel32.dll stdcall';
function NativeFindClose(Handle: THandle): Boolean;
  external 'FindClose@kernel32.dll stdcall';
procedure CopyNativeFindData(var Search: TSafeFindRec);
var I: Integer;
begin
  Search.Name := ''; Search.Attributes := Search.Data.Attributes;
  for I := 0 to 259 do begin
    if Search.Data.FileName[I] = #0 then break;
    Search.Name := Search.Name + Search.Data.FileName[I];
  end;
end;
function SafeFindFirst(Name: String; var Search: TSafeFindRec): Boolean;
begin
  Search.Handle := NativeFindFirst(Name, Search.Data);
  Search.Error := DLLGetLastError;
  Result := Search.Handle <> THandle(-1);
  if Result then CopyNativeFindData(Search);
end;
function SafeFindNext(var Search: TSafeFindRec): Boolean;
begin
  Result := NativeFindNext(Search.Handle, Search.Data);
  Search.Error := DLLGetLastError;
  if Result then CopyNativeFindData(Search);
end;
procedure SafeFindClose(var Search: TSafeFindRec);
begin
  NativeFindClose(Search.Handle);
end;
function GetDataDirectory(Param: String): String;
begin
  if DataDirectoryPageReady then Result := DataDirectoryPage.Values[0]
  else Result := GetPreviousData('DataDirectory', ExpandConstant('{localappdata}\StudyCenter-Public\Data'));
end;
function NoReparseAncestors(Path: String): Boolean;
var Current, Parent: String; Attributes: LongWord; Error: LongInt;
begin
  Result := False; Current := RemoveBackslashUnlessRoot(ExpandFileName(Path));
  if (Length(Current) < 3) or (Current[2] <> ':') or (Current[3] <> '\') then exit;
  repeat
    Attributes := GetFileAttributesW(Current);
    if Attributes = $FFFFFFFF then begin
      Error := DLLGetLastError;
      if (Error <> 2) and (Error <> 3) then exit;
    end else if (Attributes and $400) <> 0 then exit;
    Parent := ExtractFileDir(Current);
    if (Parent = Current) or (Parent = '') then break;
    Current := RemoveBackslashUnlessRoot(Parent);
  until False;
  Result := True;
end;
function FolderHasEntries(Path: String): Boolean;
var Search: TSafeFindRec;
begin
  Result := False;
  if SafeFindFirst(AddBackslash(Path) + '*', Search) then begin
    try repeat
      if (Search.Name <> '.') and (Search.Name <> '..') then begin Result := True; break; end;
    until not SafeFindNext(Search);
    if Search.Error <> 18 then Result := True;
    finally SafeFindClose(Search); end;
  end else if (Search.Error <> 2) and (Search.Error <> 18) then Result := True;
end;
procedure SkipWhitespace(S: String; var Offset: Integer);
begin
  while Offset <= Length(S) do begin
    if Pos(S[Offset], ' ' + #9 + #10 + #13) = 0 then break;
    Offset := Offset + 1;
  end;
end;
function JSONString(S: String; var Offset: Integer; var Value: String): Boolean;
var Character: Char; Number, I, Digit: Integer;
begin
  Result := False; Value := ''; SkipWhitespace(S, Offset);
  if (Offset > Length(S)) or (S[Offset] <> '"') then exit;
  Offset := Offset + 1;
  while Offset <= Length(S) do begin
    Character := S[Offset]; Offset := Offset + 1;
    if Character = '"' then begin Result := True; exit; end;
    if Ord(Character) < 32 then exit;
    if Character = '\' then begin
      if Offset > Length(S) then exit;
      Character := S[Offset]; Offset := Offset + 1;
      if (Character <> '"') and (Character <> '\') and (Character <> '/') then begin
        if Character <> 'u' then exit;
        Number := 0;
        for I := 1 to 4 do begin
          if Offset > Length(S) then exit;
          Digit := Pos(Lowercase(S[Offset]), '0123456789abcdef') - 1;
          if Digit < 0 then exit;
          Number := Number * 16 + Digit; Offset := Offset + 1;
        end;
        if (Number < 32) or (Number > 127) then exit;
        Character := Chr(Number);
      end;
    end;
    Value := Value + Character;
  end;
end;
function ValidUUID(Value: String): Boolean;
var I: Integer;
begin
  Result := False;
  if Length(Value) <> 36 then exit;
  for I := 1 to 36 do begin
    if (I = 9) or (I = 14) or (I = 19) or (I = 24) then begin
      if Value[I] <> '-' then exit;
    end else if Pos(Lowercase(Value[I]), '0123456789abcdef') = 0 then exit;
  end;
  Result := True;
end;
function StrictPublicIdentity(Path: String): Boolean;
var Bytes: AnsiString; S, Key, Value: String; Offset, Seen: Integer;
begin
  Result := False;
  if not NoReparseAncestors(Path) or not LoadStringFromFile(Path, Bytes) then exit;
  S := String(Bytes); if (Length(S) = 0) or (Length(S) > 4096) then exit;
  Offset := 1; Seen := 0; SkipWhitespace(S, Offset);
  if (Offset > Length(S)) or (S[Offset] <> '{') then exit;
  Offset := Offset + 1;
  repeat
    if not JSONString(S, Offset, Key) then exit;
    SkipWhitespace(S, Offset);
    if (Offset > Length(S)) or (S[Offset] <> ':') then exit;
    Offset := Offset + 1;
    if not JSONString(S, Offset, Value) then exit;
    if Key = 'application' then begin
      if ((Seen and 1) <> 0) or (Value <> OwnApplication) then exit;
      Seen := Seen or 1;
    end else if Key = 'instance_id' then begin
      if ((Seen and 2) <> 0) or not ValidUUID(Value) then exit;
      Seen := Seen or 2;
    end else if Key = 'host_id' then begin
      if Pos('urn:uuid:', Value) = 1 then Delete(Value, 1, 9);
      if ((Seen and 4) <> 0) or not ValidUUID(Value) then exit;
      Seen := Seen or 4;
    end else exit;
    SkipWhitespace(S, Offset);
    if Offset > Length(S) then exit;
    if S[Offset] = '}' then break;
    if S[Offset] <> ',' then exit;
    Offset := Offset + 1;
  until False;
  Offset := Offset + 1; SkipWhitespace(S, Offset);
  Result := (Seen = 7) and (Offset > Length(S));
end;
function OwnInventoryTree(Directory, Base: String; Lines: TArrayOfString; Verify: Boolean; var Output: String): Boolean;
var Search: TSafeFindRec; Path, Relative, Digest, Entry: String; I: Integer; Found: Boolean;
begin
  Result := False;
  if not NoReparseAncestors(Directory) then exit;
  if SafeFindFirst(AddBackslash(Directory) + '*', Search) then begin
    try repeat
      if (Search.Name <> '.') and (Search.Name <> '..') then begin
        Path := AddBackslash(Directory) + Search.Name;
        if not NoReparseAncestors(Path) then exit;
        if Search.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0 then begin
          if not OwnInventoryTree(Path, Base, Lines, Verify, Output) then exit;
        end else begin
          Relative := Copy(Path, Length(AddBackslash(Base)) + 1, MaxInt);
          if (CompareText(Relative, 'installation-files.txt') <> 0) and
             (CompareText(Relative, 'installation-marker.ini') <> 0) and
             (CompareText(Relative, 'unins000.dat') <> 0) and
             (CompareText(Relative, 'unins000.msg') <> 0) then begin
            Digest := GetSHA256OfFile(Path); Entry := Digest + '|' + Relative;
            if Verify then begin
              Found := False;
              for I := 0 to GetArrayLength(Lines) - 1 do
                if CompareText(Lines[I], Entry) = 0 then Found := True;
              if not Found then exit;
            end else Output := Output + Entry + #13#10;
          end;
        end;
      end;
    until not SafeFindNext(Search);
    if Search.Error <> 18 then exit;
    finally SafeFindClose(Search); end;
  end else if (Search.Error <> 2) and (Search.Error <> 18) then exit;
  Result := True;
end;
function ProgramDirectoryProblem(Directory, DataDirectory: String; AllowEmpty: Boolean): String;
var Marker, Inventory, Registered, Output, Entry, Relative: String; Lines: TArrayOfString; I, Separator: Integer;
begin
  Result := 'The program folder is not an unchanged installation of StudyCenter-Public. Choose an empty local folder.';
  if not NoReparseAncestors(Directory) then begin Result := 'The program path contains a reparse point or inaccessible ancestor.'; exit; end;
  if not DirExists(Directory) or not FolderHasEntries(Directory) then begin
    if AllowEmpty then Result := '';
    exit;
  end;
  Marker := AddBackslash(Directory) + 'installation-marker.ini';
  Inventory := AddBackslash(Directory) + 'installation-files.txt';
  if not NoReparseAncestors(Marker) or not NoReparseAncestors(Inventory) then exit;
  if not RegQueryStringValue(HKCU, OwnUninstallKey, 'InstallLocation', Registered) then exit;
  if CompareText(RemoveBackslashUnlessRoot(ExpandFileName(Registered)), RemoveBackslashUnlessRoot(ExpandFileName(Directory))) <> 0 then exit;
  if GetIniString('identity', 'application', '', Marker) <> OwnApplication then exit;
  if CompareText(GetIniString('identity', 'data_directory', '', Marker), ExpandFileName(DataDirectory)) <> 0 then exit;
  if GetIniString('identity', 'inventory_sha256', '', Marker) <> GetSHA256OfFile(Inventory) then exit;
  if not LoadStringsFromFile(Inventory, Lines) then exit;
  for I := 0 to GetArrayLength(Lines) - 1 do begin
    Entry := Lines[I]; Separator := Pos('|', Entry);
    if Separator <> 65 then exit;
    Relative := Copy(Entry, Separator + 1, MaxInt);
    if (Relative = '') or (Pos(':', Relative) > 0) or (Pos('..', Relative) > 0) or (Relative[1] = '\') then exit;
    if not NoReparseAncestors(AddBackslash(Directory) + Relative) or not FileExists(AddBackslash(Directory) + Relative) then exit;
    if CompareText(Copy(Entry, 1, 64), GetSHA256OfFile(AddBackslash(Directory) + Relative)) <> 0 then exit;
  end;
  Output := '';
  if not OwnInventoryTree(Directory, Directory, Lines, True, Output) then exit;
  if not FileExists(AddBackslash(Directory) + 'StudyCenter-Public.exe') then exit;
  Result := '';
end;
procedure InitializeWizard;
begin
  DataDirectoryPage := CreateInputDirPage(wpSelectDir, 'User data folder',
    'Choose where your originals and personal decisions are stored.',
    'The program and user data need separate folders. Updates and uninstall keep this folder.', False, '');
  DataDirectoryPage.Add('');
  DataDirectoryPage.Values[0] := ExpandConstant('{param:DATADIR|' + GetPreviousData('DataDirectory', ExpandConstant('{localappdata}\StudyCenter-Public\Data')) + '}');
  DataDirectoryPageReady := True;
end;
procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'DataDirectory', GetDataDirectory(''));
end;
function DataDirectoryProblem(DataDirectory, ProgramDirectory: String; ProbeAccess: Boolean): String;
var NormalData, NormalProgram, Probe: String;
begin
  Result := ''; DataDirectory := ExpandFileName(DataDirectory);
  NormalData := Lowercase(AddBackslash(DataDirectory));
  NormalProgram := Lowercase(AddBackslash(ExpandFileName(ProgramDirectory)));
  if (Pos(NormalProgram, NormalData) = 1) or (Pos(NormalData, NormalProgram) = 1) then begin Result := 'Choose user data outside the program folder.'; exit; end;
  if (Pos('\\', NormalData) = 1) or (Pos('\onedrive', NormalData) > 0) or (Pos('\dropbox', NormalData) > 0) or (Pos('\google drive', NormalData) > 0) then begin Result := 'Choose a local folder outside cloud synchronisation.'; exit; end;
  if not NoReparseAncestors(DataDirectory) then begin Result := 'The data path contains a reparse point or inaccessible ancestor.'; exit; end;
  if FileExists(AddBackslash(DataDirectory) + 'instance.json') then begin
    if not StrictPublicIdentity(AddBackslash(DataDirectory) + 'instance.json') then begin Result := 'The selected data folder is not a valid Public instance.'; exit; end;
  end else if DirExists(DataDirectory) and FolderHasEntries(DataDirectory) then begin Result := 'The selected data folder contains unrelated files. Choose an empty folder or a valid Public instance.'; exit; end;
  if not NoReparseAncestors(AddBackslash(DataDirectory) + 'runtime.json') or
     not NoReparseAncestors(AddBackslash(DataDirectory) + 'studycenter-public.sqlite3') or
     not NoReparseAncestors(AddBackslash(DataDirectory) + 'backups') then begin Result := 'The selected data folder contains redirected application files.'; exit; end;
  if not ProbeAccess then exit;
  if not ForceDirectories(DataDirectory) then begin Result := 'The selected data folder cannot be created.'; exit; end;
  Probe := AddBackslash(DataDirectory) + '.studycenter-public-write-check-' + GetDateTimeString('yyyymmddhhnnsszzz', '', '');
  if FileExists(Probe) then begin Result := 'Temporary access-check filename already exists. Retry.'; exit; end;
  if not SaveStringToFile(Probe, 'Synthetic installation access check', False) then begin Result := 'The selected data folder is read-only.'; exit; end;
  if not DeleteFile(Probe) then Result := 'The selected data folder does not permit normal file removal.';
end;
function NextButtonClick(CurPageID: Integer): Boolean;
var Problem: String;
begin
  Result := True;
  { Script MsgBox is not suppressed by /SUPPRESSMSGBOXES. Silent installs
    always reach the identical mandatory PrepareToInstall preflight. }
  if WizardSilent then exit;
  if DataDirectoryPageReady and (CurPageID = DataDirectoryPage.ID) then begin
    Problem := ProgramDirectoryProblem(WizardDirValue, GetDataDirectory(''), True);
    if Problem = '' then Problem := DataDirectoryProblem(GetDataDirectory(''), WizardDirValue, False);
    if Problem <> '' then begin SuppressibleMsgBox(Problem, mbError, MB_OK, IDOK); Result := False; end;
  end;
end;
function PrepareToInstall(var NeedsRestart: Boolean): String;
var ResultCode, Attempt: Integer; DataDirectory, DatabaseFile, BackupDirectory: String;
begin
  Result := SystemRuntimeProblem;
  if Result <> '' then exit;
  DataDirectory := GetDataDirectory('');
  Result := ProgramDirectoryProblem(ExpandConstant('{app}'), DataDirectory, True);
  if Result <> '' then exit;
  Result := DataDirectoryProblem(DataDirectory, ExpandConstant('{app}'), True);
  if Result <> '' then exit;
  if FileExists(ExpandConstant('{app}\StudyCenter-Public.exe')) then begin
    if not Exec(ExpandConstant('{app}\StudyCenter-Public.exe'), '--quit --data-dir "' + DataDirectory + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then begin Result := 'Close StudyCenter-Public before updating.'; exit; end;
    for Attempt := 1 to 100 do begin
      if not FileExists(AddBackslash(DataDirectory) + 'runtime.json') then break;
      Sleep(100);
    end;
    if FileExists(AddBackslash(DataDirectory) + 'runtime.json') then begin Result := 'The selected Public instance is still running. Close it before updating.'; exit; end;
    DatabaseFile := AddBackslash(DataDirectory) + 'studycenter-public.sqlite3';
    if FileExists(DatabaseFile) then begin
      BackupDirectory := AddBackslash(DataDirectory) + 'backups\before-update-' + GetDateTimeString('yyyymmddhhnnsszzz', '', '');
      if not NoReparseAncestors(BackupDirectory) or not ForceDirectories(BackupDirectory) or not FileCopy(DatabaseFile, AddBackslash(BackupDirectory) + 'studycenter-public.sqlite3', True) then
        Result := 'A backup of the existing database could not be created. Update cancelled; keep the current installation.';
    end;
  end;
end;
procedure CurStepChanged(CurStep: TSetupStep);
var Lines: TArrayOfString; Output, Marker, Inventory: String;
begin
  if CurStep <> ssPostInstall then exit;
  Output := '';
  if not OwnInventoryTree(ExpandConstant('{app}'), ExpandConstant('{app}'), Lines, False, Output) then RaiseException('Installed file inventory could not be verified.');
  Inventory := ExpandConstant('{app}\installation-files.txt'); Marker := ExpandConstant('{app}\installation-marker.ini');
  if not SaveStringToFile(Inventory, Output, False) then RaiseException('Installed file inventory could not be saved.');
  if not SetIniString('identity', 'application', OwnApplication, Marker) or
     not SetIniString('identity', 'data_directory', ExpandFileName(GetDataDirectory('')), Marker) or
     not SetIniString('identity', 'inventory_sha256', GetSHA256OfFile(Inventory), Marker) then RaiseException('Installation identity could not be saved.');
end;
function InitializeUninstall: Boolean;
var ResultCode, Attempt: Integer; DataDirectory, Problem: String;
begin
  Result := False; DataDirectory := GetDataDirectory('');
  Problem := ProgramDirectoryProblem(ExpandConstant('{app}'), DataDirectory, False);
  if Problem = '' then Problem := DataDirectoryProblem(DataDirectory, ExpandConstant('{app}'), False);
  if Problem <> '' then begin SuppressibleMsgBox(Problem, mbError, MB_OK, IDOK); exit; end;
  if not Exec(ExpandConstant('{app}\StudyCenter-Public.exe'), '--quit --data-dir "' + DataDirectory + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then begin SuppressibleMsgBox('Close StudyCenter-Public before uninstalling.', mbError, MB_OK, IDOK); exit; end;
  for Attempt := 1 to 100 do begin
    if not FileExists(AddBackslash(DataDirectory) + 'runtime.json') then break;
    Sleep(100);
  end;
  if FileExists(AddBackslash(DataDirectory) + 'runtime.json') then begin SuppressibleMsgBox('The selected Public instance is still running. Close it before uninstalling.', mbError, MB_OK, IDOK); exit; end;
  Result := True;
end;
