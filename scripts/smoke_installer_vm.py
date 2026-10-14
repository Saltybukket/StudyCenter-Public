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
    def show_log(log):
        text=log.read_text(encoding='utf-8-sig',errors='replace') if log.exists() else 'Inno did not create its own setup log.'
        for variable in ('RUNNER_TEMP','USERPROFILE','APPDATA','GITHUB_WORKSPACE'):
            value=os.environ.get(variable)
            if value:text=text.replace(value,'<'+variable+'>')
        print(text[-20000:],flush=True)
    def setup_run(command, log):
        try:return subprocess.run(command,check=False,timeout=180)
        except subprocess.TimeoutExpired:
            show_log(log)
            raise
    # Own tiny packages exercise prerequisite refusal before any target write.
    # They never execute their synthetic placeholder application.
    for label,requirement in (
        ('missing-system-runtime','studycenter-public-synthetic-missing.dll|required'),
        ('missing-system-export','vcruntime140.dll|StudyCenterPublicSyntheticMissingExport'),
    ):
        fixture=root/label;fixture.mkdir();payload=fixture/'payload';payload.mkdir()
        (payload/'StudyCenter-Public.exe').write_bytes(b'Synthetic placeholder; prerequisite refusal prevents execution')
        (payload/'SYSTEM-RUNTIME-REQUIREMENTS.iss').write_text("const SystemRuntimeRequirements = '"+requirement+"';\n")
        subprocess.run([str(compiler()),'/DBundleDir='+str(payload),'/DOutputDir='+str(fixture),str(ROOT/'packaging/windows/installer.iss')],check=True)
        setup=next(fixture.glob('*-setup.exe'));target=fixture/'Never-created program';target_data=fixture/'Never-created data';log=fixture/'refusal.log'
        outcome=setup_run([str(setup),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/DIR='+str(target),'/DATADIR='+str(target_data),'/LOG='+str(log)],log)
        if outcome.returncode!=7:
            show_log(log)
            raise RuntimeError('System prerequisite refusal failed: '+label+' exit '+str(outcome.returncode))
        if target.exists() or target_data.exists():raise RuntimeError('System prerequisite refusal wrote destination folders')
    def rejected_destination(label, candidate_program, candidate_data, preserved):
        def snapshot(directory):
            import hashlib
            return {str(path.relative_to(directory)):hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.rglob('*') if path.is_file()}
        before=snapshot(preserved)
        rejected=[str(Path(installer).resolve()),'/CURRENTUSER','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/DIR='+str(candidate_program),'/DATADIR='+str(candidate_data),'/LOG='+str(root/(label+'-rejected.log'))]
        result=setup_run(rejected,root/(label+'-rejected.log'))
        if result.returncode!=7:
            show_log(root/(label+'-rejected.log'))
            raise RuntimeError('Destination preflight did not refuse '+label+' before installation: exit '+str(result.returncode))
        if snapshot(preserved)!=before:raise RuntimeError('Destination refusal changed unrelated files during '+label)
    foreign_data=root/'Foreign data';foreign_data.mkdir();(foreign_data/'instance.json').write_text(json.dumps({'application':'Other.Application','nested':{'application':'StudyCenter.Public'},'instance_id':str(uuid4()),'host_id':'urn:uuid:'+str(uuid4())}));(foreign_data/'preserve.txt').write_text('Synthetic unrelated content')
    rejected_destination('foreign-data-marker',root/'Never-created program',foreign_data,foreign_data)
    if (root/'Never-created program').exists():raise RuntimeError('Foreign data refusal created a program directory')
    foreign_program=root/'Foreign program';foreign_program.mkdir();(foreign_program/'StudyCenter-Public.exe').write_bytes(b'Synthetic foreign named executable');(foreign_program/'matching.dll').write_bytes(b'Synthetic foreign library')
    rejected_destination('foreign-program',foreign_program,root/'Never-created data',foreign_program)
    if (root/'Never-created data').exists():raise RuntimeError('Foreign program refusal created a data directory')
    import _winapi
    junction=root/'Redirected ancestor';_winapi.CreateJunction(str(foreign_data),str(junction))
    rejected_destination('data-junction',root/'Never-created program',junction/'Child',foreign_data)
    rejected_destination('program-junction',junction/'Child',root/'Never-created data',foreign_data)
    denied=root/'Own unreadable fixture';denied.mkdir();preserved=denied/'preserve.txt';preserved.write_text('Synthetic unrelated ACL fixture')
    account=os.environ['USERDOMAIN']+'\\'+os.environ['USERNAME']
    subprocess.run(['icacls',str(denied),'/deny',account+':(RD)'],check=True,capture_output=True)
    try:
        refused=setup_run([str(Path(installer).resolve()),'/CURRENTUSER','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/DIR='+str(root/'Never-created program'),'/DATADIR='+str(denied),'/LOG='+str(root/'unreadable-rejected.log')],root/'unreadable-rejected.log')
        if refused.returncode!=7:
            show_log(root/'unreadable-rejected.log')
            raise RuntimeError('Unreadable directory was treated as empty: exit '+str(refused.returncode))
    finally:
        subprocess.run(['icacls',str(denied),'/remove:d',account],check=True,capture_output=True)
    if preserved.read_text()!='Synthetic unrelated ACL fixture' or (root/'Never-created program').exists():raise RuntimeError('Unreadable-folder refusal altered foreign data or created a program folder')
    def install(label):
        log=root/(label+'-setup.log')
        outcome=setup_run(command+['/LOG='+str(log)],log)
        if outcome.returncode:
            show_log(log)
            raise RuntimeError('Own VM installer failed during '+label+' with exit '+str(outcome.returncode))
    install('first-install')
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
    install('update')
    running.wait(timeout=30)
    if json.loads((data/'instance.json').read_text())!=before or (data/'studycenter-public.sqlite3').read_bytes()!=database:raise RuntimeError('Update changed own instance identity or database')
    snapshots=list((data/'backups').glob('before-update-*/studycenter-public.sqlite3'))
    if not snapshots or snapshots[-1].read_bytes()!=database:raise RuntimeError('Update did not preserve the existing database before replacing program files')
    smoke(executable,root/'Updated installed smoke')
    uninstaller=program/'unins000.exe'
    running=start_own_instance()
    uninstall_log=root/'own-uninstall.log'
    outcome=setup_run([str(uninstaller),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/LOG='+str(uninstall_log)],uninstall_log)
    if outcome.returncode:
        show_log(uninstall_log)
        raise RuntimeError('Own VM uninstall failed with exit '+str(outcome.returncode))
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
    result={'installer_install':True,'foreign_marker_refused_without_writes':True,'foreign_program_refused_without_exec_or_overwrite':True,'reparse_ancestors_refused_without_writes':True,'installed_smoke':True,'update_preserves_identity_database':True,'backup_before_update':True,'uninstall_retains_data':True,'own_shortcuts_removed':True,'environment':'ephemeral GitHub windows-2022 runner','windows10_live':False,'windows11_live':False}
    (root/'installer-vm-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result));return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--installer',type=Path)
    run(parser.parse_args().installer)
