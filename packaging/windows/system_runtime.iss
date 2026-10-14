{ Read-only x64 PE/export prerequisite validation. No DLL loading or copying. }
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
function RuntimeHasExport(const Image: AnsiString; Symbol: String): Boolean;
var PE, Directory, Functions, Names, Ordinals, I, OrdinalIndex, Index: Integer;
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
  for I := 0 to NameCount - 1 do begin
    if RuntimeASCII(Image, RuntimeOffset(Image, PE, RuntimeU32(Image, Names + I * 4))) = Symbol then begin
      OrdinalIndex := RuntimeU16(Image, Ordinals + I * 2);
      if LongWord(OrdinalIndex) >= Count then exit;
      Result := RuntimeU32(Image, Functions + OrdinalIndex * 4) <> 0; exit;
    end;
  end;
end;
function SystemRuntimeProblem: String;
var Remaining, Line, Name, Symbol, Previous, Path: String; Image: AnsiString;
  Cut, Separator: Integer; VersionMS, VersionLS: Cardinal;
begin
  Result := ''; Remaining := SystemRuntimeRequirements; Previous := '';
  try
    while Remaining <> '' do begin
      Cut := Pos(#10, Remaining);
      if Cut = 0 then begin Line := Remaining; Remaining := ''; end
      else begin Line := Copy(Remaining, 1, Cut - 1); Delete(Remaining, 1, Cut); end;
      Separator := Pos('|', Line);
      if Separator = 0 then RaiseException('Invalid packaged runtime requirement');
      Name := Copy(Line, 1, Separator - 1); Symbol := Copy(Line, Separator + 1, Length(Line));
      if (Pos('\', Name) <> 0) or (Pos('/', Name) <> 0) or (Pos(':', Name) <> 0) or (Pos('..', Name) <> 0) then RaiseException('Invalid system library name');
      if Name <> Previous then begin
        { Setup is explicitly x86 on both compiler versions. Sysnative reaches
          the authoritative x64 System32 without changing redirection state. }
        Path := ExpandConstant('{sysnative}\') + Name;
        if not LoadStringFromFile(Path, Image) then RaiseException('Missing readable x64 system library: ' + Name);
        if (Pos('msvcp', Name) = 1) or (Pos('vcruntime', Name) = 1) or (Pos('concrt', Name) = 1) then begin
          if not GetVersionNumbers(Path, VersionMS, VersionLS) or (VersionMS shr 16 <> 14) then RaiseException('Microsoft V14 x64 system runtime required: ' + Name);
        end;
        Previous := Name;
      end;
      if not RuntimeHasExport(Image, Symbol) then RaiseException('System library lacks a required x64 export: ' + Name + ' / ' + Symbol);
    end;
  except
    Result := 'StudyCenter-Public requires an already installed compatible Microsoft Visual C++ V14 x64 runtime and Windows 10/11 system libraries. ' + GetExceptionMessage + #13#10 +
      'Visual Studio is not required. No runtime is installed or changed by this setup. See QUICKSTART.md and the official Microsoft runtime download: https://aka.ms/vc14/vc_redist.x64.exe';
  end;
end;
