"""Bounded synthetic slim-package probe; installers only in hosted Windows CI."""
import argparse
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.request
import urllib.parse
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PERSISTED_FILES = ('instance.json', 'studycenter-public.sqlite3', 'core/campusboard.sqlite3',
                   'core/settings.json', 'browser-profiles/synthetic-profile/Default/sentinel.txt',
                   'config/synthetic-credential-sentinel.txt')


def require_persisted_unchanged(before, after):
    if len(before) != len(PERSISTED_FILES) or len(after) != len(PERSISTED_FILES):
        raise AssertionError('Incomplete persisted data inventory')
    changed = [name for name, old, new in zip(PERSISTED_FILES, before, after) if old != new]
    if changed:
        raise AssertionError('Persisted data bytes changed: '+', '.join(changed))

def run_command(command, env, timeout=300):
    print('Run:', ' '.join(map(str, command[:4])), flush=True)
    return subprocess.run(list(map(str, command)), env=env, check=True, timeout=timeout,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def starter(program, *arguments):
    return (['cmd.exe', '/d', '/c', 'call', str(program/'start.cmd')] if os.name == 'nt' else ['sh', str(program/'start.sh')]) + list(arguments)


def uninstall(program, env, log):
    run_command([program/'unins000.exe', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/LOG='+str(log)], env)
    # Inno's launcher can exit before its copied _unins.tmp worker. Wait for
    # actual removal; do not terminate or otherwise bypass the uninstaller.
    deadline = time.monotonic()+120
    while time.monotonic() < deadline:
        if not (program/'start.cmd').exists() and not (program/'.local').exists():
            return
        time.sleep(.2)
    print(log.read_text(errors='replace'), flush=True)
    from studycenter_public.platform.paths import reject_links
    for directory in (program, program/'.local', program/'.local/environments', program/'.local/uv-cache'):
        try:
            directory = reject_links(directory)
        except ValueError:
            continue
        if directory.is_dir():
            entries = []
            for path in directory.iterdir():
                info = path.lstat()
                entries.append({'path': str(path.relative_to(program)), 'attributes': getattr(info, 'st_file_attributes', 0), 'path_length': len(str(path))})
                if len(entries) >= 12:
                    break
            print(json.dumps({'remaining_own_entries': entries}), flush=True)
    raise RuntimeError('Own uninstaller did not remove the program and its environment')


def wait_http(process, data, expected_version):
    deadline = time.monotonic()+60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Prepared slim app exited before readiness')
        try:
            url = json.loads((data/'runtime.json').read_text())['url']
            with urllib.request.urlopen(url+'/api/health', timeout=2) as response:
                health = json.load(response)
            assert health['version'] == expected_version and health['application'] == 'StudyCenter.Public'
            return url
        except (OSError, KeyError, json.JSONDecodeError):
            time.sleep(.1)
    raise RuntimeError('Slim application readiness timed out')


def probe_app(program, data, env, result, expect_legacy_note=False, *, create_note=False, expect_manual_note=False):
    log = (program/'.local/app-smoke.log').open('w', encoding='utf-8')
    process = subprocess.Popen(starter(program, '--no-browser', '--port', '0', '--data-dir', str(data)), env=env, stdout=log, stderr=subprocess.STDOUT,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        expected_version = json.loads((program/'slim-manifest.json').read_text())['version']
        url = wait_http(process, data, expected_version)
        for path in ('/board', '/settings', '/overview'):
            with urllib.request.urlopen(url+path) as response:
                assert response.status == 200
        if create_note:
            req = urllib.request.Request(url+'/api/session', headers={'X-Requested-With': 'fetch'})
            with urllib.request.urlopen(req) as response:
                csrf = json.load(response)['csrf']
            body = urllib.parse.urlencode({'title':'Synthetic installation check', 'request_id':'a'*32}).encode()
            req = urllib.request.Request(url+'/notes', data=body, headers={
                'X-CSRF-Token':csrf, 'X-Requested-With':'fetch', 'Origin':url,
                'Content-Type':'application/x-www-form-urlencoded'})
            with urllib.request.urlopen(req) as response:
                assert response.status == 200 and json.load(response)['url'].startswith('/items/')
        if expect_legacy_note:
            with urllib.request.urlopen(url+'/entries?view=all') as response:
                assert b'Synthetic installation check' in response.read(), 'Migrated note is absent from the installed shared UI'
            with closing(sqlite3.connect(data/'core'/'campusboard.sqlite3')) as connection:
                rows = connection.execute('SELECT identity,payload FROM items').fetchall()
            assert any(identity.startswith('note:beta4:') and json.loads(payload).get('title') == 'Synthetic installation check'
                       for identity, payload in rows), 'Migrated note is absent from the shared database'
            result['legacy_synthetic_note_visible_in_shared_ui_and_database'] = True
        if expect_manual_note:
            with urllib.request.urlopen(url+'/entries?view=all') as response:
                assert b'Synthetic installation check' in response.read()
            with closing(sqlite3.connect(data/'core'/'campusboard.sqlite3')) as connection:
                row = connection.execute('SELECT payload FROM items WHERE identity=?', ('note:manual:'+'a'*32,)).fetchone()
            assert row and json.loads(row[0])['title'] == 'Synthetic installation check'
            result['previous_synthetic_note_visible_in_shared_ui_and_database'] = True
            if not create_note:
                with closing(sqlite3.connect(data/'core'/'campusboard.sqlite3')) as connection:
                    task = connection.execute('SELECT status,pinned,overrides FROM items WHERE identity=?', ('note:manual:'+'a'*32,)).fetchone()
                    quest = connection.execute('SELECT title,steps FROM quests WHERE id=?', ('synthetic-quest',)).fetchone()
                assert task[:2] == ('done', 1) and json.loads(task[2])['description'] == 'Synthetic personal correction'
                step = json.loads(quest[1])[0]
                assert quest[0] == 'Synthetic quest' and step['done'] is True and step['title'] == 'Synthetic edited step' and step['edited_fields'] == ['title']
                assert json.loads((data/'core/settings.json').read_text(encoding='utf-8'))['llm_model'] == 'synthetic-settings'
                result['previous_task_quest_progress_and_settings_after_start'] = True
        result['real_http_core'] = True
    finally:
        run_command(starter(program, '--data-dir', str(data), '--stop'), env)
        try:
            assert process.wait(timeout=30) == 0, 'Prepared app did not stop cleanly'
        finally:
            log.close()
            print((program/'.local/app-smoke.log').read_text(errors='replace')[-4000:], flush=True)


def pdf_probe_code():
    return '''
import pymupdf
from studycenter_public.core.documents import pdf_content
from pathlib import Path
import sys
path=Path(sys.argv[1])/'synthetic-package-check.pdf'
path.parent.mkdir(parents=True, exist_ok=True)
d=pymupdf.open();page=d.new_page();page.insert_text((72,72),'Synthetic slim native PDF text. No OCR runtime needed.')
d.save(path);d.close()
try:
    pages=pdf_content(path, None)
    assert 'Synthetic slim' in pages[0]['text']
finally:
    path.unlink()
'''


def probe_ice_import(program, root, env, result):
    """Exercise the installed launcher with the shared synthetic format-1 fixture."""
    fixture_dir = root/'ice-fixture'
    fixture_dir.mkdir()
    spec = importlib.util.spec_from_file_location('synthetic_ice_fixture', ROOT/'tests/test_ice_migration.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    backup = fixture.backup(fixture_dir)
    original = hashlib.sha256(backup.read_bytes()).hexdigest()
    destination = root/'ice-import'
    command = starter(program, '--import-ice', '--backup', str(backup), '--destination', str(destination))
    run_command(command, env)
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == original
    with closing(sqlite3.connect((destination/'core/campusboard.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)) as con:
        assert con.execute("SELECT count(*) FROM items WHERE identity='note:manual:123'").fetchone()[0] == 1
    marker = destination/'synthetic-retain.txt'
    marker.write_text('keep', encoding='utf-8')
    refused = subprocess.run(list(map(str, command)), env=env, timeout=300,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert refused.returncode != 0 and marker.read_text(encoding='utf-8') == 'keep'
    result['installed_ice_import_and_existing_destination_refusal'] = True


def seed_upgrade_evidence(data):
    """Add one bounded synthetic original and evidence link to the old instance."""
    content = b'Synthetic pre-update original evidence'
    digest = hashlib.sha256(content).hexdigest()
    documents = data/'core/documents'
    documents.mkdir(exist_ok=True)
    original = documents/(digest+'.txt')
    original.write_bytes(content)
    # sqlite3.Connection.__exit__ commits but does not close. Close explicitly
    # before the byte baseline so a later GC cannot checkpoint a pending WAL.
    with closing(sqlite3.connect(data/'core/campusboard.sqlite3')) as con, con:
        item = con.execute('SELECT id FROM items WHERE identity=?', ('note:manual:'+'a'*32,)).fetchone()[0]
        source = con.execute("INSERT INTO sources(kind,title,url) VALUES('manual','Synthetic source','import:package-smoke')").lastrowid
        doc = con.execute("INSERT INTO documents(source_id,url,title,mime,hash,fetched_at,original_path) VALUES(?,'import:package-smoke','Synthetic original','text/plain',?,'2026-01-01',?)", (source,digest,str(original))).lastrowid
        con.execute("INSERT INTO item_evidence(item_id,document_id,page,anchor,quote,payload) VALUES(?,?,1,'p1','synthetic','{}')", (item,doc))
        con.execute("UPDATE items SET status='done', pinned=1, overrides=? WHERE id=?", (json.dumps({'description':'Synthetic personal correction'}), item))
        con.execute("INSERT INTO quests(id,request_id,input_hash,title,mode,horizon,daily_minutes,start_date,end_date,state,sources,steps,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ('synthetic-quest','synthetic-request','synthetic-hash','Synthetic quest','custom',7,30,'2026-01-01','2026-01-07','active','[]',json.dumps([{'id':'synthetic-step','title':'Synthetic edited step','detail':'Synthetic completed exercise','topic':'Synthetic topic','materials':[],'item_id':None,'done':True,'completed_at':'2026-01-02','edited_fields':['title'],'minutes':20,'day_offset':1}]),'2026-01-01','2026-01-01'))
    settings = data/'core/settings.json'
    settings.write_text(json.dumps({'stt_engine':'whisper','stt_model':'base','stt_device':'cpu','llm_model':'synthetic-settings'}), encoding='utf-8')
    for name in ('browser-profiles/synthetic-profile/Default/sentinel.txt', 'config/synthetic-credential-sentinel.txt'):
        marker = data/name
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('synthetic non-secret retained local state', encoding='utf-8')
    return digest, content


def verify_upgrade_backup(data, root, expected):
    from studycenter_public.core.backup import inspect_archive
    archives = sorted((data/'core/backups').glob('CampusBoard-*.zip'), key=lambda path: path.stat().st_mtime_ns)
    assert archives, 'Core backup was not created before update'
    destination = root/('inspected-core-backup-'+uuid4().hex)
    manifest = inspect_archive(archives[-1], destination)
    digest, content = expected
    assert manifest['counts']['documents'] >= 1 and manifest['counts']['item_evidence'] >= 1
    assert (destination/'documents'/(digest+'.txt')).read_bytes() == content
    with closing(sqlite3.connect(destination/'campusboard.sqlite3')) as con:
        assert con.execute('SELECT count(*) FROM item_evidence').fetchone()[0] >= 1
        row = con.execute('SELECT payload FROM items WHERE identity=?', ('note:manual:'+'a'*32,)).fetchone()
        assert row and json.loads(row[0])['title'] == 'Synthetic installation check'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--assets', required=True, type=Path)
    parser.add_argument('--installer', type=Path)
    parser.add_argument('--previous-installer', type=Path)
    parser.add_argument('--previous-archive', type=Path)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--expected-source', required=True)
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise RuntimeError('Run this package lifecycle only on an existing disposable GitHub-hosted runner')
    assets = args.assets.resolve(strict=True)
    root = Path(os.environ['RUNNER_TEMP'])/('StudyCenter-Public-slim-'+uuid4().hex)
    root.mkdir()
    program, data = root/'program', root/'data'
    env = dict(os.environ, STUDYCENTER_PUBLIC_RELEASE_ASSETS=str(assets))
    env.pop('GH_TOKEN', None)
    env.pop('GITHUB_TOKEN', None)
    manifest_path = assets / ('slim-windows-x86_64.json' if os.name == 'nt' else 'slim-linux-x86_64.json')
    source_manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    assert source_manifest.get('source_git_sha') == args.expected_source
    result = {'source_git_sha': args.expected_source, 'asset_manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(), 'environment': os.environ.get('RUNNER_OS'),
              'real_tu_accounts': False, 'models_downloaded': False, 'windows11_native': False}
    def persisted_bytes():
        return tuple(path.read_bytes() if path.exists() else None for path in
                     (data/name for name in PERSISTED_FILES))
    previous_version = None
    upgrade_evidence = None
    if os.name == 'nt':
        if not args.installer:
            parser.error('--installer is required on Windows')
        if args.previous_installer:
            result['previous_installer_sha256'] = hashlib.sha256(args.previous_installer.read_bytes()).hexdigest()
            previous_env = dict(env, STUDYCENTER_PUBLIC_RELEASE_ASSETS=str(args.previous_installer.parent))
            run_command([args.previous_installer, '/CURRENTUSER', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/DIR='+str(program), '/DATADIR='+str(data)], previous_env)
            run_command(starter(program, '--self-test', '--no-browser', '--data-dir', str(data)), previous_env)
            previous_version = json.loads((program/'slim-manifest.json').read_text())['version']
            if previous_version == '0.1.0b5':
                probe_app(program, data, previous_env, {}, create_note=True, expect_manual_note=True)
                upgrade_evidence = seed_upgrade_evidence(data)
            elif previous_version != '0.1.0b4':
                raise ValueError('Unreviewed previous version')
            before = persisted_bytes()
        else:
            before = None
        # A missing Microsoft prerequisite aborts silent mode. This probe never
        # approves or launches an external Microsoft/system installer.
        install = [args.installer, '/CURRENTUSER', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/DIR='+str(program), '/DATADIR='+str(data)]
        if before:
            # An invalid supplied application wheel makes setup fail before
            # promotion. Check the real installer rollback over an old full app.
            broken = root/'broken-own-assets'
            broken.mkdir()
            wheels = list(assets.glob('studycenter_public-*-py3-none-any.whl'))
            if len(wheels) != 1:
                raise ValueError('Expected exactly one application wheel')
            wheel_name = wheels[0].name
            for name in (wheel_name, 'proxy_tools-0.1.0-py3-none-any.whl', 'requirements-windows.lock'):
                shutil.copy2(assets/name, broken/name)
            (broken/wheel_name).write_bytes(b'Synthetic interrupted release wheel')
            old_hash = hashlib.sha256((program/'start.cmd').read_bytes()).hexdigest()
            failed = subprocess.run(list(map(str, install+['/LOG='+str(root/'failed-update.log')])),
                                    env=dict(env, STUDYCENTER_PUBLIC_RELEASE_ASSETS=str(broken)), timeout=300,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            print((root/'failed-update.log').read_text(errors='replace'), flush=True)
            assert failed.returncode != 0, 'Corrupt release wheel was accepted'
            assert hashlib.sha256((program/'start.cmd').read_bytes()).hexdigest() == old_hash
            require_persisted_unchanged(before, persisted_bytes())
            if upgrade_evidence:
                verify_upgrade_backup(data, root, upgrade_evidence)
                result['failed_update_retains_restorable_core_backup'] = True
            run_command(starter(program, '--self-test', '--no-browser', '--data-dir', str(root/'old-after-failed-update')), previous_env)
            result['failed_installer_update_retains_old_functioning_core'] = True
        run_command(install+['/LOG='+str(root/'first-install.log')], env)
        if before:
            require_persisted_unchanged(before, persisted_bytes())
            assert (program/'start.cmd').is_file()
            if upgrade_evidence:
                verify_upgrade_backup(data, root, upgrade_evidence)
                result['successful_update_retains_restorable_core_backup'] = True
            result['previous_transition_preserves_identity_and_database'] = previous_version
    else:
        if not args.archive:
            parser.error('--archive is required on Linux')
        if args.previous_archive:
            result['previous_archive_sha256'] = hashlib.sha256(args.previous_archive.read_bytes()).hexdigest()
            previous_env = dict(env, STUDYCENTER_PUBLIC_RELEASE_ASSETS=str(args.previous_archive.parent))
            with tarfile.open(args.previous_archive) as archive:
                archive.extractall(root, filter='data')
            program = root/'StudyCenter-Public'
            previous_version = json.loads((program/'slim-manifest.json').read_text())['version']
            if previous_version != '0.1.0b5':
                raise ValueError('Unreviewed previous Linux version')
            run_command(starter(program, '--setup-only', '--data-dir', str(data)), previous_env)
            probe_app(program, data, previous_env, {}, create_note=True, expect_manual_note=True)
            upgrade_evidence = seed_upgrade_evidence(data)
            before = persisted_bytes()
            candidate = root/'candidate'
            candidate.mkdir()
            with tarfile.open(args.archive) as archive:
                archive.extractall(candidate, filter='data')
            for path in (candidate/'StudyCenter-Public').iterdir():
                if not path.is_file() or path.is_symlink():
                    raise ValueError('Unexpected candidate bootstrap entry')
                shutil.copy2(path, program/path.name)
            run_command(starter(program, '--setup-only', '--update', '--data-dir', str(data)), env)
            require_persisted_unchanged(before, persisted_bytes())
            verify_upgrade_backup(data, root, upgrade_evidence)
            result['successful_update_retains_restorable_core_backup'] = True
            result['previous_transition_preserves_identity_and_database'] = previous_version
        else:
            with tarfile.open(args.archive) as archive:
                archive.extractall(root, filter='data')
            program = root/'StudyCenter-Public'
            run_command(starter(program, '--setup-only', '--data-dir', str(data)), env)
    receipt = json.loads((program/'.local/installation.json').read_text())
    manifest = json.loads((program/'slim-manifest.json').read_text())
    assert manifest['source_git_sha'] == args.expected_source
    assert receipt['version'] == manifest['version']
    python = receipt['python']
    run_command([python, '-I', '-c', 'import importlib.util; assert all(importlib.util.find_spec(m) is None for m in ("playwright","faster_whisper","sherpa_onnx","av","pytest"))'], env)
    assert not (program/'.local/components').exists()
    result['base_without_optional_components'] = True
    result['python_origin'] = receipt['python_origin']
    result['base_installed_bytes_including_caches'] = sum(p.stat().st_size for p in program.rglob('*') if p.is_file())
    # Native text-PDF extraction is a real isolated interpreter/application call.
    code = pdf_probe_code()
    run_command([python, '-I', '-c', code, data], env)
    result['native_pdf_without_ocr'] = True
    probe_ice_import(program, root, env, result)
    offline = dict(env, UV_OFFLINE='1')
    probe_app(program, data, offline, result, expect_legacy_note=previous_version == '0.1.0b4', expect_manual_note=previous_version == '0.1.0b5')
    # Isolated child PATH hides discoverable system Python on the hosted runner;
    # it never uninstalls or edits an existing interpreter. Record actual choice.
    private_program = root/'private-runtime-program'
    shutil.copytree(program, private_program, ignore=shutil.ignore_patterns('.local', 'unins*', 'installation-*'))
    private_env = dict(env)
    if os.name == 'nt':
        windows = Path(os.environ['SystemRoot'])
        private_env['PATH'] = os.pathsep.join(map(str, (windows/'System32', windows, windows/'System32/WindowsPowerShell/v1.0')))
    else:
        tools = root/'bootstrap-tools'
        tools.mkdir()
        for name in ('dirname','uname','mkdir','curl','tar','gzip','sha256sum','mv','rm'):
            original = shutil.which(name)
            if not original:
                raise RuntimeError('Existing bootstrap tool missing: '+name)
            (tools/name).symlink_to(original)
        private_env['PATH'] = str(tools)
    private_command = (['cmd.exe','/d','/c','call',str(private_program/'start.cmd')] if os.name == 'nt' else ['/bin/sh',str(private_program/'start.sh')])
    run_command(private_command+['--setup-only','--data-dir',str(root/'private-runtime-data')], private_env)
    private_receipt = json.loads((private_program/'.local/installation.json').read_text())
    result['restricted_child_path_python_origin'] = private_receipt['python_origin']
    result['private_runtime_installed_bytes_including_cache'] = sum(p.stat().st_size for p in private_program.rglob('*') if p.is_file())
    if private_receipt['python_origin'] == 'managed':
        result['real_private_runtime_setup'] = True
    else:
        result['real_private_runtime_setup'] = False
        result['private_runtime_limit'] = 'A compatible interpreter remains discoverable in the hosted image registry; absence case is covered by isolated selection tests.'
    identity_file = data/'instance.json'
    legacy_file = data/'studycenter-public.sqlite3'
    core_file = data/'core'/'campusboard.sqlite3'
    baseline = persisted_bytes()
    if os.name == 'nt':
        run_command(install+['/LOG='+str(root/'update.log')], env)
        require_persisted_unchanged(baseline, persisted_bytes())
        if legacy_file.exists():
            assert list((data/'backups').glob('before-update-*/studycenter-public.sqlite3'))
        result['slim_update_preserves_data_and_backup'] = True
    for name in ('ocr', 'browser', 'speech'):
        run_command(starter(program, '--component', name, '--allow-download', '--setup-only'), env, timeout=600)
        receipt = json.loads((program/'.local/installation.json').read_text())
        python = receipt['python']
        # Second setup is offline and must reuse both archives and Python wheels.
        run_command(starter(program, '--component', name, '--allow-download', '--setup-only'), offline)
    result['optional_components_real_install_and_offline_repeat'] = True
    result['all_components_installed_bytes_including_cache'] = sum(p.stat().st_size for p in program.rglob('*') if p.is_file())
    result['component_network_scope'] = 'Own draft archives fetched once by gh; verified read-only asset seed. Python extras fetched by uv. No model weights.'
    probe_app(program, data, offline, result, expect_legacy_note=previous_version == '0.1.0b4', expect_manual_note=previous_version == '0.1.0b5')
    if os.name == 'nt':
        preserved = persisted_bytes()
        notices = list((program/'.local/components').rglob('*COPYING.LGPL-2.1-or-later'))
        assert notices and max(len(str(p)) for p in notices) > 260
        result['long_component_notice_path_chars'] = max(len(str(p)) for p in notices)
        foreign = root/'foreign-junction-fixture'
        foreign.mkdir()
        marker = foreign/'keep.txt'
        marker.write_bytes(b'Synthetic foreign junction target')
        link = program/'.local/refused-junction'
        run_command(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(foreign)], env)
        assert link.is_junction()
        refused_log = root/'junction-refusal.log'
        refused = subprocess.run([str(program/'unins000.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/LOG='+str(refused_log)],
            env=env, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
        assert refused.returncode != 0
        assert 'Public own safety refused member' in refused_log.read_text(errors='replace')
        assert (program/'start.cmd').exists() and marker.read_bytes() == b'Synthetic foreign junction target'
        os.rmdir(link)  # Remove only this own junction, never recurse into its target.
        result['junction_refused_without_foreign_deletion'] = True
        uninstall(program, env, root/'uninstall.log')
        assert marker.read_bytes() == b'Synthetic foreign junction target'
        require_persisted_unchanged(preserved, persisted_bytes())
        assert not (program/'start.cmd').exists() and not (program/'.local').exists()
        result['uninstall_retains_data_removes_own_environment'] = True
        if before:
            # The failed first setup leaves only its narrowly marked own staging
            # directory. The same installer must repair it without manual cleanup.
            fresh, fresh_data = root/'fresh retry program', root/'fresh retry data'
            fresh_install = [args.installer, '/CURRENTUSER', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/DIR='+str(fresh), '/DATADIR='+str(fresh_data)]
            failed = subprocess.run(list(map(str, fresh_install+['/LOG='+str(root/'fresh-failure.log')])),
                env=dict(env, STUDYCENTER_PUBLIC_RELEASE_ASSETS=str(broken)), timeout=300,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            print((root/'fresh-failure.log').read_text(errors='replace'), flush=True)
            assert failed.returncode != 0
            assert (fresh/'.setup-pending.ini').is_file() and not (fresh/'start.cmd').exists()
            run_command(fresh_install+['/LOG='+str(root/'fresh-retry.log')], env)
            assert not (fresh/'.setup-pending.ini').exists()
            retained = Path(json.loads((fresh/'.local/installation.json').read_text())['manifest'])
            assert retained.is_relative_to(fresh/'.local/manifests') and retained.is_file()
            probe_app(fresh, fresh_data, offline, {})
            uninstall(fresh, env, root/'fresh-uninstall.log')
            assert not (fresh/'.local').exists()
            result['interrupted_first_install_repairs_on_retry'] = True
    print(json.dumps(result, indent=2), flush=True)
    (root/'slim-result.json').write_text(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
