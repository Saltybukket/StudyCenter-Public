"""Native PowerShell rollback controls using only owned synthetic files and key."""
import json
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from studycenter_public import update_install


@pytest.mark.skipif(sys.platform != 'win32', reason='Requires native Win32 INI functions')
@pytest.mark.parametrize(('utf16', 'data_name'), [(False, 'data'), (False, 'data ä ö'),
                                                 (True, 'data'), (True, 'data ä ö 学習')])
def test_marker_reader_matches_win32_for_data_path(tmp_path, utf16, data_name):
    marker = tmp_path/'marker.ini'
    if utf16:
        marker.write_bytes(b'\xff\xfe')
    write = ctypes.WinDLL('kernel32', use_last_error=True).WritePrivateProfileStringW
    write.argtypes = [ctypes.c_wchar_p] * 4
    write.restype = ctypes.c_int
    data = str(tmp_path/data_name)
    for key, value in [('application', 'StudyCenter.Public'), ('data_directory', data), ('inventory_sha256', 'a'*64)]:
        assert write('identity', key, value, str(marker))
    assert update_install._read_windows_marker(marker) == {
        'application': 'StudyCenter.Public', 'data_directory': data, 'inventory_sha256': 'a'*64}
    if utf16:
        assert marker.read_bytes().startswith(b'\xff\xfe')


@pytest.mark.skipif(sys.platform != 'win32', reason='Requires Windows PowerShell and registry')
@pytest.mark.parametrize('unknown', [False, True])
def test_windows_snapshot_restores_files_pointers_and_shortcuts(tmp_path, unknown):
    root = tmp_path / 'program'
    stage = tmp_path / 'stage'
    local = root / '.local'
    local.mkdir(parents=True)
    stage.mkdir()
    for name in ('start.cmd', 'setup.py', 'installation-files.txt', 'installation-marker.ini'):
        (root / name).write_text('old '+name)
    for name in ('installation.json', 'python-path.txt'):
        (local / name).write_text('old '+name)
    shortcut = tmp_path/'shortcut.lnk'
    shortcut.write_text('old shortcut')
    registry = 'HKCU\\Software\\StudyCenter-Public-Test-'+uuid4().hex
    subprocess.run(['reg', 'add', registry, '/v', 'Sentinel', '/d', 'old', '/f'], check=True, capture_output=True)
    helper = update_install._WINDOWS_HELPER
    functions = helper[helper.index('# The installer may replace'):helper.index('$opened=$false')]
    a = functions.index('$registry=')
    b = functions.index('function Hash', a)
    functions = functions[:a]+"$registry='"+registry+"'\n$shortcuts=@('"+str(shortcut).replace("'", "''")+"')\n"+functions[b:]
    script = "$ErrorActionPreference='Stop'\n$d=Get-Content -LiteralPath $args[0] -Raw | ConvertFrom-Json\n"+functions+"""
SaveInstallation
[IO.File]::WriteAllText((Join-Path $d.root 'setup.py'),'new')
[IO.File]::WriteAllText((Join-Path $d.root '.local/installation.json'),'new')
[IO.File]::WriteAllText((Join-Path $d.root 'launch.vbs'),'new')
[IO.File]::WriteAllText($shortcuts[0],'new')
& reg add $registry /v Sentinel /d new /f | Out-Null
if ($args[1] -eq 'unknown') { [IO.File]::WriteAllText((Join-Path $d.root 'user-added.txt'),'keep') }
try { RestoreInstallation; if ($args[1] -eq 'unknown') { throw 'Expected unknown-path refusal' } }
catch { if ($args[1] -ne 'unknown' -or $_.Exception.Message -notlike '*Unbekannte neue Dateien*') { throw } }
"""
    plan = tmp_path/'plan.json'
    plan.write_text(json.dumps({'root':str(root), 'stage':str(stage)}), encoding='utf-8')
    script_file = tmp_path/'test.ps1'
    script_file.write_text(script, encoding='utf-8')
    try:
        result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                                 '-File',str(script_file),str(plan),'unknown' if unknown else 'normal'],
                                text=True,capture_output=True,timeout=30)
        assert result.returncode == 0, result.stdout+'\n'+result.stderr
        assert (root/'setup.py').read_text() == 'old setup.py'
        assert (local/'installation.json').read_text() == 'old installation.json'
        assert not (root/'launch.vbs').exists()
        assert shortcut.read_text() == 'old shortcut'
        assert ('old' in subprocess.run(['reg','query',registry,'/v','Sentinel'],capture_output=True,text=True,check=True).stdout)
        assert (root/'user-added.txt').exists() == unknown
    finally:
        subprocess.run(['reg','delete',registry,'/f'],capture_output=True)


