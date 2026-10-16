{ Read-only x64 PE/export prerequisite validation. No DLL loading or copying. }
const
  { Official Microsoft V14 x64 14.51.36247.0. Downloaded by the end user;
    neither the installer nor individual Microsoft DLLs are redistributed. }
  VCRuntimeURL = 'https://download.visualstudio.microsoft.com/download/pr/ebdab8e5-1d7b-4d9f-a11b-cbb1720c3b12/843068991DAAA1F73AD9F6239BCE4D0F6A07A51F18C37EA2A867E9BECA71295C/VC_redist.x64.exe';
  VCRuntimeSHA256 = '843068991daaa1f73ad9f6239bce4d0f6a07a51f18c37ea2a867e9beca71295c';
  VCRuntimeFile = 'StudyCenter-Public-VC_redist.x64.exe';
function RuntimeU16(const Image: AnsiString; Offset: Integer): LongWord;
begin
  if (Offset < 0) or (Offset > Length(Image) - 2) then RaiseException('Truncated system PE header');
  Result := Ord(Image[Offset + 1]) + Ord(Image[Offset + 2]) * 256;
end;
function RuntimeU32(const Image: AnsiString; Offset: Integer): LongWord;
begin
  Result := RuntimeU16(Image, Offset) + RuntimeU16(Image, Offset + 2) * 65536;
end;
function RuntimeOffset(const Image: AnsiString; PE: Integer; RVA: LongWord): Integer;
var Count, Header, I, Section: Integer; Address, Size, Raw, Delta: LongWord;
begin
  Result := -1; Count := RuntimeU16(Image, PE + 6);
  Header := PE + 24 + RuntimeU16(Image, PE + 20);
  if (Count < 1) or (Count > 96) then RaiseException('Invalid system PE section count');
  for I := 0 to Count - 1 do begin
    Section := Header + I * 40; Address := RuntimeU32(Image, Section + 12);
    Size := RuntimeU32(Image, Section + 16); Raw := RuntimeU32(Image, Section + 20);
    if RVA >= Address then begin
      Delta := RVA - Address;
      if (Delta < Size) and (Raw < LongWord(Length(Image))) and (Delta < LongWord(Length(Image)) - Raw) then begin
        Result := Raw + Delta; exit;
      end;
    end;
  end;
  RaiseException('System PE export points outside file');
end;
function RuntimeASCII(const Image: AnsiString; Offset: Integer): String;
var I, Value: Integer;
begin
  Result := '';
  for I := 0 to 4095 do begin
    if (Offset < 0) or (Offset + I >= Length(Image)) then break;
    Value := Ord(Image[Offset + I + 1]);
    if Value = 0 then exit;
    if (Value < 32) or (Value > 126) then break;
    Result := Result + Chr(Value);
  end;
  RaiseException('Invalid system PE export name');
end;
function RuntimeCompareASCII(Left, Right: String): Integer;
var I, Count: Integer;
begin
  Count := Length(Left); if Length(Right) < Count then Count := Length(Right);
  for I := 1 to Count do begin
    Result := Ord(Left[I]) - Ord(Right[I]); if Result <> 0 then exit;
  end;
  Result := Length(Left) - Length(Right);
end;
function RuntimeHasExport(const Image: AnsiString; Symbol: String): Boolean;
var PE, Directory, Functions, Names, Ordinals, OrdinalIndex, Index, Low, High, Middle, Comparison: Integer;
  ExportName: String;
  Count, NameCount, Base: LongWord;
