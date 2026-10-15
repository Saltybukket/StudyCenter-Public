import asyncio
import importlib.util
import os
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.smoke_release import sandbox_screenshot_script


def frozen_module():
    spec=importlib.util.spec_from_file_location('owned_logging_entry',Path(__file__).resolve().parents[1]/'packaging/frozen_entry.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('channel',[None,'chromium'])
def test_diagnostic_browser_routes_logging_to_stderr_without_parent_changes(tmp_path,channel):
    before=dict(os.environ)
    options=frozen_module().browser_launch_options(channel,SimpleNamespace(cache=tmp_path))
    assert '--enable-logging=stderr' in options['args']
    assert options['chromium_sandbox'] is True
    assert Path(options['env']['CHROME_LOG_FILE']).is_relative_to(tmp_path)
    environment_unchanged=dict(os.environ)==before
    assert environment_unchanged


@pytest.mark.parametrize('channel',[None,'chromium'])
def test_screenshot_browser_routes_logging_to_stderr_with_sandbox(channel):
    script=sandbox_screenshot_script('synthetic-own-package','synthetic-own.png',channel)
    assert '--enable-logging=stderr' in script
    assert 'chromiumSandbox:true' in script and '--no-sandbox' not in script


@pytest.mark.parametrize('visible',[False,True])
@pytest.mark.parametrize('system',[False,True])
def test_source_browser_logs_stderr_in_both_modes_using_own_profile(tmp_path,monkeypatch,visible,system):
    import playwright.async_api
    from studycenter_public.collectors import browser
    from studycenter_public.platform import components
    data=tmp_path/'data';profiles=data/'profiles';profiles.mkdir(parents=True)
    cache=tmp_path/'cache'
    captured={}
    class Context:
        async def close(self):pass
        async def route(self,*args):pass
    class Chromium:
        async def launch_persistent_context(self,profile,**options):
            captured.update(options);captured['profile']=profile
            return Context()
    class Driver:
        chromium=Chromium()
        async def stop(self):pass
    class Starter:
        async def start(self):return Driver()
    async def resolve(function,*args):return function(*args)
    monkeypatch.setattr(playwright.async_api,'async_playwright',Starter)
    monkeypatch.setattr(components,'configure_browser_runtime',lambda paths:None)
    monkeypatch.setattr(browser,'restrict_permissions',lambda path:None)
    monkeypatch.setattr(browser,'linux_system_chrome',lambda:str(tmp_path/'synthetic-system-chrome') if system else None)
    monkeypatch.setattr(browser,'resolve_public',lambda host:['93.184.216.34'])
    monkeypatch.setattr(asyncio,'to_thread',resolve)
    monkeypatch.setenv('CHROME_DEVEL_SANDBOX','synthetic-unused-helper')
    monkeypatch.setenv('LD_PRELOAD','synthetic-unused-preload')
    before=dict(os.environ)
    manager=browser.BrowserManager(SimpleNamespace(data=data,profiles=profiles,cache=cache))
    def complete(coroutine):
        try:coroutine.send(None)
        except StopIteration as finished:return finished.value
        finally:coroutine.close()
        raise AssertionError('Options fixture unexpectedly attempted asynchronous I/O')
    try:
        complete(manager._launch(visible))
        assert '--enable-logging=stderr' in captured['args']
        assert captured['chromium_sandbox'] is True
        assert captured['headless'] is (not visible)
        assert Path(captured['env']['CHROME_LOG_FILE']).is_relative_to(cache)
        assert captured['env'].get('CHROME_DEVEL_SANDBOX') is None
        assert captured['env'].get('LD_PRELOAD') is None
        assert Path(captured['profile']).parent==profiles
        environment_unchanged=dict(os.environ)==before
        assert environment_unchanged
    finally:complete(manager.close())


def synthetic_bundle(tmp_path):
    import json,hashlib
    program=tmp_path/'bundle';program.mkdir()
    executable=program/'StudyCenter-Public.exe'
    executable.write_bytes(b'synthetic executable never launched')
    manifest={'files':{'StudyCenter-Public.exe':{'size':executable.stat().st_size,'sha256':hashlib.sha256(executable.read_bytes()).hexdigest()}}}
    (program/'BUILD-MANIFEST.json').write_text(json.dumps(manifest),encoding='utf8')
    return program


def test_portable_program_gate_refuses_unknown_files_before_native_execution(tmp_path):
    from scripts import smoke_release
    program=synthetic_bundle(tmp_path)
    baseline=smoke_release.program_inventory(program)
    (program/'debug.log').write_bytes(b'synthetic unwanted native log')
    with pytest.raises(ValueError,match='manifest|Program'):
        smoke_release.program_inventory(program)
    assert baseline['StudyCenter-Public.exe']['size']>0


@pytest.mark.parametrize('mutation',['add','change','remove'])
def test_native_program_gate_detects_every_program_byte_mutation(tmp_path,mutation):
    from scripts import smoke_release
    program=synthetic_bundle(tmp_path)
    baseline=smoke_release.program_inventory(program)
    if mutation=='add':(program/'DEPENDENCIES_VALIDATED').touch()
    elif mutation=='change':(program/'StudyCenter-Public.exe').write_bytes(b'synthetic changed bytes')
    else:(program/'StudyCenter-Public.exe').unlink()
    with pytest.raises(ValueError,match='Program'):
        smoke_release.require_program_unchanged(program,baseline)


def test_program_gate_refuses_linked_member_without_reading_target(tmp_path):
    from scripts import smoke_release
    program=synthetic_bundle(tmp_path)
    other=tmp_path/'foreign';other.mkdir();sentinel=other/'sentinel';sentinel.write_bytes(b'synthetic fixture untouched')
    try:(program/'linked').symlink_to(other,target_is_directory=True)
    except OSError:pytest.skip('Own link fixture unavailable')
    with pytest.raises(ValueError,match='link|reparse|Program'):
        smoke_release.program_inventory(program)
    assert sentinel.read_bytes()==b'synthetic fixture untouched'


@pytest.mark.parametrize('linked',['file','ancestor',None])
def test_smoke_checks_lexical_executable_before_every_native_call(tmp_path,monkeypatch,linked):
    from scripts import smoke_release
    program=synthetic_bundle(tmp_path)
    executable=program/'StudyCenter-Public.exe'
    if linked:
        link=tmp_path/('linked-'+linked)
        try:link.symlink_to(executable if linked=='file' else program,target_is_directory=linked=='ancestor')
        except OSError:pytest.skip('Own link fixture unavailable')
        executable=link if linked=='file' else link/executable.name
    calls=[]
    class OwnNativeBoundary(Exception):pass
    def intercept(*args,**kwargs):
        calls.append('attempted')
        raise OwnNativeBoundary()
    monkeypatch.setattr(smoke_release.sys,'platform','linux')
    monkeypatch.setattr(smoke_release.os,'environ',{'PATH':'synthetic-unused-path'})
    monkeypatch.setattr(smoke_release.subprocess,'run',intercept)
    if linked:
        with pytest.raises(ValueError,match='Program|link|reparse'):
            smoke_release.smoke(executable,tmp_path/'output')
        assert calls==[]
        assert not (tmp_path/'output').exists()
    else:
        with pytest.raises(OwnNativeBoundary):
            smoke_release.smoke(executable,tmp_path/'output')
        assert calls==['attempted']


def test_installed_program_gate_accepts_only_validated_finite_inno_metadata(tmp_path,monkeypatch):
    import hashlib
    from scripts import smoke_release,smoke_installer_vm
    program=synthetic_bundle(tmp_path)
    data=tmp_path/'data';data.mkdir()
    (program/'unins000.exe').write_bytes(b'synthetic own Inno executable')
    (program/'unins000.dat').write_bytes(b'synthetic own Inno data')
    (program/'installation-marker.ini').write_bytes(b'synthetic own marker fixture')
    inventory=''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'|'+p.name+'\r\n' for p in program.iterdir() if p.is_file() and p.name not in {'installation-marker.ini','unins000.dat'})
    (program/'installation-files.txt').write_bytes(inventory.encode())
    original=smoke_installer_vm.installation_inventory_diagnostics
    marker={'application':'StudyCenter.Public','data_directory':str(data),'inventory_sha256':hashlib.sha256(inventory.encode()).hexdigest()}
    monkeypatch.setattr(smoke_installer_vm,'installation_inventory_diagnostics',lambda p,d,r:original(p,d,r,marker_values=marker))
    supplied={'data_directory':data,'registered_install_location':str(program)}
    baseline=smoke_release.program_inventory(program,installed_inventory=supplied)
    assert 'unins000.exe' in baseline
    (program/'debug.log').write_bytes(b'synthetic unknown added file')
    with pytest.raises(ValueError,match='Program|inventory|manifest'):
        smoke_release.program_inventory(program,installed_inventory=supplied)