@pytest.mark.skipif(sys.platform != 'win32', reason='Requires Windows junctions and PowerShell')
def test_restore_refuses_junction_before_creating_missing_directory(tmp_path):
    import _winapi
    foreign = tmp_path/'foreign'
    foreign.mkdir()
    root = tmp_path/'root'
    root.mkdir()
    _winapi.CreateJunction(str(foreign), str(root/'link'))
    source = tmp_path/'source'
    source.write_text('own backup')
    helper = update_install._WINDOWS_HELPER
    functions = helper[helper.index('function Hash'):helper.index('$opened=$false')]
    script = tmp_path/'junction.ps1'
    script.write_text("$ErrorActionPreference='Stop'\n"+functions+"\ntry { RestoreFile $args[0] $args[1]; exit 9 } catch { exit 0 }\n", encoding='utf-8')
    outcome = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                              '-File',str(script),str(source),str(root/'link'/'new'/'file.txt')],
                             capture_output=True,timeout=30)
    assert outcome.returncode == 0
    assert not (foreign/'new').exists()
    os.rmdir(root/'link')


@pytest.mark.skipif(sys.platform != 'win32', reason='Requires Windows file sharing and PowerShell')
def test_restore_retries_after_locked_destination_is_released(tmp_path):
    source, destination = tmp_path/'source', tmp_path/'destination'
    source.write_text('old bytes')
    destination.write_text('new bytes')
    helper = update_install._WINDOWS_HELPER
    functions = helper[helper.index('function Hash'):helper.index('$opened=$false')]
    script = tmp_path/'retry.ps1'
    script.write_text("$ErrorActionPreference='Stop'\n"+functions+"""
$lock=[IO.File]::Open($args[1], [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
$failed=$false
try { RestoreFile $args[0] $args[1] } catch { $failed=$true } finally { $lock.Dispose() }
if (-not $failed) { throw 'Expected locked file failure' }
RestoreFile $args[0] $args[1]
""", encoding='utf-8')
    outcome = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                              '-File',str(script),str(source),str(destination)], capture_output=True,text=True,timeout=30)
    assert outcome.returncode == 0, outcome.stdout+'\n'+outcome.stderr
    assert destination.read_text() == 'old bytes'
    assert not list(tmp_path.glob('destination.update-restore-*'))


