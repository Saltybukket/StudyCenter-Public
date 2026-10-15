"""Exercise a frozen bundle with synthetic data, real local HTTP and optional OCR."""
import argparse
import http.cookiejar
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import stat
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from uuid import uuid4


def _manifest_files(program):
    path=program/'BUILD-MANIFEST.json';info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
        raise ValueError('Program manifest is not a regular owned file')
    expected=json.loads(path.read_text(encoding='utf-8'))['files']
    if any(not isinstance(name,str) or name.startswith(('/','\\')) or ':' in name or '..' in name or '\\' in name for name in expected):
        raise ValueError('Program manifest contains an invalid relative member')
    return expected


def _program_files(program):
    program=Path(program).absolute();files={}
    for ancestor in (program,*program.parents):
        info=ancestor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise ValueError('Program path contains a link or reparse point')
    expected=_manifest_files(program)
    def regular_record(path):
        descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(descriptor,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):raise ValueError('Program alias target is not regular')
            return {'size':info.st_size,'sha256':hashlib.file_digest(stream,'sha256').hexdigest()}
    def alias_record(path):
        original=os.readlink(path);current=path;seen=set()
        for _ in range(64):
            if current in seen:raise ValueError('Program alias contains a loop')
            seen.add(current)
            if current.relative_to(program).as_posix() not in expected:
                raise ValueError('Program alias target is not manifest bound')
            for ancestor in current.parents:
                if ancestor==program:break
                info=ancestor.lstat()
                if not stat.S_ISDIR(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                    raise ValueError('Program alias traverses a linked or non-directory ancestor')
            info=current.lstat()
            if stat.S_ISLNK(info.st_mode):
                target=os.readlink(current)
                if os.path.isabs(target):raise ValueError('Program alias target is absolute')
                current=Path(os.path.abspath(current.parent/target))
                if current==program or not current.is_relative_to(program):
                    raise ValueError('Program alias target escapes the program')
            elif stat.S_ISREG(info.st_mode):
                return {**regular_record(current),'link_target':original}
            else:raise ValueError('Program alias target is not a regular file')
        raise ValueError('Program alias chain exceeds the bounded limit')
    def visit(directory):
        with os.scandir(directory) as entries:
            for entry in entries:
                path=Path(entry.path);info=path.lstat()
                if stat.S_ISLNK(info.st_mode) and sys.platform=='linux' and os.name!='nt':
                    files[path.relative_to(program).as_posix()]=alias_record(path)
                    continue
                if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                    raise ValueError('Program member contains a link or reparse point')
                if stat.S_ISDIR(info.st_mode):visit(path)
                elif stat.S_ISREG(info.st_mode):
                    files[path.relative_to(program).as_posix()]=regular_record(path)
                else:raise ValueError('Program contains a non-regular member')
    visit(program)
    return files


def program_inventory(program, *, installed_inventory=None):
    """Bind the complete program before native tests; never bless unknown files."""
    program=Path(program).absolute()
    actual=_program_files(program)
    expected=_manifest_files(program)
    if any(name not in actual or {key:actual[name][key] for key in ('size','sha256')}!=record for name,record in expected.items()):
        raise ValueError('Program bytes differ from the build manifest')
    allowed=set(expected)|{'BUILD-MANIFEST.json'}
    if installed_inventory is not None:
        try:from .smoke_installer_vm import installation_inventory_diagnostics
        except ImportError:from smoke_installer_vm import installation_inventory_diagnostics
        diagnostic=installation_inventory_diagnostics(program,installed_inventory['data_directory'],installed_inventory['registered_install_location'])
        required=('program_path_safe','marker_application_matches','marker_data_directory_matches','registry_install_location_matches','inventory_hash_matches_marker','inventory_parse_valid')
        if (not all(diagnostic.get(key) is True for key in required) or diagnostic['blocked_member_count'] or diagnostic['read_error_count'] or any(diagnostic['inventory_delta'][key] for key in ('added_count','missing_count','changed_count'))):
            raise ValueError('Program installation inventory is not a validated unchanged own tree')
        inno={'installation-files.txt','installation-marker.ini','unins000.exe','unins000.dat','unins000.msg'}
        if not {'installation-files.txt','installation-marker.ini','unins000.exe','unins000.dat'}.issubset(actual):
            raise ValueError('Program installation metadata is incomplete')
        allowed|=inno
    if set(actual)!=allowed.intersection(actual) or not (set(expected)|{'BUILD-MANIFEST.json'}).issubset(actual):
        raise ValueError('Program has files outside the exact manifest and validated installer metadata')
    return actual


def require_program_unchanged(program, before):
    if _program_files(program)!=before:
        raise ValueError('Program files changed during native smoke; candidate remains unaccepted')


def sandbox_screenshot_script(package, screenshot, channel):
    channel_option=('channel:'+json.dumps(channel)+',') if channel else ''
    return "const fs=require('fs');const {chromium}=require("+json.dumps(str(package))+");(async()=>{const browser=await chromium.launch({headless:true,chromiumSandbox:true,"+channel_option+"args:['--enable-logging=stderr','--host-resolver-rules=MAP * ~NOTFOUND']});const page=await browser.newPage();await page.setContent('<html><body>Synthetic bundled screenshot</body></html>');await page.screenshot({path:"+json.dumps(str(screenshot))+"});await browser.close();})().catch(error=>{console.error(error);process.exitCode=1;});"


def run_component_diagnostic(command, data, environment):
    """Preserve native failure status while exposing bounded synthetic diagnostics."""
    try:
        subprocess.run(command, check=True, timeout=90, env=environment)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        for name in ('component-phase.txt', 'startup-error.txt'):
            path = Path(data) / name
            if not path.is_file():
                print(name + ': diagnostic file unavailable', flush=True)
                continue
            with path.open('rb') as stream:
                text = stream.read(10000).decode('utf-8', errors='replace')
                if path.stat().st_size > 10000:
                    stream.seek(max(10000, path.stat().st_size - 10000))
                    text += '\n[diagnostic continuation]\n' + stream.read(10000).decode('utf-8', errors='replace')
            replacements = {key: environment.get(key) for key in ('GITHUB_WORKSPACE', 'RUNNER_TEMP', 'USERPROFILE', 'APPDATA', 'HOME')}
            replacements['DIAGNOSTIC_DATA'] = str(Path(data))
            for key, value in sorted(replacements.items(), key=lambda item: len(item[1] or ''), reverse=True):
                if value:
                    for spelling in {value, value.replace('\\', '/'), value.replace('/', '\\')}:
                        text = text.replace(spelling, '<' + key + '>')
            print(name + ':\n' + text, flush=True)
        raise


def run_browser_checks(executable,root,environment,enabled=True):
    if not enabled:
        return {key:'not_run' for key in ('bundled_driver_and_both_browsers','both_bundled_screenshots_without_video_helper','native_speech_imports_cpu')}
    component_data=root/'Native components ö space'
    run_component_diagnostic([str(executable),'--component-test','--data-dir',str(component_data)], component_data, environment)
    component_result=json.loads((component_data/'component-check.json').read_text())
    assert component_result['browsers']['dedicated_headless_shell']['rendered'] and component_result['browsers']['full_chromium']['rendered']
    driver=executable.parent/'_internal'/'playwright'/'driver'
    node=driver/('node.exe' if sys.platform=='win32' else 'node')
    environment['PLAYWRIGHT_BROWSERS_PATH']=str(executable.parent/'components'/'browsers')
    for name,channel in (('shell',None),('chromium','chromium')):
        screenshot=root/('synthetic-'+name+'.png')
        script=root/('sandbox-screenshot-'+name+'.js')
        script.write_text(sandbox_screenshot_script(driver/'package',screenshot,channel),encoding='utf-8')
        command=[str(node),str(script)]
        subprocess.run(command,check=True,timeout=60,env=environment)
        assert screenshot.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    return {'bundled_driver_and_both_browsers':True,'both_bundled_screenshots_without_video_helper':True,'native_speech_imports_cpu':component_result['speech_runtime_imports']}


def smoke(executable,output=None,ocr=True,browsers=True,*,installed_inventory=None):
    executable=Path(executable).absolute()
    for path in (executable,*executable.parents):
        info=path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise ValueError('Program executable path contains a link or reparse point')
        if not (stat.S_ISREG(info.st_mode) if path==executable else stat.S_ISDIR(info.st_mode)):
            raise ValueError('Program executable or ancestor is not a regular file or directory')
    executable=executable.resolve()
    initial_program=program_inventory(executable.parent,installed_inventory=installed_inventory)
    root=Path(output).resolve() if output else Path(tempfile.mkdtemp(prefix='studycenter-public-package-smoke-'))
    root.mkdir(parents=True,exist_ok=True)
    environment=os.environ.copy()
    for variable in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV'):environment.pop(variable,None)
    system=Path(environment.get('SystemRoot','C:/Windows')) if sys.platform=='win32' else None
    if system:
        try:from .package_windows_runtime import check_system
        except ImportError:from package_windows_runtime import check_system
        recipe=json.loads((executable.parent/'SYSTEM-RUNTIME-RECIPE.json').read_text())
        check_system(recipe['system_requirements'],system/'System32')
    allowed_paths=[executable.parent,executable.parent/'_internal',executable.parent/'components'/'ocr']
    allowed_paths += [system/'System32',system] if system else [Path('/usr/bin'),Path('/bin')]
    environment['PATH']=os.pathsep.join(str(path) for path in allowed_paths)
    if not system:environment['LD_LIBRARY_PATH']=str(executable.parent/'components'/'ocr'/'usr'/'lib'/'x86_64-linux-gnu')
    environment['CHROME_LOG_FILE']=str(root/'browser.log')
    environment['XDG_CONFIG_HOME']=str(root/'browser-config')
    environment['XDG_CACHE_HOME']=str(root/'browser-cache')
    library_cache=root/'diagnostic-library-cache'
    for variable,suffix in (('HF_HOME',''),('HF_HUB_CACHE','hub'),('HF_XET_CACHE','xet'),('HF_ASSETS_CACHE','assets')):environment[variable]=str(library_cache/suffix)
    environment['HF_HUB_DISABLE_TELEMETRY']='1';environment['HF_HUB_OFFLINE']='1';environment['HF_HUB_DISABLE_XET']='1'
    self_data=root/'Self test ä space';self_data.mkdir(exist_ok=True)
    subprocess.run([str(executable),'--self-test','--data-dir',str(self_data),'--no-browser'],check=True,timeout=60,env=environment)
    with sqlite3.connect(self_data/'studycenter-public.sqlite3') as con:
        assert con.execute('SELECT count(*) FROM action_receipts').fetchone()[0]==1
    browser_checks=run_browser_checks(executable,root,environment,browsers)
    require_program_unchanged(executable.parent,initial_program)
    data=root/'HTTP test ü space';data.mkdir(exist_ok=True)
    process=subprocess.Popen([str(executable),'--data-dir',str(data),'--port','0','--no-browser'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment)
    checks={'frozen_self_test':True,'developer_python_environment_removed':True,**browser_checks}
    try:
        endpoint_file=data/'runtime.json';deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('Frozen server exited: '+process.communicate()[1].decode(errors='replace'))
            if endpoint_file.exists():
                try:
                    endpoint=json.loads(endpoint_file.read_text())['url']
                    with urllib.request.urlopen(endpoint+'/api/health',timeout=2) as response:health=json.load(response)
                    break
                except (OSError,json.JSONDecodeError):pass
            time.sleep(.2)
        else:raise RuntimeError('Frozen local server did not become ready')
        assert health['application']=='StudyCenter.Public';checks['http_health']=True
        opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with opener.open(endpoint+'/api/session') as response:csrf=json.load(response)['csrf']
        def request(path,payload=None,content_type='application/json'):
            content=json.dumps(payload).encode() if payload is not None and content_type=='application/json' else payload
            headers={'Origin':endpoint,'X-CSRF-Token':csrf,'Content-Type':content_type}
            with opener.open(urllib.request.Request(endpoint+path,data=content,headers=headers),timeout=150) as response:
                body=response.read()
                return json.loads(body) if response.headers.get('Content-Type','').startswith('application/json') else body
        assert request('/api/items')==[]
        actions=[{'type':'create_task','title':'Synthetic package task'}];rid=str(uuid4())
        assert request('/api/actions/preview',{'actions':actions})['preview'] is True
        receipt=request('/api/actions',{'actions':actions,'request_id':rid});assert request('/api/actions',{'actions':actions,'request_id':rid})==receipt
        assert len(request('/api/items'))==1
        request('/api/actions/undo',{'request_id':rid,'undo_id':str(uuid4())});assert request('/api/items')==[]
        checks['action_preview_receipt_undo']=True
        try:
            with urllib.request.urlopen(urllib.request.Request(endpoint+'/api/actions',data=b'{}',headers={'Content-Type':'application/json'})):pass
        except urllib.error.HTTPError as error:assert error.code==403;checks['csrf_countercontrol']=True
        else:raise AssertionError('CSRF-less write was accepted')
        with opener.open(endpoint+'/') as response:assert b'StudyCenter' in response.read();checks['local_ui']=True
        if ocr:
            from PIL import Image,ImageDraw,ImageFont
            image=Image.new('RGB',(1500,500),'white');draw=ImageDraw.Draw(image)
            draw.text((80,150),'SYNTHETIC PACKAGE DOCUMENT 2026',font=ImageFont.load_default(size=42),fill='black')
            raw=io.BytesIO();image.save(raw,format='PDF',resolution=150)
            boundary='synthetic-package-'+uuid4().hex
            body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic-scan.pdf"\r\nContent-Type: application/pdf\r\n\r\n').encode()+raw.getvalue()+f'\r\n--{boundary}--\r\n'.encode()
            doc=request('/api/documents/import',body,'multipart/form-data; boundary='+boundary)
            assert 'SYNTHETIC' in '\n'.join(page['text'] for page in doc['pages']),doc
            checks['bundled_pdf_ocr']=True
            assert request('/api/documents/'+str(doc['id'])+'/original')==raw.getvalue();checks['original_bytes_preserved']=True
        backup=request('/api/backup',{});assert backup.startswith(b'PK');checks['backup_download']=True
        subprocess.run([str(executable),'--data-dir',str(data),'--quit'],check=True,timeout=15,env=environment)
        process.wait(timeout=30);assert process.returncode==0;checks['cooperative_shutdown']=True
    finally:
        if process.poll() is None:
            subprocess.run([str(executable),'--data-dir',str(data),'--quit'],timeout=15,check=False,env=environment)
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)
    result={'platform':sys.platform,'scope':'full' if browsers else 'core_docs_ocr_http','checks':checks,'isolated_directory':str(root)}
    require_program_unchanged(executable.parent,initial_program)
    (root/'package-smoke.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result));return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('executable',type=Path);parser.add_argument('--output',type=Path);parser.add_argument('--no-ocr',action='store_true');parser.add_argument('--core-only',action='store_true',help='Test core, documents, OCR and HTTP; record browser and speech diagnostics as not_run')
    args=parser.parse_args();smoke(args.executable,args.output,not args.no_ocr,not args.core_only)
