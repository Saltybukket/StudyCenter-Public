"""Voluntary handoff to a verified release; never owns user data."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

from .platform.paths import atomic_json, reject_links, restrict_permissions


class UpdateInstallError(ValueError):
    pass


def _digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _read_windows_marker(path):
    """Use the same Win32 INI interpretation as Inno, including ANSI files."""
    import ctypes
    from ctypes import wintypes
    read = ctypes.WinDLL('kernel32', use_last_error=True).GetPrivateProfileStringW
    read.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                     wintypes.LPWSTR, wintypes.DWORD, wintypes.LPCWSTR]
    read.restype = wintypes.DWORD
    values = {}
    for name in ('application', 'data_directory', 'inventory_sha256'):
        buffer = ctypes.create_unicode_buffer(32768)
        count = read('identity', name, '', buffer, len(buffer), str(path))
        if count >= len(buffer)-1:
            raise UpdateInstallError('Installationskennung ist zu lang.')
        values[name] = buffer.value
    return values


def _root():
    if getattr(sys, 'frozen', False):
        return reject_links(Path(sys.executable).parent)
    configured = os.environ.get('STUDYCENTER_PUBLIC_INSTALL_ROOT')
    if not configured:
        raise UpdateInstallError('Quellcode-Checkout oder nicht verwaltete Installation: bitte manuell aktualisieren.')
    return reject_links(Path(configured))


def _layout(paths):
    root = _root()
    data = reject_links(paths.data)
    if root == data or root.is_relative_to(data) or data.is_relative_to(root):
        raise UpdateInstallError('Programm und Daten müssen in getrennten Ordnern liegen.')
    explicit = paths.config == data/'config' and paths.cache == data/'cache'
    if not explicit:
        if sys.platform == 'win32':
            base = Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData/Local'))/'StudyCenter-Public'
            expected = (base/'Data', base/'Config', base/'Cache')
        else:
            expected = (Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))/'studycenter-public',
                        Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))/'studycenter-public',
                        Path(os.environ.get('XDG_CACHE_HOME', Path.home()/'.cache'))/'studycenter-public')
        if tuple(map(Path, (paths.data, paths.config, paths.cache))) != expected:
            raise UpdateInstallError('Diese Speicherpfade können beim Neustart nicht sicher erhalten werden.')
        if sys.platform == 'win32':
            raise UpdateInstallError('Diese Installation nutzt getrennte Standardordner; bitte manuell aktualisieren.')
    if not root.is_dir() or not os.access(root, os.W_OK) or not os.access(root.parent, os.W_OK):
        raise UpdateInstallError('Der Programmordner ist nicht beschreibbar; bitte manuell aktualisieren.')
    if sys.platform == 'win32':
        marker = reject_links(root/'installation-marker.ini')
        inventory = reject_links(root/'installation-files.txt')
        if not marker.is_file() or not inventory.is_file():
            raise UpdateInstallError('Keine verwaltete Windows-Installation erkannt.')
        ini = _read_windows_marker(marker)
        if (ini['application'] != 'StudyCenter.Public' or
            os.path.normcase(os.path.normpath(ini['data_directory'])) != os.path.normcase(str(data)) or
            ini['inventory_sha256'].lower() != _digest(inventory)):
            raise UpdateInstallError('Installationskennung oder Datenordner stimmt nicht überein.')
        import winreg
        keyname = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B60E9F91-11F3-4EA8-B973-5C6D65C7A59A}_is1'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, keyname) as key:
            registered = winreg.QueryValueEx(key, 'InstallLocation')[0]
        if os.path.normcase(os.path.normpath(registered)) != os.path.normcase(str(root)):
            raise UpdateInstallError('Die Installation ist nicht für diesen Ordner registriert.')
        if (root/'start.cmd').is_file():
            for name in ('installation.json', 'python-path.txt'):
                if not reject_links(root/'.local'/name).is_file():
                    raise UpdateInstallError('Die vorhandene Windows-Umgebungskennung fehlt: '+name)
        if not (root/'start.cmd').is_file() and not (root/'StudyCenter-Public.exe').is_file():
            raise UpdateInstallError('Der vorhandene Programmstarter fehlt.')
        return root, explicit, None
    if sys.platform != 'linux':
        raise UpdateInstallError('Automatische Installation wird auf diesem Betriebssystem nicht unterstützt.')
    for candidate in (root/'.local/update-recover.py', root/'.local/update-recover.py.tmp',
                      root/'.local/update-recovery.lock', root/'.local/update-journal.json',
                      root/'.local/update-journal.tmp', root/'.local/update-recovery-python.txt',
                      root/'.local/update-recovery-python.tmp'):
        reject_links(candidate)
    for name in ('start.sh', 'LICENSE', 'README.md', 'setup.py', 'backup_core.py', 'slim-manifest.json'):
        reject_links(root/('.update-'+name+'.tmp'))
    if (root/'.local/update-journal.json').exists():
        raise UpdateInstallError('Ein unterbrochenes Update muss zuerst durch einen Neustart wiederhergestellt werden.')
    receipt_path = reject_links(root/'.local/installation.json')
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    environment = reject_links(Path(receipt['environment']))
    python = Path(receipt['python']).absolute()
    uv = reject_links(Path(receipt['uv']))
    if uv != root/'.local/tools/uv-0.8.22/uv':
        raise UpdateInstallError('Das Setup-Werkzeug liegt nicht in dieser Installation.')
    if (receipt.get('schema') != 1 or receipt.get('application') != 'StudyCenter.Public' or
        Path(receipt['root']).absolute() != root or receipt.get('source') or
        not environment.is_relative_to(root/'.local/environments') or python != environment/'bin/python' or
        not python.is_file() or not (root/'setup.py').is_file() or not (root/'slim-manifest.json').is_file()):
        raise UpdateInstallError('Keine verwaltete Linux-Slim-Installation erkannt; bitte manuell aktualisieren.')
    if not hasattr(os, 'pidfd_open'):
        raise UpdateInstallError('Ein sicherer Neustart benötigt Linux-pidfd-Unterstützung.')
    return root, explicit, receipt


def installation_support(paths):
    try:
        _layout(paths)
        return True, ''
    except (OSError, ValueError, KeyError, TypeError) as error:
        return False, str(error)


def _extract_slim(archive, destination):
    allowed = {'start.sh', 'LICENSE', 'README.md', 'setup.py', 'backup_core.py', 'slim-manifest.json'}
    seen = set()
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            name = PurePosixPath(member.name)
            if len(name.parts) != 2 or name.parts[0] != 'StudyCenter-Public' or name.parts[1] not in allowed or name.parts[1] in seen or not member.isfile():
                raise UpdateInstallError('Das Linux-Archiv enthält einen unerwarteten oder verlinkten Eintrag.')
            if member.size > 16 * 1024 * 1024:
                raise UpdateInstallError('Ein Archiveintrag ist unerwartet groß.')
            seen.add(name.parts[1])
            with tar.extractfile(member) as source, (destination/name.parts[1]).open('xb') as target:
                shutil.copyfileobj(source, target)
            (destination/name.parts[1]).chmod(0o755 if name.parts[1] == 'start.sh' else 0o644)
    if seen != allowed:
        raise UpdateInstallError('Das Linux-Slim-Archiv ist unvollständig.')


def prepare_handoff(paths, artifact_info):
    root, explicit, receipt = _layout(paths)
    expected_platform = 'windows' if sys.platform == 'win32' else 'linux'
    if artifact_info.get('installer_compatible') is not True:
        raise UpdateInstallError('Für dieses Update fehlt der bestätigte Installationsvertrag; bitte manuell aktualisieren.')
    if artifact_info.get('platform') != expected_platform+'-x86_64':
        raise UpdateInstallError('Das Update gehört zu einem anderen Betriebssystem.')
    if expected_platform == 'windows' and artifact_info.get('installer_update_lock') is not True:
        raise UpdateInstallError('Dieser Installer weist den Schutz für feste Update-Ordner nicht aus; bitte manuell aktualisieren.')
    expected = artifact_info.get('sha256', '').lower()
    if len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
        raise UpdateInstallError('Ungültige Update-Prüfsumme.')
    source = reject_links(Path(artifact_info['path']))
    if not source.is_file() or _digest(source) != expected:
        raise UpdateInstallError('Update-Prüfsumme stimmt nicht überein.')
    stage = Path(tempfile.mkdtemp(prefix='.studycenter-update-', dir=root.parent))
    restrict_permissions(stage)
    artifact = stage/('setup.exe' if expected_platform == 'windows' else 'release.tar.gz')
    shutil.copyfile(source, artifact)
    if _digest(artifact) != expected:
        raise UpdateInstallError('Staged Update-Prüfsumme stimmt nicht überein.')
    if expected_platform == 'linux':
        unpacked = stage/'release'; unpacked.mkdir()
        _extract_slim(artifact, unpacked)
        if b'update-recover.py' not in (unpacked/'start.sh').read_bytes():
            raise UpdateInstallError('Dieses Linux-Update unterstützt die Wiederherstellung noch nicht; bitte manuell aktualisieren.')
        for filename in ('start.sh', 'LICENSE', 'README.md', 'setup.py', 'backup_core.py', 'slim-manifest.json'):
            old = reject_links(root/filename)
            if not old.is_file():
                raise UpdateInstallError('Bisherige Linux-Slim-Datei fehlt: '+filename)
    result = {'platform': expected_platform, 'root': str(root), 'data': str(paths.data),
              'explicit': explicit, 'stage': str(stage), 'artifact': str(artifact), 'sha256': expected,
              'receipt': receipt, 'status_file': str(reject_links(paths.config/'update-handoff.json')), 'port': 8767, 'files': ({p.name: _digest(p) for p in unpacked.iterdir()} if expected_platform == 'linux' else {})}
    try:
        from urllib.parse import urlsplit
        url = json.loads((Path(paths.data)/'runtime.json').read_text(encoding='utf-8'))['url']
        parsed = urlsplit(url)
        if parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and parsed.port:
            result['port'] = parsed.port
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return result

_LINUX_HELPER = r'''import fcntl, hashlib, json, os, pathlib, runpy, select, shutil, subprocess, sys, time
p=pathlib.Path(sys.argv[1]); d=json.loads(p.read_text(encoding='utf-8')); root=pathlib.Path(d['root']); stage=pathlib.Path(d['stage']); artifact=pathlib.Path(d['artifact']); release=stage/'release'; fd=int(sys.argv[2]); ready=stage/'ready'; outcome=stage/'outcome.json'
def digest(path):
    with open(path,'rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()
def write(status, detail=''):
    outcome.write_text(json.dumps({'status':status,'detail':detail},ensure_ascii=False),encoding='utf-8')
def relaunch():
    receipt=json.loads((root/'.local/installation.json').read_text(encoding='utf-8'))
    args=[receipt['python'],'-I','-m','studycenter_public','--port',str(d['port'])]
    if d['explicit']: args += ['--data-dir',d['data']]
    env=os.environ.copy(); env['STUDYCENTER_PUBLIC_INSTALL_ROOT']=str(root); env['STUDYCENTER_PUBLIC_COMPONENT_ROOT']=str(root/'.local/components')
    subprocess.Popen(args,cwd=root,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)
try:
    if digest(artifact)!=d['sha256']: raise ValueError('Prüfsumme geändert')
    for name, value in d['files'].items():
        if digest(release/name)!=value: raise ValueError('Staged Datei geändert: '+name)
    ready.write_text('ready',encoding='utf-8')
    deadline=time.monotonic()+20
    while not ((stage/'commit').is_file() and (stage/'commit').read_text(encoding='utf-8') == 'commit'):
        if time.monotonic() >= deadline: write('aborted','Keine bestätigte Übergabe.'); sys.exit(1)
        time.sleep(.05)
    (stage/'accepted').write_text('accepted',encoding='utf-8')
    while not ((stage/'go').is_file() and (stage/'go').read_text(encoding='utf-8') == 'go'):
        if (stage/'abort').exists() or select.select([fd],[],[],.1)[0]:
            write('aborted','Übergabe nicht abgeschlossen.'); sys.exit(1)
    if not select.select([fd],[],[],60)[0]:
        write('timeout','Vorherige Instanz läuft noch.'); sys.exit(1)
    os.close(fd)
    if digest(artifact)!=d['sha256']: raise ValueError('Prüfsumme geändert')
    for name, value in d['files'].items():
        if digest(release/name)!=value: raise ValueError('Staged Datei geändert: '+name)
    lock=root/'.local/update-recovery.lock'
    with open(lock,'a+b') as lockfile:
        fcntl.flock(lockfile,fcntl.LOCK_EX)
        recovery=root/'.local/update-recover.py'
        recovery_temp=root/'.local/update-recover.py.tmp'
        shutil.copy2(stage/'recover.py',recovery_temp)
        with open(recovery_temp,'rb') as f: os.fsync(f.fileno())
        os.replace(recovery_temp,recovery)
        old=stage/'previous'; old.mkdir()
        names=list(d['files'])
        for name in names: shutil.copy2(root/name,old/name)
        receipt=root/'.local/installation.json'; pointer=root/'.local/python-path.txt'
        shutil.copy2(receipt,old/'installation.json'); shutil.copy2(pointer,old/'python-path.txt')
        for saved in old.iterdir():
            with open(saved,'rb') as f: os.fsync(f.fileno())
        sync=runpy.run_path(str(recovery))['syncdir']; sync(old); sync(stage); sync(stage.parent); sync(recovery.parent)
        # Install the recovery-aware starter before creating a journal. If
        # interrupted here the old environment still starts with that starter.
        temp=root/'.update-start.sh.tmp'; shutil.copy2(release/'start.sh',temp)
        with open(temp,'rb') as f: os.fsync(f.fileno())
        os.replace(temp,root/'start.sh'); sync(root)
        pointer_file=root/'.local/update-recovery-python.txt'; pointer_temp=pointer_file.with_suffix('.tmp')
        with open(pointer_temp,'w',encoding='utf-8') as f:
            f.write(d['receipt']['python']+'\n'); f.flush(); os.fsync(f.fileno())
        os.replace(pointer_temp,pointer_file); sync(pointer_file.parent)
        journal=root/'.local/update-journal.json'; temp=journal.with_suffix('.tmp')
        with open(temp,'w',encoding='utf-8') as f:
            json.dump({'stage':str(stage),'hashes':{p.name:digest(p) for p in old.iterdir()}},f)
            f.flush(); os.fsync(f.fileno())
        os.replace(temp,journal); sync(journal.parent)
        try:
            cmd=[d['receipt']['python'],'-I',str(release/'setup.py'),'--root',str(root),'--uv',d['receipt']['uv'],'--manifest',str(release/'slim-manifest.json'),'--setup-only','--update']
            if d['explicit']: cmd += ['--data-dir',d['data']]
            with (stage/'setup.log').open('w',encoding='utf-8') as log:
                subprocess.run(cmd,check=True,stdout=log,stderr=subprocess.STDOUT)
            for name in names:
                temp=root/('.update-'+name+'.tmp'); shutil.copy2(release/name,temp)
                with open(temp,'rb') as f: os.fsync(f.fileno())
                os.replace(temp,root/name)
            sync(root)
            journal.unlink(); sync(journal.parent)
            write('installed')
        except Exception:
            runpy.run_path(str(recovery))['restore'](root)
            raise
    relaunch()
except Exception as e:
    write('failed',str(e))
    if (stage/'ready').exists() and not (root/'.local/update-journal.json').exists():
        try: relaunch()
        except Exception as restart_error: write('failed',str(e)+'; Neustart: '+str(restart_error))
'''

_WINDOWS_HELPER = r'''param([string]$Plan,[int]$ParentId)
$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class UpdateWait {
 [DllImport("kernel32.dll", SetLastError=true)] public static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
 [DllImport("kernel32.dll", SetLastError=true)] public static extern uint WaitForSingleObject(IntPtr h, uint ms);
 [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr h);
}
"@
$d=Get-Content -LiteralPath $Plan -Raw -Encoding UTF8 | ConvertFrom-Json
$ready=Join-Path $d.stage 'ready'; $outcome=Join-Path $d.stage 'outcome.json'
function Record($status,$detail) { @{status=$status;detail=$detail} | ConvertTo-Json | Set-Content -LiteralPath $outcome -Encoding UTF8 }
function RestartPrevious {
 if (Test-Path -LiteralPath (Join-Path $d.root 'start.cmd')) {
   $target=Join-Path $env:SystemRoot 'System32\wscript.exe'
   $arguments='"'+(Join-Path $d.root 'launch.vbs')+'" --data-dir "'+$d.data+'" --port '+$d.port
 } else { $target=Join-Path $d.root 'StudyCenter-Public.exe'; $arguments='--data-dir "'+$d.data+'" --port '+$d.port }
 Start-Process -FilePath $target -ArgumentList $arguments -WorkingDirectory $d.root -WindowStyle Hidden | Out-Null
}
# The installer may replace or remove program files after preparing a new
# environment. Keep the old bytes and registration until it reports success.
# .local environments/components are retained; only its active pointers change.
$backup=Join-Path $d.stage 'previous-installation'
$registry='HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\{B60E9F91-11F3-4EA8-B973-5C6D65C7A59A}_is1'
$shortcuts=@(
 (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\StudyCenter-Public\StudyCenter-Public.lnk'),
 (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\StudyCenter-Public\Uninstall StudyCenter-Public.lnk'),
 (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\StudyCenter-Public.lnk'),
 (Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'StudyCenter-Public.lnk')
)
function Hash($path) {
 $stream=[IO.File]::OpenRead($path); $sha=[Security.Cryptography.SHA256]::Create()
 try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-','').ToLowerInvariant() }
 finally { $stream.Dispose(); $sha.Dispose() }
}
function CheckRegular($path) {
 $cursor=[IO.Path]::GetFullPath($path)
 while ($cursor) {
  if (([IO.File]::Exists($cursor) -or [IO.Directory]::Exists($cursor)) -and (([IO.File]::GetAttributes($cursor) -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { throw 'Verlinkter Installationspfad verweigert: '+$cursor }
  $cursor=[IO.Path]::GetDirectoryName($cursor)
 }
}
function ProgramFiles {
 $files=New-Object 'System.Collections.Generic.List[string]'
 $pending=New-Object 'System.Collections.Generic.Stack[string]'; $pending.Push($d.root)
 while ($pending.Count -gt 0) {
  $directory=$pending.Pop(); CheckRegular $directory
  foreach ($item in Get-ChildItem -LiteralPath $directory -Force) {
   if ($directory -eq $d.root -and $item.Name -eq '.local') { CheckRegular $item.FullName; continue }
   CheckRegular $item.FullName
   if ($item.PSIsContainer) { $pending.Push($item.FullName) } else { $files.Add($item.FullName.Substring($d.root.Length).TrimStart('\')) }
  }
 }
 return $files.ToArray()
}
function SaveInstallation {
 [IO.Directory]::CreateDirectory($backup) | Out-Null
 $files=@(ProgramFiles)
 foreach ($name in $files) {
  $source=Join-Path $d.root $name; $target=Join-Path (Join-Path $backup 'program') $name
  [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
  [IO.File]::Copy($source,$target,$false)
 }
 $files | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $backup 'files.json') -Encoding UTF8
 if (Test-Path -LiteralPath (Join-Path $d.root 'start.cmd')) {
  foreach ($name in @('installation.json','python-path.txt')) {
   $source=Join-Path (Join-Path $d.root '.local') $name
   if (-not [IO.File]::Exists($source)) { throw 'Vorherige Umgebungskennung fehlt.' }
   CheckRegular $source; [IO.File]::Copy($source,(Join-Path $backup $name),$false)
  }
 }
 & "$env:SystemRoot\System32\reg.exe" export $registry (Join-Path $backup 'uninstall.reg') /y | Out-Null
 if ($LASTEXITCODE -ne 0) { throw 'Installationsregistrierung konnte nicht gesichert werden.' }
 $saved=@()
 for ($i=0; $i -lt $shortcuts.Count; $i++) {
  $exists=[IO.File]::Exists($shortcuts[$i]); $saved+= $exists
  if ($exists) { CheckRegular $shortcuts[$i]; [IO.File]::Copy($shortcuts[$i],(Join-Path $backup ('shortcut-'+$i)),$false) }
 }
 ConvertTo-Json -InputObject $saved | Set-Content -LiteralPath (Join-Path $backup 'shortcuts.json') -Encoding UTF8
}
function RestoreFile($source,$destination) {
 $parent=[IO.Path]::GetDirectoryName($destination); CheckRegular $parent
 [IO.Directory]::CreateDirectory($parent) | Out-Null
 CheckRegular $parent
 if ([IO.File]::Exists($destination)) { CheckRegular $destination }
 $temporary=$destination+'.update-restore-'+[Guid]::NewGuid().ToString('N'); $created=$false
 try {
  [IO.File]::Copy($source,$temporary,$false); $created=$true
  if ([IO.File]::Exists($destination)) { [IO.File]::Copy($temporary,$destination,$true) } else { [IO.File]::Move($temporary,$destination) }
  if ((Hash $source) -ne (Hash $destination)) { throw 'Wiederherstellung konnte nicht verifiziert werden: '+$destination }
 } finally {
  if ($created -and [IO.File]::Exists($temporary)) { CheckRegular $temporary; [IO.File]::Delete($temporary) }
 }
}
function RestoreInstallation {
 $saved=Get-Content -LiteralPath (Join-Path $backup 'files.json') -Raw -Encoding UTF8 | ConvertFrom-Json
 $allowed=@('start.cmd','start.sh','LICENSE','README.md','setup.py','slim-manifest.json','launch.vbs','SYSTEM-RUNTIME-REQUIREMENTS.iss','installation-files.txt','installation-marker.ini','unins000.exe','unins000.dat','unins000.msg')
 $unknown=@()
 foreach ($name in @(ProgramFiles)) {
  if ($saved -notcontains $name) {
   if ($allowed -contains $name) { [IO.File]::Delete((Join-Path $d.root $name)) } else { $unknown+= $name }
  }
 }
 foreach ($name in $saved) { RestoreFile (Join-Path (Join-Path $backup 'program') $name) (Join-Path $d.root $name) }
 foreach ($name in @('installation.json','python-path.txt')) {
  $source=Join-Path $backup $name
  if ([IO.File]::Exists($source)) { RestoreFile $source (Join-Path (Join-Path $d.root '.local') $name) }
 }
 [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($registry.Substring(5),$false)
 & "$env:SystemRoot\System32\reg.exe" import (Join-Path $backup 'uninstall.reg') | Out-Null
 if ($LASTEXITCODE -ne 0) { throw 'Installationsregistrierung konnte nicht wiederhergestellt werden.' }
 $savedShortcuts=Get-Content -LiteralPath (Join-Path $backup 'shortcuts.json') -Raw -Encoding UTF8 | ConvertFrom-Json
 for ($i=0; $i -lt $shortcuts.Count; $i++) {
  if ($savedShortcuts[$i]) { RestoreFile (Join-Path $backup ('shortcut-'+$i)) $shortcuts[$i] }
  elseif ([IO.File]::Exists($shortcuts[$i])) { CheckRegular $shortcuts[$i]; [IO.File]::Delete($shortcuts[$i]) }
 }
 if ($unknown.Count -gt 0) { throw 'Unbekannte neue Dateien bleiben erhalten; automatischer Neustart verweigert: '+($unknown -join ', ') }
}
$opened=$false; $exited=$false; $safeRestart=$true; $mutationPossible=$false
try {
 if ((Hash $d.artifact) -ne $d.sha256) { throw 'Prüfsumme geändert' }
 $handle=[UpdateWait]::OpenProcess(0x100000,$false,$ParentId)
 if ($handle -eq [IntPtr]::Zero) { throw 'Prozess-Handle konnte nicht geöffnet werden' }
 $opened=$true
 Set-Content -LiteralPath $ready -Value 'ready' -Encoding ASCII
 $deadline=[DateTime]::UtcNow.AddSeconds(20)
 while (-not ((Test-Path -LiteralPath (Join-Path $d.stage 'commit')) -and ((Get-Content -LiteralPath (Join-Path $d.stage 'commit') -Raw -Encoding UTF8) -ceq 'commit'))) {
  if ([DateTime]::UtcNow -ge $deadline) { Record 'aborted' 'Keine bestätigte Übergabe.'; exit 1 }
  Start-Sleep -Milliseconds 50
 }
 Set-Content -LiteralPath (Join-Path $d.stage 'accepted') -Value 'accepted' -Encoding ASCII
 while (-not ((Test-Path -LiteralPath (Join-Path $d.stage 'go')) -and ((Get-Content -LiteralPath (Join-Path $d.stage 'go') -Raw -Encoding UTF8) -ceq 'go'))) {
  if ((Test-Path -LiteralPath (Join-Path $d.stage 'abort')) -or ([UpdateWait]::WaitForSingleObject($handle,100) -eq 0)) { Record 'aborted' 'Übergabe nicht abgeschlossen.'; exit 1 }
 }
 $wait=[UpdateWait]::WaitForSingleObject($handle,60000)
 if ($wait -ne 0) { Record 'timeout' 'Vorherige Instanz läuft noch.'; exit 1 }
 $exited=$true
 if ((Hash $d.artifact) -ne $d.sha256) { throw 'Prüfsumme geändert' }
 SaveInstallation
 $arguments='/DIR="'+$d.root+'" /DATADIR="'+$d.data+'" /UPDATELOCK=1 /UPDATELOCKDIR="'+$d.root+'" /UPDATELOCKDATA="'+$d.data+'"'
 $mutationPossible=$true
 $process=Start-Process -FilePath $d.artifact -ArgumentList $arguments -PassThru -Wait
 if ($process.ExitCode -ne 0) { try { RestoreInstallation; $mutationPossible=$false } catch { $safeRestart=$false; throw }; Record 'cancelled_or_failed' ('Installer exit '+$process.ExitCode); RestartPrevious; exit 0 }
 $mutationPossible=$false
 Record 'installer_completed' ''
 RestartPrevious
} catch {
 $detail=$_.Exception.Message
 if ($mutationPossible -and $safeRestart) { try { RestoreInstallation; $mutationPossible=$false } catch { $safeRestart=$false; $detail+='; Wiederherstellung fehlgeschlagen: '+$_.Exception.Message } }
 Record 'failed' $detail
 if ($exited -and $safeRestart) { try { RestartPrevious } catch { Record 'failed' ($detail+'; Neustart fehlgeschlagen: '+$_.Exception.Message) } }
}
finally { if ($opened) { [UpdateWait]::CloseHandle($handle) | Out-Null } }
'''


def launch_handoff(prepared):
    stage = reject_links(Path(prepared['stage']))
    if _digest(prepared['artifact']) != prepared['sha256']:
        raise UpdateInstallError('Update-Prüfsumme stimmt vor Übergabe nicht mehr überein.')
    plan = dict(prepared)
    if prepared['platform'] == 'linux':
        release = stage/'release'
        if {p.name: _digest(p) for p in release.iterdir()} != prepared['files']:
            raise UpdateInstallError('Staged Update-Dateien haben sich verändert.')
    config = stage/'handoff.json'
    config.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
    if sys.platform != 'win32': config.chmod(0o600)
    helper = stage/('handoff.ps1' if prepared['platform'] == 'windows' else 'handoff.py')
    helper.write_text(_WINDOWS_HELPER if prepared['platform'] == 'windows' else _LINUX_HELPER, encoding='utf-8-sig' if prepared['platform'] == 'windows' else 'utf-8')
    if prepared['platform'] == 'linux':
        (stage/'recover.py').write_text(_RECOVERY_HELPER, encoding='utf-8')
    options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    fd = None
    try:
        if prepared['platform'] == 'windows':
            powershell = Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
            command = [str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(helper), str(config), str(os.getpid())]
            options['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        else:
            fd = os.pidfd_open(os.getpid(), 0)
            command = [prepared['receipt']['python'], '-I', str(helper), str(config), str(fd)]
            options.update(pass_fds=(fd,), start_new_session=True)
        child = subprocess.Popen(command, **options)
        deadline = time.monotonic()+15
        while time.monotonic() < deadline:
            if (stage/'ready').is_file():
                atomic_json(Path(prepared['status_file']), {'stage': str(stage)})
                temporary = stage/'commit.tmp'
                with temporary.open('x', encoding='utf-8') as confirmation:
                    confirmation.write('commit')
                    confirmation.flush()
                    os.fsync(confirmation.fileno())
                os.replace(temporary, stage/'commit')
                accepted_deadline = time.monotonic()+15
                while time.monotonic() < accepted_deadline:
                    if (stage/'accepted').is_file():
                        go = stage/'go.tmp'
                        with go.open('x', encoding='utf-8') as signal:
                            signal.write('go'); signal.flush(); os.fsync(signal.fileno())
                        os.replace(go, stage/'go')
                        return {'pid': child.pid, 'outcome': str(stage/'outcome.json')}
                    if child.poll() is not None: break
                    time.sleep(.05)
                try: (stage/'abort').touch(exist_ok=True)
                except OSError: pass
                raise UpdateInstallError('Update-Helfer hat die Übergabe nicht bestätigt; StudyCenter bleibt geöffnet.')
            if child.poll() is not None:
                break
            time.sleep(.05)
        raise UpdateInstallError('Update-Helfer konnte die laufende Instanz nicht sicher übernehmen; StudyCenter bleibt geöffnet.')
    finally:
        if fd is not None: os.close(fd)


def outcome_status(paths):
    """Read this instance's latest handoff status without scanning siblings."""
    try:
        root, _, _ = _layout(paths)
        pointer = reject_links(paths.config/'update-handoff.json')
        stage = reject_links(Path(json.loads(pointer.read_text(encoding='utf-8'))['stage']))
        if stage.parent != root.parent or not stage.name.startswith('.studycenter-update-'):
            return None
        payload = json.loads(reject_links(stage/'handoff.json').read_text(encoding='utf-8'))
        if payload.get('root') != str(root) or payload.get('data') != str(paths.data) or payload.get('stage') != str(stage):
            return None
        result = reject_links(stage/'outcome.json')
        return json.loads(result.read_text(encoding='utf-8-sig')) if result.is_file() else {'status': 'pending', 'detail': ''}
    except (OSError, ValueError, KeyError, TypeError):
        return None