@pytest.mark.skipif(os.name!='nt' or not os.environ.get('STUDYCENTER_TEST_LOG_OWN_BUNDLE'),reason='Explicit own packaged Windows fixture required')
@pytest.mark.parametrize('channel',[None,'chromium'])
def test_native_packaged_logging_emits_stderr_and_preserves_every_program_byte(tmp_path,channel):
    """Real embedded Node/browser, synthetic console warning, no external navigation."""
    from scripts.smoke_release import _program_files
    repo=Path(__file__).resolve().parents[1]
    program=Path(os.environ['STUDYCENTER_TEST_LOG_OWN_BUNDLE']).resolve(strict=True)
    assert program.is_relative_to(repo) and not program.is_symlink()
    manifest=json.loads((program/'BUILD-MANIFEST.json').read_text())
    assert manifest['application']=='StudyCenter-Public'
    driver=program/'_internal/playwright/driver'
    for folder in ['tmp','config','cache']:(tmp_path/folder).mkdir()
    parent=dict(os.environ)
    env=dict(os.environ)
    for name in ['CHROME_DEVEL_SANDBOX','LD_PRELOAD','PYTHONHOME','PYTHONPATH','VIRTUAL_ENV']:env.pop(name,None)
    system=Path(env.get('SystemRoot','C:/Windows'))
    env.update(PATH=os.pathsep.join(map(str,[program,program/'_internal',program/'components/ocr',system/'System32',system])),PLAYWRIGHT_BROWSERS_PATH=str(program/'components/browsers'),DEBUG='pw:browser',CHROME_LOG_FILE=str(tmp_path/'cache'/'own.log'),XDG_CONFIG_HOME=str(tmp_path/'config'),XDG_CACHE_HOME=str(tmp_path/'cache'),TEMP=str(tmp_path/'tmp'),TMP=str(tmp_path/'tmp'))
    script=sandbox_screenshot_script(driver/'package',tmp_path/'synthetic.png',channel)
    # Force an observable native log message; retain the production route/options.
    script=script.replace('args:[','args:["--log-level=0","--v=1",',1)
    script=script.replace('await page.screenshot','await page.evaluate(()=>console.warn("SYNTHETIC OWN NATIVE LOG ROUTE"));await page.screenshot',1)
    script_path=tmp_path/'native-log-control.js';script_path.write_text(script,encoding='utf8')
    before=_program_files(program)
    process=subprocess.run([str(driver/'node.exe'),str(script_path)],cwd=program,env=env,capture_output=True,timeout=90,creationflags=subprocess.CREATE_NO_WINDOW)
    assert process.returncode==0
    native_logging_emitted=any(b'[err]' in line and b'SYNTHETIC OWN NATIVE LOG ROUTE' in line for line in process.stderr.splitlines())
    assert native_logging_emitted
    program_unchanged=_program_files(program)==before
    assert program_unchanged
    assert (tmp_path/'synthetic.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    parent_environment_unchanged=dict(os.environ)==parent
    assert parent_environment_unchanged


@pytest.mark.skipif(sys.platform!='linux',reason='Real Linux file alias required')
def test_manifest_bound_internal_so_alias_is_preserved_and_target_switch_detected(tmp_path):
    import hashlib
    from scripts import smoke_release
    program=synthetic_bundle(tmp_path)
    for name in ['libsample.so.1','libsample.so.2']:(program/name).write_bytes(b'synthetic identical regular library content')
    (program/'libsample.so').symlink_to('libsample.so.1')
    manifest=json.loads((program/'BUILD-MANIFEST.json').read_text())
    for name in ['libsample.so.1','libsample.so.2','libsample.so']:
        raw=(program/name).read_bytes();manifest['files'][name]={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    (program/'BUILD-MANIFEST.json').write_text(json.dumps(manifest))
    initial=smoke_release.program_inventory(program)
    assert initial['libsample.so']['link_target']=='libsample.so.1'
    (program/'libsample.so').unlink();(program/'libsample.so').symlink_to('libsample.so.2')
    with pytest.raises(ValueError,match='Program'):
        smoke_release.require_program_unchanged(program,initial)


@pytest.mark.skipif(sys.platform!='linux',reason='Real Linux file alias required')
@pytest.mark.parametrize('target_kind',['absolute','escape','directory','loop','special'])
def test_program_alias_rejects_nonregular_or_unowned_targets(tmp_path,target_kind):
    import hashlib
    from scripts import smoke_release
    program=synthetic_bundle(tmp_path)
    target=program/'owned';target.write_bytes(b'synthetic regular content')
    if target_kind=='absolute':value=str(target)
    elif target_kind=='escape':
        (tmp_path/'foreign').write_bytes(b'synthetic external fixture');value='../foreign'
    elif target_kind=='directory':target.unlink();target.mkdir();value='owned'
    elif target_kind=='loop':value='libbad.so'
    else:target.unlink();os.mkfifo(target);value='owned'
    (program/'libbad.so').symlink_to(value)
    manifest=json.loads((program/'BUILD-MANIFEST.json').read_text())
    manifest['files']['libbad.so']={'size':25,'sha256':hashlib.sha256(b'synthetic regular content').hexdigest()}
    (program/'BUILD-MANIFEST.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='Program|alias|link'):
        smoke_release.program_inventory(program)


@pytest.mark.skipif(sys.platform!='linux',reason='Real Linux file alias required')
@pytest.mark.parametrize('case',['unregistered','linked_ancestor','windows_mode'])
def test_program_alias_requires_manifest_and_real_linux_mode(tmp_path,monkeypatch,case):
    import hashlib
    from scripts import smoke_release
    program=synthetic_bundle(tmp_path)
    folder=program/'real';folder.mkdir()
    target=folder/'library.so.1';target.write_bytes(b'synthetic own regular content')
    manifest=json.loads((program/'BUILD-MANIFEST.json').read_text())
    record={'size':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
    manifest['files']['real/library.so.1']=record
    if case=='linked_ancestor':
        (program/'indirect').symlink_to('real',target_is_directory=True)
        destination='indirect/library.so.1'
    else:destination='real/library.so.1'
    (program/'library.so').symlink_to(destination)
    if case!='unregistered':manifest['files']['library.so']=record
    (program/'BUILD-MANIFEST.json').write_text(json.dumps(manifest))
    if case=='windows_mode':monkeypatch.setattr(smoke_release.sys,'platform','win32')
    with pytest.raises(ValueError,match='Program|alias|link|reparse'):
        smoke_release.program_inventory(program)
