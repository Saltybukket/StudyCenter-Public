"""Installer lifecycle probe restricted to ephemeral GitHub-hosted Windows runners."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import stat
import re
import urllib.request
from urllib.parse import urlsplit
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]


def ready_instance_snapshot(process, data, timeout=60):
    """Take the strict byte baseline after normal first-start initialization."""
    identity=json.loads((data/'instance.json').read_text())
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if process.poll() is not None:raise RuntimeError('Installed own application exited before readiness')
        try:
            endpoint=json.loads((data/'runtime.json').read_text())['url']
            parsed=urlsplit(endpoint)
            if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or not parsed.port or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                raise RuntimeError('Own VM readiness endpoint is not a loopback origin')
            with urllib.request.urlopen(endpoint+'/api/health',timeout=2) as response:health=json.load(response)
            if health.get('application')!='StudyCenter.Public' or health.get('instance_id')!=identity['instance_id']:
                raise RuntimeError('Own VM readiness identity differs')
            if json.loads((data/'instance.json').read_text())!=identity:
                raise RuntimeError('Own instance identity changed during readiness')
            return identity,(data/'studycenter-public.sqlite3').read_bytes()
        except (OSError,json.JSONDecodeError):time.sleep(.1)
    raise RuntimeError('Installed own application did not become ready')


def installation_inventory_diagnostics(program, data_directory, registered_install_location,
                                       *, marker_values=None, limit=32):
    """Read only: identify a VM update refusal without trusting extra files."""
    program=Path(program).absolute()
    if type(limit) is not int or not 1<=limit<=128:raise ValueError('Bounded diagnostic limit required')
    blocked=[];errors=[];actual={}
    excluded={'installation-files.txt','installation-marker.ini','unins000.dat','unins000.msg'}
    def no_links(path):
        for entry in (path,*path.parents):
            try:info=entry.lstat()
            except FileNotFoundError:continue
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:return False
        return True
    def own_member(name):
        normalized=name.replace('\\','/')
        if not normalized or ':' in normalized or '..' in normalized or normalized.startswith('/'):
            raise ValueError('Invalid relative inventory member')
        return program/normalized
    def hash_file(path):
        with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
    if not no_links(program):return {'program_path_safe':False,'diagnostic_error':'link_or_reparse_ancestor'}
    def visit(directory):
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    path=Path(entry.path);name=path.relative_to(program).as_posix()
                    try:
                        info=path.lstat()
                        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                            blocked.append({'path':name,'reason':'link_or_reparse'});continue
                        if stat.S_ISDIR(info.st_mode):visit(path)
                        elif stat.S_ISREG(info.st_mode):actual[name]={'sha256':hash_file(path),'size':info.st_size}
                        else:blocked.append({'path':name,'reason':'special_file'})
                    except OSError as error:errors.append({'path':name,'reason':type(error).__name__})
        except OSError as error:errors.append({'path':directory.relative_to(program).as_posix(),'reason':type(error).__name__})
    visit(program)
    def read_known(name):
        path=own_member(name)
        if name not in actual or not no_links(path):raise ValueError('Diagnostic metadata unavailable')
        return path.read_bytes()
    if marker_values is None:
        if sys.platform!='win32':raise RuntimeError('Native marker read requires the disposable Windows runner')
        import ctypes
        from ctypes import wintypes
        read_ini=ctypes.WinDLL('kernel32',use_last_error=True).GetPrivateProfileStringW
        read_ini.argtypes=[wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.LPWSTR,wintypes.DWORD,wintypes.LPCWSTR]
        read_ini.restype=wintypes.DWORD
        marker=own_member('installation-marker.ini')
        marker_values={}
        if 'installation-marker.ini' in actual and no_links(marker):
            for key in ('application','data_directory','inventory_sha256'):
                value=ctypes.create_unicode_buffer(4097)
                length=read_ini('identity',key,'',value,len(value),str(marker))
                marker_values[key]=value.value if length<len(value)-1 else ''
    def comparable(value):
        if not value:return None
        normalized=os.path.abspath(value).rstrip('\\/')
        return normalized.casefold() if os.name=='nt' else normalized
    invalid=0;inventory={};manifest={};inventory_encoding=None;inventory_sha=None
    try:
        raw=read_known('installation-files.txt');inventory_sha=hashlib.sha256(raw).hexdigest()
        try:text=raw.decode('utf-8-sig');inventory_encoding='utf-8-sig'
        except UnicodeDecodeError:text=raw.decode('mbcs' if os.name=='nt' else 'cp1252');inventory_encoding='system_ansi'
        for line in text.splitlines():
            if not line:continue
            try:
                digest,name=line.split('|',1)
                if not re.fullmatch('[0-9a-fA-F]{64}',digest):raise ValueError('Invalid digest')
                name=own_member(name).relative_to(program).as_posix()
                if name in inventory:raise ValueError('Duplicate member')
                inventory[name]={'sha256':digest.lower()}
            except ValueError:invalid+=1
    except (OSError,ValueError,UnicodeError) as error:errors.append({'path':'installation-files.txt','reason':type(error).__name__})
    try:
        payload=json.loads(read_known('BUILD-MANIFEST.json'))['files']
        for name,row in payload.items():
            relative=own_member(name).relative_to(program).as_posix()
            if not re.fullmatch('[0-9a-f]{64}',row['sha256']):raise ValueError('Invalid manifest digest')
            manifest[relative]={'sha256':row['sha256'],'size':row['size']}
    except (OSError,ValueError,KeyError,TypeError) as error:errors.append({'path':'BUILD-MANIFEST.json','reason':type(error).__name__})
    def delta(expected,ignored):
        observed={name:row for name,row in actual.items() if name not in ignored}
        added=[{'path':name,**observed[name]} for name in sorted(set(observed)-set(expected))]
        missing=[{'path':name,**expected[name]} for name in sorted(set(expected)-set(observed))]
        changed=[{'path':name,'expected_sha256':expected[name]['sha256'],'actual_sha256':observed[name]['sha256']} for name in sorted(set(expected)&set(observed)) if expected[name]['sha256']!=observed[name]['sha256'] or ('size' in expected[name] and expected[name]['size']!=observed[name]['size'])]
        return {label+'_count':len(rows) for label,rows in [('added',added),('missing',missing),('changed',changed)]}|{label:rows[:limit] for label,rows in [('added',added),('missing',missing),('changed',changed)]}
    return {'program_path_safe':True,'marker_application_matches':marker_values.get('application')=='StudyCenter.Public',
            'marker_data_directory_matches':comparable(marker_values.get('data_directory'))==comparable(str(data_directory)),
            'registry_install_location_matches':comparable(registered_install_location)==comparable(str(program)),
            'inventory_hash_matches_marker':inventory_sha is not None and marker_values.get('inventory_sha256')==inventory_sha,
            'inventory_sha256':inventory_sha,'inventory_encoding':inventory_encoding,'inventory_parse_valid':invalid==0 and inventory_sha is not None,
            'invalid_inventory_entries':invalid,'inventory_entries':len(inventory),'manifest_entries':len(manifest),
            'inventory_delta':delta(inventory,excluded),'manifest_delta':delta(manifest,excluded|{'BUILD-MANIFEST.json'}),
            'blocked_member_count':len(blocked),'blocked_members':blocked[:limit],'read_error_count':len(errors),'read_errors':errors[:limit],
            'scope':'Read-only diagnostics, not installation ownership approval; only relative paths and byte hashes.'}


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


def run(installer=None,expected_source=None):
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
        ('missing-system-export','kernel32.dll|StudyCenterPublicSyntheticMissingExport'),
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
    manifest=json.loads((program/'BUILD-MANIFEST.json').read_text())
    if expected_source is not None and manifest.get('source_git_sha')!=expected_source:
        raise RuntimeError('Installed candidate does not have the expected immutable build source')
    import hashlib
    for name,record in manifest['files'].items():
        file=program/name
        if not file.resolve().is_relative_to(program.resolve()) or file.is_symlink():
            raise RuntimeError('Installed manifest member escapes the own program')
        with file.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if file.stat().st_size!=record['size'] or digest!=record['sha256']:
            raise RuntimeError('Installed candidate differs from its build manifest: '+name)
    # Actual frozen refusal formerly opened a bootloader dialog. These negative
    # executable controls run only after the disposable VM guard above.
    foreign_frozen=root/'Frozen foreign data';foreign_frozen.mkdir()
    marker=foreign_frozen/'preserve.txt';marker.write_bytes(b'Synthetic unrelated frozen-start sentinel')
    for quiet_flag in ('--no-browser','--self-test','--component-test','--quit'):
        refusal=subprocess.run([str(executable),quiet_flag,'--data-dir',str(foreign_frozen)],
                               capture_output=True,timeout=30)
        if refusal.returncode!=2:
            raise RuntimeError('Frozen startup did not refuse foreign data headlessly: '+quiet_flag)
        if list(foreign_frozen.iterdir())!=[marker] or marker.read_bytes()!=b'Synthetic unrelated frozen-start sentinel':
            raise RuntimeError('Frozen refusal altered unrelated data: '+quiet_flag)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,registry,0,winreg.KEY_READ|winreg.KEY_WOW64_64KEY) as key:
            installed_location=winreg.QueryValueEx(key,'InstallLocation')[0]
    except OSError:installed_location=None
    smoke(executable,root/'First installed smoke',installed_inventory={'data_directory':data,'registered_install_location':installed_location})
    subprocess.run([str(executable),'--self-test','--data-dir',str(data),'--no-browser'],check=True,timeout=60)
    def start_own_instance():
        process=subprocess.Popen([str(executable),'--no-browser','--port','0','--data-dir',str(data)])
        deadline=time.monotonic()+60
        while not (data/'runtime.json').exists():
            if process.poll() is not None:raise RuntimeError('Installed own application exited before readiness')
            if time.monotonic()>deadline:raise RuntimeError('Installed own application did not become ready')
            time.sleep(.2)
        return process
    running=start_own_instance()
    before,database=ready_instance_snapshot(running,data)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,registry,0,winreg.KEY_READ|winreg.KEY_WOW64_64KEY) as key:
            registered_location=winreg.QueryValueEx(key,'InstallLocation')[0]
    except OSError:registered_location=None
    diagnostic=installation_inventory_diagnostics(program,data,registered_location)
    (root/'before-update-inventory-diagnostic.json').write_text(json.dumps(diagnostic,indent=2),encoding='utf-8')
    print(json.dumps({'before_update_inventory_diagnostic':diagnostic}),flush=True)
    install('update')
    running.wait(timeout=30)
    if json.loads((data/'instance.json').read_text())!=before or (data/'studycenter-public.sqlite3').read_bytes()!=database:raise RuntimeError('Update changed own instance identity or database')
    snapshots=list((data/'backups').glob('before-update-*/studycenter-public.sqlite3'))
    if not snapshots or snapshots[-1].read_bytes()!=database:raise RuntimeError('Update did not preserve the existing database before replacing program files')
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,registry,0,winreg.KEY_READ|winreg.KEY_WOW64_64KEY) as key:
            updated_location=winreg.QueryValueEx(key,'InstallLocation')[0]
    except OSError:updated_location=None
    smoke(executable,root/'Updated installed smoke',installed_inventory={'data_directory':data,'registered_install_location':updated_location})
    uninstaller=program/'unins000.exe'
    running=start_own_instance()
    if ready_instance_snapshot(running,data)!=(before,database):raise RuntimeError('Reopening the updated instance changed identity or database')
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
    with Path(installer).open('rb') as stream:installer_sha=hashlib.file_digest(stream,'sha256').hexdigest()
    result={'installer_sha256':installer_sha,'source_git_sha':manifest.get('source_git_sha'),'installed_manifest_files_verified':len(manifest['files']),'installer_install':True,'foreign_marker_refused_without_writes':True,'foreign_program_refused_without_exec_or_overwrite':True,'reparse_ancestors_refused_without_writes':True,'installed_smoke':True,'update_preserves_identity_database':True,'backup_before_update':True,'uninstall_retains_data':True,'own_shortcuts_removed':True,'environment':'ephemeral GitHub windows-2022 runner','windows10_live':False,'windows11_live':False}
    result['frozen_foreign_data_headless_refusal_modes']=4
    (root/'installer-vm-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result));return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--installer',type=Path);parser.add_argument('--expected-source')
    args=parser.parse_args();run(args.installer,args.expected_source)
