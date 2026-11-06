import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import threading
import zipfile

from fastapi.testclient import TestClient
from fastapi import HTTPException
import pytest

from studycenter_public.platform.paths import AppPaths
from studycenter_public.platform.secrets import SecretStore
from studycenter_public.product import create_app
from test_ice_migration import backup


def app_for(tmp_path):
    paths = AppPaths.create(tmp_path / 'current')
    app = create_app(paths, SecretStore(paths.instance_id, session_only=True))
    return paths, app


def headers(app, folder='imported', confirm='yes'):
    return {'X-CSRF-Token': app.state.csrf, 'X-ICE-Folder': folder,
            'X-ICE-Confirm': confirm, 'Content-Type': 'application/zip'}


def test_setup_import_creates_separate_instance_and_reports_how_to_open(tmp_path):
    source = backup(tmp_path)
    raw = source.read_bytes()
    paths, app = app_for(tmp_path)
    marker = paths.data / 'untouched.txt'
    marker.write_text('current', encoding='utf-8')
    identity = (paths.data / 'instance.json').read_bytes()
    with TestClient(app) as client:
        assert 'ICE-Backup' in client.get('/setup').text
        response = client.post('/api/ice-import', headers=headers(app), content=raw)
        assert response.status_code == 200, response.text
        result = response.json()
        assert Path(result['destination']) == tmp_path / 'imported'
        assert '--data-dir' in result['command'] and str(tmp_path / 'imported') in result['command']
        assert result['counts']['items'] == 1
        assert client.post('/api/ice-import', headers=headers(app), content=raw).status_code == 400
    assert (paths.data / 'instance.json').read_bytes() == identity
    assert marker.read_text(encoding='utf-8') == 'current'
    assert source.read_bytes() == raw
    assert (tmp_path / 'imported' / 'core' / 'ice-import.json').exists()
    assert not list(paths.cache.glob('.ice-upload-*'))


def test_import_guard_rejects_csrf_path_malformed_and_oversize(tmp_path, monkeypatch):
    paths, app = app_for(tmp_path)
    with TestClient(app) as client:
        assert client.post('/api/ice-import', content=b'bad').status_code == 403
        assert client.post('/api/ice-import', headers=headers(app, '../current'), content=b'bad').status_code == 400
        assert client.post('/api/ice-import', headers=headers(app, confirm='no'), content=b'bad').status_code == 400
        assert client.post('/api/ice-import', headers=headers(app), content=b'bad').status_code == 400
        monkeypatch.setattr('studycenter_public.ice_import_ui.MAX_SIZE', 4)
        assert client.post('/api/ice-import', headers=headers(app), content=b'12345').status_code == 413
    assert not (tmp_path / 'imported').exists()
    assert not list(paths.cache.glob('.ice-upload-*'))


def test_same_name_concurrent_import_has_exactly_one_winner(tmp_path):
    raw = backup(tmp_path).read_bytes()
    paths, app = app_for(tmp_path)
    with TestClient(app) as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: client.post('/api/ice-import', headers=headers(app), content=raw), range(2)))
    assert sorted(response.status_code for response in responses) in ([200, 400], [200, 409])
    assert (tmp_path / 'imported' / 'core' / 'ice-import.json').is_file()
    assert not list(paths.cache.glob('.ice-upload-*'))
    assert not list(tmp_path.glob('.studycenter-ice-import-*'))


def test_import_route_has_own_archive_limit_in_local_guard(tmp_path, monkeypatch):
    import studycenter_public.core.app as core_app
    source = backup(tmp_path)
    with zipfile.ZipFile(source, 'a') as archive:
        archive.comment = b'x' * 65535
    raw = source.read_bytes()
    _, app = app_for(tmp_path)
    monkeypatch.setattr(core_app, 'MAX_BYTES', 4)
    with TestClient(app) as client:
        response = client.post('/api/ice-import', headers=headers(app), content=raw)
        assert response.status_code == 200, response.text
        other = client.post('/api/components/install', headers={'X-CSRF-Token': app.state.csrf}, content=b'x' * 65541)
        assert other.status_code == 413


@pytest.mark.asyncio
async def test_cancelled_import_keeps_upload_until_worker_finishes_and_blocks_another(tmp_path, monkeypatch):
    from studycenter_public import ice_import_ui
    paths = AppPaths.create(tmp_path / 'current')
    started, release = threading.Event(), threading.Event()
    seen = []

    def slow_import(source, destination):
        seen.append(source)
        started.set()
        assert release.wait(5)
        assert source.read_bytes() == b'fixture'
        destination.mkdir()
        (destination / 'published').write_text('complete', encoding='utf-8')
        return {'counts': {}}

    class Request:
        headers = {'x-ice-confirm': 'yes', 'x-ice-folder': 'imported'}
        async def stream(self):
            yield b'fixture'

    monkeypatch.setattr(ice_import_ui, 'import_ice', slow_import)
    endpoint = ice_import_ui.create_ice_import_router(paths).routes[0].endpoint
    task = asyncio.create_task(endpoint(Request()))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        with pytest.raises(HTTPException) as busy:
            await endpoint(Request())
        assert busy.value.status_code == 409
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and seen[0].read_bytes() == b'fixture'
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (tmp_path / 'imported' / 'published').read_text(encoding='utf-8') == 'complete'
    assert not list(paths.cache.glob('.ice-upload-*'))


def test_installed_module_cli_import_and_collision(tmp_path):
    source = backup(tmp_path)
    target = tmp_path / 'cli-imported'
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'))
    command = [sys.executable, '-m', 'studycenter_public', '--import-ice', '--backup', str(source), '--destination', str(target)]
    first = subprocess.run(command, env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    marker = (target / 'instance.json').read_bytes()
    second = subprocess.run(command, env=env, capture_output=True, text=True)
    assert second.returncode != 0
    assert (target / 'instance.json').read_bytes() == marker


def test_launch_command_uses_installed_starter_and_quotes_powershell(tmp_path, monkeypatch):
    from studycenter_public import ice_import_ui
    starter = tmp_path / 'start.cmd'
    starter.write_text('fixture', encoding='utf-8')
    monkeypatch.setenv('STUDYCENTER_PUBLIC_INSTALL_ROOT', str(tmp_path))
    monkeypatch.setattr(ice_import_ui.sys, 'platform', 'win32')
    command = ice_import_ui._launch_command(tmp_path / "O'Brien & data")
    assert command.startswith("& '")
    assert "start.cmd' '--data-dir'" in command
    assert "O''Brien & data'" in command