begin
  Result := False;
  if (Length(Image) < 64) or (Copy(Image, 1, 2) <> 'MZ') then exit;
  if RuntimeU32(Image, 60) > LongWord(Length(Image) - 24) then exit;
  PE := RuntimeU32(Image, 60);
  if (Copy(Image, PE + 1, 4) <> 'PE' + #0 + #0) or
     (RuntimeU16(Image, PE + 4) <> $8664) or (RuntimeU16(Image, PE + 24) <> $20B) then exit;
  if (RuntimeU16(Image, PE + 20) < 120) or (RuntimeU32(Image, PE + 132) < 1) then exit;
  if RuntimeU32(Image, PE + 136) = 0 then exit;
  Directory := RuntimeOffset(Image, PE, RuntimeU32(Image, PE + 136));
  Base := RuntimeU32(Image, Directory + 16); Count := RuntimeU32(Image, Directory + 20);
  NameCount := RuntimeU32(Image, Directory + 24);
  if (Count = 0) or (Count > 1000000) or (NameCount > Count) then exit;
  Functions := RuntimeOffset(Image, PE, RuntimeU32(Image, Directory + 28));
  if Copy(Symbol, 1, 1) = '#' then begin
    Index := StrToIntDef(Copy(Symbol, 2, Length(Symbol)), -1);
    if (Index < 0) or (LongWord(Index) < Base) or (LongWord(Index) - Base >= Count) then exit;
    Result := RuntimeU32(Image, Functions + (LongWord(Index) - Base) * 4) <> 0; exit;
  end;
  if NameCount = 0 then exit;
  Names := RuntimeOffset(Image, PE, RuntimeU32(Image, Directory + 32));
  Ordinals := RuntimeOffset(Image, PE, RuntimeU32(Image, Directory + 36));
  { The PE export name pointer table is sorted by bytewise ASCII name.
    Binary search preserves exact case, bounds and ordinal checks. }
  Low := 0; High := NameCount - 1;
  while Low <= High do begin
    Middle := Low + (High - Low) div 2;
    ExportName := RuntimeASCII(Image, RuntimeOffset(Image, PE, RuntimeU32(Image, Names + Middle * 4)));
    Comparison := RuntimeCompareASCII(ExportName, Symbol);
    if Comparison = 0 then begin
      OrdinalIndex := RuntimeU16(Image, Ordinals + Middle * 2);
      if LongWord(OrdinalIndex) >= Count then exit;
      Result := RuntimeU32(Image, Functions + OrdinalIndex * 4) <> 0; exit;
    end;
    if Comparison < 0 then Low := Middle + 1 else High := Middle - 1;
  end;
end;
function IsVCRuntimeLibrary(Name: String): Boolean;
begin
  Name := Lowercase(Name);
  Result := (Name = 'msvcp140.dll') or (Name = 'msvcp140_1.dll') or
    (Name = 'vcruntime140.dll') or (Name = 'vcruntime140_1.dll') or
    (Name = 'concrt140.dll');
end;
function SystemRuntimeGroupProblem(VCGroup: Boolean): String;
var Remaining, Line, Name, Symbol, Previous, Path: String; Image: AnsiString;
  Cut, Separator: Integer; VersionMS, VersionLS: Cardinal;
begin
  Result := ''; Remaining := SystemRuntimeRequirements; Previous := '';
  Log('System runtime prerequisite: begin');
  try
    while Remaining <> '' do begin
      Cut := Pos(#10, Remaining);
      if Cut = 0 then begin Line := Remaining; Remaining := ''; end
      else begin Line := Copy(Remaining, 1, Cut - 1); Delete(Remaining, 1, Cut); end;
      Separator := Pos('|', Line);
      if Separator = 0 then RaiseException('Invalid packaged runtime requirement');
      Name := Copy(Line, 1, Separator - 1); Symbol := Copy(Line, Separator + 1, Length(Line));
      if (Pos('\', Name) <> 0) or (Pos('/', Name) <> 0) or (Pos(':', Name) <> 0) or (Pos('..', Name) <> 0) then RaiseException('Invalid system library name');
      if IsVCRuntimeLibrary(Name) = VCGroup then begin
        if Name <> Previous then begin
          Log('System runtime prerequisite library: ' + Name);
          { Setup is explicitly x86 on both compiler versions. Sysnative reaches
            the authoritative x64 System32 without changing redirection state. }
          Path := ExpandConstant('{sysnative}\') + Name;
          if not LoadStringFromFile(Path, Image) then RaiseException('Missing readable x64 system library: ' + Name);
          if VCGroup then begin
            if not GetVersionNumbers(Path, VersionMS, VersionLS) or (VersionMS shr 16 <> 14) then RaiseException('Microsoft V14 x64 system runtime required: ' + Name);
          end;
          Previous := Name;
        end;
        if not RuntimeHasExport(Image, Symbol) then RaiseException('System library lacks a required x64 export: ' + Name + ' / ' + Symbol);
      end;
    end;
    Log('System runtime prerequisite: complete');
  except
    Result := 'StudyCenter-Public requires compatible Microsoft Visual C++ V14 x64 and Windows 10/11 system libraries. ' + GetExceptionMessage;
  end;
end;
function SystemRuntimeProblem: String;
begin
  Result := SystemRuntimeGroupProblem(False);
  if Result = '' then Result := SystemRuntimeGroupProblem(True);
end;
function CmdLineParamExists(Parameter: String): Boolean;
var I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Parameter) = 0 then begin Result := True; exit; end;
end;
function EnsureSystemRuntime: String;
var ResultCode: Integer; Progress: TOutputProgressWizardPage;
begin
  { Windows defects are never a reason to acquire the VC runtime. }
  Result := SystemRuntimeGroupProblem(False);
  if Result <> '' then exit;
  Result := SystemRuntimeGroupProblem(True);
  if Result = '' then exit;
  if WizardSilent and not CmdLineParamExists('/INSTALLPREREQUISITES') then begin
    Result := Result + #13#10 + 'The Microsoft prerequisite is missing. Rerun interactively or explicitly allow its download and visible Microsoft installer with /INSTALLPREREQUISITES. Microsoft license acceptance and administrator approval are required.';
    exit;
  end;
  if not WizardSilent then begin
    if MsgBox('A compatible Microsoft Visual C++ V14 x64 runtime is missing.' + #13#10 +
      'Setup will download the official Microsoft installer (about 18 MB). Accept Microsoft license terms in its window and approve Windows administrator access to continue. No automatic restart will occur.' + #13#10 +
      'Download and open the Microsoft installer now?', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) <> IDYES then begin
      Result := 'Microsoft prerequisite installation was declined. StudyCenter-Public Setup stopped before writing application files.'; exit;
    end;
  end;
  try
    Progress := CreateOutputProgressPage('Microsoft runtime prerequisite', 'Download from Microsoft');
    Progress.SetText('Downloading and verifying Microsoft Visual C++ V14 x64...', 'The Microsoft license and administrator prompt follow.');
    Progress.Show;
    try
      DownloadTemporaryFile(VCRuntimeURL, VCRuntimeFile, VCRuntimeSHA256, nil);
    finally
      Progress.Hide;
    end;
    if not ShellExec('runas', ExpandConstant('{tmp}\') + VCRuntimeFile, '/install /norestart', '',
      SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode) then begin
      Result := 'Microsoft prerequisite could not start or administrator approval was cancelled: ' + SysErrorMessage(ResultCode); exit;
    end;
    Log('Microsoft VC prerequisite exit code: ' + IntToStr(ResultCode));
    if ResultCode = 3010 then begin
      Result := 'Microsoft requires a Windows restart. Restart manually, then rerun StudyCenter-Public Setup. Setup stopped before writing application files.'; exit;
    end;
    if ResultCode <> 0 then begin
      Result := 'Microsoft prerequisite was cancelled or failed (exit code ' + IntToStr(ResultCode) + '). StudyCenter-Public Setup stopped before writing application files.'; exit;
    end;
    { Success is determined by the same complete x64/export validation, not
      the child exit code or a registry version alone. }
    Result := SystemRuntimeProblem;
    if Result <> '' then Result := 'Microsoft prerequisite did not satisfy all required exports. ' + Result;
  except
    Result := 'Microsoft prerequisite download, hash verification or installation failed. Check your Internet connection and retry Setup. ' + GetExceptionMessage;
  end;
end;
