"""Native PowerShell rollback controls using only owned synthetic files and key."""
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from studycenter_public import update_install


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