@pytest.mark.skipif(sys.platform != 'win32', reason='Requires native Windows process handoff')
@pytest.mark.parametrize('installer_exit', [0, 3])
def test_native_handoff_waits_for_parent_and_handles_installer_result(tmp_path, installer_exit):
    """Use the real handoff protocol with an owned executable and registry key."""
    root, stage, data = (tmp_path/name for name in ('program', 'stage', 'data'))
    local = root/'.local'
    local.mkdir(parents=True)
    stage.mkdir()
    data.mkdir()
    for name in ('start.cmd', 'setup.py', 'execution-marker.txt'):
        (root/name).write_text('old', encoding='utf-8')
    for name in ('installation.json', 'python-path.txt'):
        (local/name).write_text('old', encoding='utf-8')
    shortcut = tmp_path/'shortcut.lnk'
    shortcut.write_text('old', encoding='utf-8')
    registry = 'HKCU\\Software\\StudyCenter-Public-Test-'+uuid4().hex
    subprocess.run(['reg', 'add', registry, '/v', 'Sentinel', '/d', 'old', '/f'], check=True, capture_output=True)
    source = tmp_path/'installer.cs'
    source.write_text('''using System;
using System.IO;
public static class Installer {
 public static int Main(string[] args) {
  string root = null;
  foreach (string a in args) if (a.StartsWith("/DIR=")) root=a.Substring(5);
  if (root == null) return 42;
  File.WriteAllText(Path.Combine(root,"execution-marker.txt"),"executed");
  File.WriteAllText(Path.Combine(root,"setup.py"),"new");
  File.WriteAllText(Path.Combine(root,".local","installation.json"),"new");
  return EXIT_CODE;
 }
}'''.replace('EXIT_CODE', str(installer_exit)), encoding='utf-8')
    artifact = stage/'setup.exe'
    compiler = Path(os.environ['SystemRoot'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([str(compiler), '/nologo', '/out:'+str(artifact), str(source)], check=True, capture_output=True)
    import hashlib
    plan = {'platform':'windows', 'root':str(root), 'data':str(data), 'stage':str(stage),
            'artifact':str(artifact), 'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest(),
            'status_file':str(data/'status.json'), 'port':8767, 'explicit':True}
    plan_file = tmp_path/'plan.json'
    plan_file.write_text(json.dumps(plan), encoding='utf-8')
    controller = tmp_path/'controller.py'
    controller.write_text('''import json, pathlib, sys, time
from studycenter_public import update_install as u
plan=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
registry,shortcut,ack,release=sys.argv[2:]
helper=u._WINDOWS_HELPER
a=helper.index('function RestartPrevious {'); b=helper.index('# The installer may replace',a)
helper=helper[:a]+"function RestartPrevious { Set-Content -LiteralPath (Join-Path $d.stage 'restarted') -Value 'yes' }\\n"+helper[b:]
a=helper.index('$registry='); b=helper.index('function Hash',a)
helper=helper[:a]+"$registry='"+registry+"'\\n$shortcuts=@('"+shortcut.replace("'", "''")+"')\\n"+helper[b:]
u._WINDOWS_HELPER=helper
result=u.launch_handoff(plan)
pathlib.Path(ack).write_text(json.dumps(result),encoding='utf-8')
deadline=time.monotonic()+30
while not pathlib.Path(release).exists():
 if time.monotonic()>deadline: raise RuntimeError('Parent release timed out')
 time.sleep(.05)
''', encoding='utf-8')
    ack, release = tmp_path/'ack.json', tmp_path/'exit-parent'
    child = subprocess.Popen([sys.executable, str(controller), str(plan_file), registry, str(shortcut), str(ack), str(release)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic()+30
        while not ack.exists():
            if child.poll() is not None:
                out, err = child.communicate()
                pytest.fail('Controller exited before handoff: '+out+'\n'+err)
            if time.monotonic() >= deadline:
                pytest.fail('Controller did not complete handoff')
            time.sleep(.05)
        assert (stage/'ready').exists() and (stage/'accepted').exists() and (stage/'go').exists()
        time.sleep(.5)
        assert child.poll() is None and (root/'execution-marker.txt').read_text() == 'old'
        assert not (stage/'outcome.json').exists()
        release.touch()
        out, err = child.communicate(timeout=30)
        assert child.returncode == 0, out+'\n'+err
        deadline = time.monotonic()+30
        while not (stage/'restarted').exists():
            if time.monotonic() >= deadline:
                outcome = (stage/'outcome.json').read_text(encoding='utf-8-sig') if (stage/'outcome.json').exists() else 'missing'
                pytest.fail('Helper did not finish: '+outcome)
            time.sleep(.05)
        outcome = json.loads((stage/'outcome.json').read_text(encoding='utf-8-sig'))
        if installer_exit:
            assert outcome['status'] == 'cancelled_or_failed'
            assert (root/'execution-marker.txt').read_text() == 'old'
            assert (root/'setup.py').read_text() == 'old'
            assert (local/'installation.json').read_text() == 'old'
        else:
            assert outcome['status'] == 'installer_completed'
            assert (root/'execution-marker.txt').read_text() == 'executed'
            assert (root/'setup.py').read_text() == 'new'
    finally:
        if child.poll() is None:
            release.touch()
            try:
                child.communicate(timeout=35)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.communicate(timeout=10)
        subprocess.run(['reg','delete',registry,'/f'],capture_output=True)
