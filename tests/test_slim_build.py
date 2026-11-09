"""Release tag and cached component identity boundaries."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from scripts import build_slim


def test_slim_diagnostics_do_not_fail_on_unicode_with_legacy_console():
    code = ("import os,sys; from scripts.smoke_slim import configure_diagnostics,run_command; "
            "configure_diagnostics(); run_command([sys.executable,'-c','pass','\\u5b66\\u7fd2'],dict(os.environ))")
    env = dict(os.environ, PYTHONIOENCODING='cp1252:strict')
    result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode('ascii', errors='replace')
    assert b'\\u5b66\\u7fd2' in result.stdout


def test_slim_page_probe_consumes_complete_http_responses():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from scripts.smoke_slim import check_core_pages

    completed = []
    all_completed = threading.Event()
    body = b'synthetic-page' * 131072

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return
            completed.append(self.path)
            if len(completed) == 3:
                all_completed.set()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        check_core_pages('http://127.0.0.1:'+str(server.server_port))
        assert all_completed.wait(5)
        assert completed == ['/board', '/settings', '/overview']
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize('actual,accepted', [('0.1.0b4', True), ('0.1.0b3', False)])
def test_package_readiness_is_bound_to_candidate_version(tmp_path, monkeypatch, actual, accepted):
    from scripts import smoke_slim
    (tmp_path/'runtime.json').write_text(json.dumps({'url':'http://127.0.0.1:8767'}))
    process = type('Running', (), {'poll': lambda self: None})()
    monkeypatch.setattr(smoke_slim.urllib.request, 'urlopen', lambda *a, **kw:
                        io.BytesIO(json.dumps({'version':actual, 'application':'StudyCenter.Public'}).encode()))
    if accepted:
        assert smoke_slim.wait_http(process, tmp_path, '0.1.0b4') == 'http://127.0.0.1:8767'
    else:
        with pytest.raises(AssertionError):
            smoke_slim.wait_http(process, tmp_path, '0.1.0b4')


def test_release_tag_requires_exact_beta_syntax():
    assert build_slim.release_version('v0.1.0-beta.4') == '0.1.0b4'
    for tag in ('v0.1.0-beta.04', 'v0.1.0-beta.4/other', '0.1.0b4', 'v0.1.0-beta.0'):
        with pytest.raises(ValueError):
            build_slim.release_version(tag)


def test_cached_archives_require_pinned_origin_hash_and_size(tmp_path, monkeypatch):
    platform = 'linux'
    hashes = {}
    records = {}
    for component in ('browser', 'ocr', 'speech'):
        suffix = 'speech-notices' if component == 'speech' else component
        name = f'studycenter-public-0.1.0b3-linux-x86_64-{suffix}.tar.gz'
        payload = component.encode()
        (tmp_path/name).write_bytes(payload)
        hashes[component] = hashlib.sha256(payload).hexdigest()
        records[component] = {'archive': {'filename': name, 'url': build_slim.ORIGIN + 'v0.1.0-beta.3/' + name,
                                         'sha256': hashes[component], 'size': len(payload)}}
    monkeypatch.setitem(build_slim.BETA3_SHA256, platform, hashes)
    assert build_slim.verify_cached_components(records, platform, tmp_path) == records
    records['ocr']['archive']['url'] = 'https://example.org/' + records['ocr']['archive']['filename']
    with pytest.raises(ValueError, match='identity or origin'):
        build_slim.verify_cached_components(records, platform, tmp_path)
    records['ocr']['archive']['url'] = build_slim.ORIGIN + 'v0.1.0-beta.3/' + records['ocr']['archive']['filename']
    (tmp_path/records['ocr']['archive']['filename']).write_bytes(b'bad')
    with pytest.raises(ValueError, match='archive changed'):
        build_slim.verify_cached_components(records, platform, tmp_path)


def test_source_wheel_requires_exact_committed_package_bytes(tmp_path, monkeypatch):
    import tarfile
    import zipfile
    import pytest
    from scripts import build_slim
    source = io.BytesIO()
    with tarfile.open(fileobj=source, mode='w') as tree:
        payload = b'synthetic committed source'
        entry = tarfile.TarInfo('src/studycenter_public/__init__.py')
        entry.size = len(payload)
        tree.addfile(entry, io.BytesIO(payload))
    monkeypatch.setattr(build_slim.subprocess, 'check_output', lambda *a, **k: source.getvalue())
    wheel = tmp_path/'fixture.whl'
    def write(content, extra=False):
        with zipfile.ZipFile(wheel, 'w') as archive:
            archive.writestr('studycenter_public/__init__.py', content)
            if extra: archive.writestr('studycenter_public/foreign.py', b'untracked')
    write(payload); build_slim.verify_source_wheel(wheel, 'synthetic-commit')
    write(b'changed')
    with pytest.raises(ValueError, match='changed='): build_slim.verify_source_wheel(wheel, 'synthetic-commit')
    write(payload, True)
    with pytest.raises(ValueError, match='extra='): build_slim.verify_source_wheel(wheel, 'synthetic-commit')
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('studycenter_public/__init__.py', b'altered')
        with pytest.warns(UserWarning, match='Duplicate name'):
            archive.writestr('studycenter_public/__init__.py', payload)
    with pytest.raises(ValueError, match='duplicate'):
        build_slim.verify_source_wheel(wheel, 'synthetic-commit')


def test_pdf_probe_creates_missing_data_root_and_removes_fixture(tmp_path, monkeypatch):
    import sys
    from scripts.smoke_slim import pdf_probe_code
    data = tmp_path/'new'/'data'
    assert not data.exists()
    monkeypatch.setattr(sys, 'argv', ['synthetic-pdf-probe', str(data)])
    exec(compile(pdf_probe_code(), '<synthetic-pdf-probe>', 'exec'), {})
    assert data.is_dir()
    assert not (data/'synthetic-package-check.pdf').exists()


def test_component_download_rejects_corrupt_bytes_without_promoting(tmp_path, monkeypatch):
    from scripts import prepare_slim_assets
    destination = tmp_path/'component.tar.gz'
    destination.write_bytes(b'existing')
    monkeypatch.setattr(prepare_slim_assets.urllib.request, 'urlopen',
                        lambda *a, **k: io.BytesIO(b'wrong'))
    with pytest.raises(ValueError, match='SHA256'):
        prepare_slim_assets.fetch('https://example.invalid/component', destination, 5,
                                  hashlib.sha256(b'right').hexdigest())
    assert destination.read_bytes() == b'existing'
    assert not (tmp_path/'component.tar.gz.partial').exists()


def test_installer_scan_refuses_unreviewed_decoder(tmp_path, monkeypatch):
    from scripts import scan_slim_installer
    installer = tmp_path/'candidate.exe'
    installer.write_bytes(b'synthetic installer')
    bootstrap = tmp_path/'bootstrap'
    bootstrap.mkdir()
    decoder = tmp_path/'decoder.exe'
    decoder.write_bytes(b'unreviewed decoder')
    monkeypatch.setattr(scan_slim_installer.subprocess, 'run', lambda *a, **k: pytest.fail('decoder was executed'))
    with pytest.raises(ValueError, match='decoder differs'):
        scan_slim_installer.scan(installer, bootstrap, tmp_path/'scan', decoder)


def test_shared_self_test_checks_existing_database_read_only(tmp_path):
    import sqlite3
    from scripts.smoke_release import verify_self_test_database
    with pytest.raises(AssertionError, match='not initialized'):
        verify_self_test_database(tmp_path)
    assert not (tmp_path/'core').exists()
    core = tmp_path/'core'
    core.mkdir()
    with sqlite3.connect(core/'campusboard.sqlite3') as con:
        for name in ('courses', 'sources', 'documents', 'items'):
            con.execute(f'CREATE TABLE {name}(id INTEGER)')
    verify_self_test_database(tmp_path)
    with sqlite3.connect(core/'campusboard.sqlite3') as con:
        con.execute('DROP TABLE items')
    with pytest.raises(AssertionError):
        verify_self_test_database(tmp_path)


def test_upgrade_fixture_closes_wal_before_exact_byte_baseline(tmp_path):
    from contextlib import closing
    import gc
    import importlib.util
    import sqlite3
    from studycenter_public.core.db import Database
    from studycenter_public.core.models import Candidate
    from studycenter_public.platform.paths import AppPaths
    from scripts.smoke_slim import PERSISTED_FILES, seed_upgrade_evidence, verify_upgrade_backup, require_persisted_unchanged
    data = tmp_path/'data'
    paths = AppPaths.create(data)
    core = data/'core'
    (core/'documents').mkdir(parents=True, exist_ok=True)
    database = core/'campusboard.sqlite3'
    Database(database)
    payload = Candidate(title='Synthetic installation check', kind='task', quote='note', page=1, anchor='note').model_dump_json()
    with closing(sqlite3.connect(database)) as con, con:
        con.execute("INSERT INTO items(identity,payload,created_at,changed_at) VALUES(?,?,?,?)",
                    ('note:manual:'+'a'*32, payload, '2026-01-01', '2026-01-01'))
    expected = seed_upgrade_evidence(data)
    from studycenter_public.core.quests import QuestService, Step
    dashboard = QuestService(Database(database), None, None, search=object()).dashboard(current_items=[])
    assert dashboard['quests'][0]['completed_count'] == 1
    fixture_step = dashboard['quests'][0]['steps'][0]
    assert fixture_step['title'] == 'Synthetic edited step'
    Step.model_validate({name: fixture_step[name] for name in Step.model_fields})
    assert dashboard['quests'][0]['mode'] == 'custom' and dashboard['quests'][0]['daily_minutes'] == 30
    from fastapi.testclient import TestClient
    from studycenter_public.product import create_app
    from studycenter_public.platform.secrets import SecretStore
    with closing(sqlite3.connect(database)) as con:
        item_id, status = con.execute('SELECT id,status FROM items WHERE identity=?', ('note:manual:'+'a'*32,)).fetchone()
    assert status == 'done'
    with TestClient(create_app(paths, SecretStore(paths.instance_id, session_only=True))) as client:
        response = client.get(f'/items/{item_id}')
        assert response.status_code == 200 and 'Synthetic installation check' in response.text
    snapshot = lambda: tuple((data/name).read_bytes() if (data/name).exists() else None for name in PERSISTED_FILES)
    before = snapshot()
    gc.collect()
    require_persisted_unchanged(before, snapshot())
    spec = importlib.util.spec_from_file_location('synthetic_pre_update_backup',
                                                  Path(__file__).resolve().parents[1]/'packaging/backup_core.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.backup(data)
    require_persisted_unchanged(before, snapshot())
    verify_upgrade_backup(data, tmp_path, expected)
    with closing(sqlite3.connect(database)) as con, con:
        con.execute("UPDATE items SET pinned=0 WHERE identity=?", ('note:manual:'+'a'*32,))
    with pytest.raises(AssertionError, match='core/campusboard.sqlite3'):
        require_persisted_unchanged(before, snapshot())
