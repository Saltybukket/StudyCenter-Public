"""Bounded synthetic slim-package probe; installers only in hosted Windows CI."""
import argparse
import hashlib
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
from uuid import uuid4


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


def wait_http(process, data):
    deadline = time.monotonic()+60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Prepared slim app exited before readiness')
        try:
            url = json.loads((data/'runtime.json').read_text())['url']
            with urllib.request.urlopen(url+'/api/health', timeout=2) as response:
                health = json.load(response)
            assert health['version'] == '0.1.0b3' and health['application'] == 'StudyCenter.Public'
            return url
        except (OSError, KeyError, json.JSONDecodeError):
            time.sleep(.1)
    raise RuntimeError('Slim application readiness timed out')


def probe_app(program, data, env, result):
    log = (program/'.local/app-smoke.log').open('w', encoding='utf-8')
    process = subprocess.Popen(starter(program, '--no-browser', '--port', '0', '--data-dir', str(data)), env=env, stdout=log, stderr=subprocess.STDOUT,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        url = wait_http(process, data)
        for path in ('/board', '/settings', '/overview'):
            with urllib.request.urlopen(url+path) as response:
                assert response.status == 200
        result['real_http_core'] = True
    finally:
        run_command(starter(program, '--data-dir', str(data), '--stop'), env)
        try:
            assert process.wait(timeout=30) == 0, 'Prepared app did not stop cleanly'
        finally:
            log.close()
            print((program/'.local/app-smoke.log').read_text(errors='replace')[-4000:], flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--assets', required=True, type=Path)
    parser.add_argument('--installer', type=Path)
    parser.add_argument('--previous-installer', type=Path)
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
    result = {'source_git_sha': args.expected_source, 'environment': os.environ.get('RUNNER_OS'),
              'real_tu_accounts': False, 'models_downloaded': False, 'windows11_native': False}
    if os.name == 'nt':
        if not args.installer:
            parser.error('--installer is required on Windows')
        if args.previous_installer:
            run_command([args.previous_installer, '/CURRENTUSER', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/DIR='+str(program), '/DATADIR='+str(data)], env)
            run_command([program/'StudyCenter-Public.exe', '--self-test', '--no-browser', '--data-dir', data], env)
            before = (data/'instance.json').read_bytes(), (data/'studycenter-public.sqlite3').read_bytes()
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
            for name in ('studycenter_public-0.1.0b3-py3-none-any.whl', 'proxy_tools-0.1.0-py3-none-any.whl', 'requirements-windows.lock'):
                shutil.copy2(assets/name, broken/name)
            (broken/'studycenter_public-0.1.0b3-py3-none-any.whl').write_bytes(b'Synthetic interrupted release wheel')
            old_hash = hashlib.sha256((program/'StudyCenter-Public.exe').read_bytes()).hexdigest()
            failed = subprocess.run(list(map(str, install+['/LOG='+str(root/'failed-update.log')])),
                                    env=dict(env, STUDYCENTER_PUBLIC_RELEASE_ASSETS=str(broken)), timeout=300,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            print((root/'failed-update.log').read_text(errors='replace'), flush=True)
            assert failed.returncode != 0, 'Corrupt release wheel was accepted'
            assert hashlib.sha256((program/'StudyCenter-Public.exe').read_bytes()).hexdigest() == old_hash
            assert ((data/'instance.json').read_bytes(), (data/'studycenter-public.sqlite3').read_bytes()) == before
            run_command([program/'StudyCenter-Public.exe', '--self-test', '--no-browser', '--data-dir', root/'old-after-failed-update'], env)
            result['failed_installer_update_retains_old_functioning_core'] = True
        run_command(install+['/LOG='+str(root/'first-install.log')], env)
        if before:
            assert ((data/'instance.json').read_bytes(), (data/'studycenter-public.sqlite3').read_bytes()) == before
            assert not (program/'StudyCenter-Public.exe').exists()
            result['beta2_transition_preserves_identity_and_database'] = True
    else:
        if not args.archive:
            parser.error('--archive is required on Linux')
        with tarfile.open(args.archive) as archive:
            archive.extractall(root, filter='data')
        program = root/'StudyCenter-Public'
        run_command(starter(program, '--setup-only', '--data-dir', str(data)), env)
    receipt = json.loads((program/'.local/installation.json').read_text())
    manifest = json.loads((program/'slim-manifest.json').read_text())
    assert manifest['source_git_sha'] == args.expected_source
    assert receipt['version'] == '0.1.0b3'
    python = receipt['python']
    run_command([python, '-I', '-c', 'import importlib.util; assert all(importlib.util.find_spec(m) is None for m in ("playwright","faster_whisper","av","pytest"))'], env)
    assert not (program/'.local/components').exists()
    result['base_without_optional_components'] = True
    result['python_origin'] = receipt['python_origin']
    result['base_installed_bytes_including_caches'] = sum(p.stat().st_size for p in program.rglob('*') if p.is_file())
    # Native text-PDF extraction is a real isolated interpreter/application call.
    code = '''
import pymupdf
from studycenter_public.platform.paths import AppPaths
from studycenter_public.storage.db import Database
from studycenter_public.services.documents import DocumentService
from pathlib import Path
import sys
p=AppPaths.create(Path(sys.argv[1]))
d=pymupdf.open();page=d.new_page();page.insert_text((72,72),'Synthetic slim native PDF text. No OCR runtime needed.')
s=DocumentService(Database(p.database),p)
r=s.import_bytes(d.tobytes(),title='Synthetic PDF',mime='application/pdf')
assert 'Synthetic slim' in s.db.pages(r['id'])[0]['text']
'''
    run_command([python, '-I', '-c', code, data], env)
    result['native_pdf_without_ocr'] = True
    offline = dict(env, UV_OFFLINE='1')
    probe_app(program, data, offline, result)
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
    baseline = (data/'instance.json').read_bytes(), (data/'studycenter-public.sqlite3').read_bytes()
    if os.name == 'nt':
        run_command(install+['/LOG='+str(root/'update.log')], env)
        assert ((data/'instance.json').read_bytes(), (data/'studycenter-public.sqlite3').read_bytes()) == baseline
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
    probe_app(program, data, offline, result)
    if os.name == 'nt':
        preserved = (data/'instance.json').read_bytes(), (data/'studycenter-public.sqlite3').read_bytes()
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
        assert ((data/'instance.json').read_bytes(), (data/'studycenter-public.sqlite3').read_bytes()) == preserved
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