_RECOVERY_HELPER = r'''import fcntl, hashlib, json, os, pathlib, shutil, stat, sys
NAMES=('start.sh','LICENSE','README.md','setup.py','backup_core.py','slim-manifest.json')
def checked(path):
    p=pathlib.Path(path).absolute()
    for part in (p,*p.parents):
        try: mode=part.lstat().st_mode
        except FileNotFoundError: continue
        if stat.S_ISLNK(mode): raise ValueError('Verlinkter Wiederherstellungspfad')
    return p
def digest(path):
    with open(path,'rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()
def syncdir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)
def restore(root):
    root=checked(root); journal=checked(root/'.local/update-journal.json')
    if not journal.exists(): return
    j=json.loads(journal.read_text(encoding='utf-8')); stage=checked(j['stage'])
    if stage.parent != root.parent or not stage.name.startswith('.studycenter-update-'): raise ValueError('Ungültiger Update-Ordner')
    old=checked(stage/'previous')
    targets={name:root/name for name in NAMES}
    targets.update({'installation.json':root/'.local/installation.json','python-path.txt':root/'.local/python-path.txt'})
    if set(j['hashes']) != set(targets): raise ValueError('Unvollständiges Update-Journal')
    for name in targets:
        saved=checked(old/name); checked(targets[name])
        if not saved.is_file() or digest(saved)!=j['hashes'][name]: raise ValueError('Update-Sicherung beschädigt: '+name)
    # Keep the recovery-aware starter until every other file is restored and
    # the journal is durably removed. An interrupted recovery remains retryable.
    for name,target in targets.items():
        if name == 'start.sh': continue
        temp=checked(target.with_name(target.name+'.update-restore'))
        shutil.copy2(old/name,temp)
        with open(temp,'rb') as f: os.fsync(f.fileno())
        os.replace(temp,target); syncdir(target.parent)
    journal.unlink(); syncdir(journal.parent)
    target=targets['start.sh']; temp=checked(target.with_name(target.name+'.update-restore'))
    shutil.copy2(old/'start.sh',temp)
    with open(temp,'rb') as f: os.fsync(f.fileno())
    os.replace(temp,target); syncdir(target.parent)
if __name__=='__main__':
    root=checked(sys.argv[1]); lock=checked(root/'.local/update-recovery.lock')
    with open(lock,'a+b') as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        restore(root)
'''
