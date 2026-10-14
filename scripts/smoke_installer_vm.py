"""Installer lifecycle probe restricted to ephemeral GitHub-hosted Windows runners."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]


def require_disposable_runner():
    if sys.platform!='win32' or os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':
        raise RuntimeError('Installer execution is allowed only in an ephemeral GitHub-hosted Windows runner; never on a developer host')
    temporary=Path(os.environ['RUNNER_TEMP']).resolve()
    if not temporary.is_dir():raise RuntimeError('Disposable runner TEMP directory is unavailable')
    return temporary


def compiler():
    candidates=[Path(os.environ.get('ProgramFiles(x86)',''))/'Inno Setup 6'/'ISCC.exe',Path(os.environ.get('ProgramFiles',''))/'Inno Setup 7'/'ISCC.exe']
    found=shutil.which('ISCC.exe')
    if found:candidates.insert(0,Path(found))
    for candidate in candidates:
        if candidate.is_file():return candidate
    raise RuntimeError('Runner has no preinstalled Inno compiler; no installer/tool bootstrap is performed')


def run(installer=None):
    temporary=require_disposable_runner()
    from smoke_release import smoke
    import winreg
    app_id='{B60E9F91-11F3-4EA8-B973-5C6D65C7A59A}_is1'
    registry='Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\'+app_id
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,registry):raise RuntimeError('Own application ID already installed in runner; refusing to touch an existing instance')
    except FileNotFoundError:pass
    root=temporary/('StudyCenter Public VM ä '+uuid4().hex[:8]);root.mkdir()
    program=root/'Program ü space';data=root/'Data ä space'
    bundle=ROOT/'dist/windows/StudyCenter-Public'
    if installer is None:
        version=__import__('tomllib').loads((ROOT/'pyproject.toml').read_text())['project']['version']
        subprocess.run([str(compiler()),'/DAppVersion='+version,'/DBundleDir='+str(bundle),'/DOutputDir='+str(root),str(ROOT/'packaging/windows/installer.iss')],check=True)
        installer=next(root.glob('*-setup.exe'))
    command=[str(Path(installer).resolve()),'/CURRENTUSER','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/DIR='+str(program),'/DATADIR='+str(data),'/TASKS=startup,desktopicon']
    subprocess.run(command,check=True,timeout=180)
    executable=program/'StudyCenter-Public.exe'
    if not executable.is_file():raise RuntimeError('Installer did not create the own application executable')
    smoke(executable,root/'First installed smoke')
    subprocess.run([str(executable),'--self-test','--data-dir',str(data),'--no-browser'],check=True,timeout=60)
    before=json.loads((data/'instance.json').read_text());database=(data/'studycenter-public.sqlite3').read_bytes()
    def start_own_instance():
        process=subprocess.Popen([str(executable),'--no-browser','--port','0','--data-dir',str(data)])
        deadline=time.monotonic()+60
        while not (data/'runtime.json').exists():
            if process.poll() is not None:raise RuntimeError('Installed own application exited before readiness')
            if time.monotonic()>deadline:raise RuntimeError('Installed own application did not become ready')
            time.sleep(.2)
        return process
    running=start_own_instance()
    subprocess.run(command,check=True,timeout=180)
    running.wait(timeout=30)
    if json.loads((data/'instance.json').read_text())!=before or (data/'studycenter-public.sqlite3').read_bytes()!=database:raise RuntimeError('Update changed own instance identity or database')
    snapshots=list((data/'backups').glob('before-update-*/studycenter-public.sqlite3'))
    if not snapshots or snapshots[-1].read_bytes()!=database:raise RuntimeError('Update did not preserve the existing database before replacing program files')
    smoke(executable,root/'Updated installed smoke')
    uninstaller=program/'unins000.exe'
    running=start_own_instance()
    subprocess.run([str(uninstaller),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'],check=True,timeout=180)
    running.wait(timeout=30)
    if executable.exists():raise RuntimeError('Own application executable remains after uninstall')
    if json.loads((data/'instance.json').read_text())!=before or (data/'studycenter-public.sqlite3').read_bytes()!=database:raise RuntimeError('Uninstall removed or changed preserved own user data')
    startup=Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs/Startup/StudyCenter-Public.lnk'
    desktop=Path(os.environ['USERPROFILE'])/'Desktop/StudyCenter-Public.lnk'
    group=Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs/StudyCenter-Public'
    if startup.exists() or desktop.exists() or group.exists():raise RuntimeError('Own shortcuts remain after uninstall')
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,registry):raise RuntimeError('Own uninstall registration remains after uninstall')
    except FileNotFoundError:pass
    result={'installer_install':True,'installed_smoke':True,'update_preserves_identity_database':True,'backup_before_update':True,'uninstall_retains_data':True,'own_shortcuts_removed':True,'environment':'ephemeral GitHub windows-2022 runner','windows10_live':False,'windows11_live':False}
    (root/'installer-vm-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result));return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--installer',type=Path)
    run(parser.parse_args().installer)
