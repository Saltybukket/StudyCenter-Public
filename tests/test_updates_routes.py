import asyncio
import json
import time
import zipfile

from fastapi.testclient import TestClient
import pytest

from studycenter_public.platform.paths import AppPaths
from studycenter_public.platform.secrets import SecretStore
from studycenter_public.product import create_app
from studycenter_public import updates


class Catalog:
    def __init__(self, paths):
        self.calls = []
        self.value = dict(current_version='0.1.0b6', platform='linux-x86_64', supported=True,
                          auto_check=False, state='idle', release=None, last_checked=None,
                          skipped_version=None, error=None)

    def status(self):
        return dict(self.value)

    async def check(self):
        self.calls.append('check')
        self.value.update(state='available', release=dict(version='0.1.0b7', tag='v0.1.0-beta.7', notes='Fix', url='https://github.com/Saltybukket/StudyCenter-Public/releases/tag/v0.1.0-beta.7'))

    async def download(self, version):
        self.calls.append('download')
        self.value['state'] = 'ready'

    def set_preferences(self, value):
        self.value['auto_check'] = value

    def skip(self, version):
        self.value['skipped_version'] = version

    def get_ready(self, version):
        self.calls.append('verify')
        return {'version': version, 'installer_compatible': True}


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, 'UpdateCatalog', Catalog)
    monkeypatch.setattr(updates.update_install, 'installation_support', lambda paths: (True, ''))
    monkeypatch.setattr(updates.update_install, 'outcome_status', lambda paths: None)
    paths = AppPaths.create(tmp_path / 'instance')
    app = create_app(paths, SecretStore(paths.instance_id, session_only=True))
    app.state.stop_callback = lambda: None
    app.state.product_update_activities = lambda: []
    return app, paths


def test_reading_status_never_checks_or_installs_and_consent_is_required(app_env, monkeypatch):
    app, paths = app_env
    handed = []
    monkeypatch.setattr(updates.update_install, 'launch_handoff', lambda prepared: handed.append(prepared))
    with TestClient(app) as client:
        headers = {'X-CSRF-Token': app.state.csrf}
        assert client.get('/api/updates').json()['auto_check'] is False
        assert app.state.updates.calls == []
        assert client.post('/api/updates/check').status_code == 403
        assert client.post('/api/updates/install', json={'version': '0.1.0b7', 'confirm': False}, headers=headers).status_code == 400
        assert client.post('/api/updates/install', json={'version': '0.1.0b7', 'confirm': 'true'}, headers=headers).status_code == 422
        assert client.post('/api/updates/check', json={}, headers=headers).status_code == 200
        for _ in range(100):
            if client.get('/api/updates').json()['state'] == 'available':
                break
            time.sleep(.01)
        assert app.state.updates.calls == ['check']
        assert handed == [] and app.state.lifecycle_request == ''
        assert client.post('/api/updates/preferences', json={'auto_check': True}, headers=headers).json()['auto_check']
        assert client.post('/api/updates/preferences', json={'auto_check': False}, headers=headers).json()['auto_check'] is False


def test_install_saves_real_backup_before_handoff_and_preserves_progress(app_env, monkeypatch):
    app, paths = app_env
    db = app.state.db
    db.execute("INSERT INTO items(identity,payload,status,overrides,created_at,changed_at) VALUES(?,?,?,?,?,?)",
               ('note:synthetic', json.dumps({'title':'Eigener Text','kind':'task','source_state':'active'}), 'done', '{"title":"Meine Änderung"}', '2030-01-01','2030-01-01'))
    steps = json.dumps([{'id':'step','title':'Meine Etappe','done':True,'detail':'Eigene Notiz','edited_fields':['detail']}])
    db.execute("INSERT INTO quests(id,request_id,input_hash,title,mode,horizon,daily_minutes,start_date,end_date,state,steps,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
               ('quest','request','hash','Lernplan','custom',7,30,'2030-01-01','2030-01-07','active',steps,'2030-01-01','2030-01-01'))
    before = [db.rows('SELECT * FROM '+table) for table in ('items','quests')]
    trace = []
    monkeypatch.setattr(updates.update_install, 'prepare_handoff', lambda *args: trace.append('prepare') or {})
    def launch(prepared):
        assert app.state.maintenance
        backups = list((paths.data/'core/backups').glob('*.zip'))
        assert len(backups) == 1
        with zipfile.ZipFile(backups[0]) as archive:
            counts = json.loads(archive.read('manifest.json'))['counts']
            assert counts['items'] == 1 and counts['quests'] == 1
        trace.append('launch')
    monkeypatch.setattr(updates.update_install, 'launch_handoff', launch)
    with TestClient(app) as client:
        headers = {'X-CSRF-Token': app.state.csrf}
        response = client.post('/api/updates/install', json={'version':'0.1.0b7','confirm':True}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()['backup'].endswith('.zip')
        assert trace == ['prepare','launch']
        assert app.state.lifecycle_request == 'stop'
        assert client.post('/api/updates/install', json={'version':'0.1.0b7','confirm':True}, headers=headers).status_code == 409
    assert before == [db.rows('SELECT * FROM '+table) for table in ('items','quests')]


@pytest.mark.parametrize('failure', ['busy','backup','handoff'])
def test_failure_never_stops_app_and_releases_maintenance(app_env, monkeypatch, failure):
    app, paths = app_env
    trace = []
    monkeypatch.setattr(updates.update_install, 'prepare_handoff', lambda *args: {})
    def launch(prepared):
        trace.append('launch')
        if failure == 'handoff':
            raise ValueError('Helfer nicht bereit')
    monkeypatch.setattr(updates.update_install, 'launch_handoff', launch)
    if failure == 'busy':
        app.state.product_update_activities = lambda: ['Quest planen']
    if failure == 'backup':
        def fail(*args):
            raise OSError('Sicherung nicht möglich')
        monkeypatch.setattr(updates, 'create_backup', fail)
    with TestClient(app) as client:
        headers = {'X-CSRF-Token': app.state.csrf}
        response = client.post('/api/updates/install', json={'version':'0.1.0b7','confirm':True}, headers=headers)
        assert response.status_code == (409 if failure == 'busy' else 400)
        assert app.state.lifecycle_request == '' and not app.state.maintenance
        assert trace == (['launch'] if failure == 'handoff' else [])
        assert client.post('/api/updates/preferences', json={'auto_check':False}, headers=headers).status_code == 200


def test_install_rejects_legacy_package_before_backup_or_handoff(app_env, monkeypatch):
    app, paths = app_env
    monkeypatch.setattr(app.state.updates, 'get_ready', lambda version: {'version': version})
    def forbidden(*args):
        pytest.fail('Legacy package must not enter installation')
    monkeypatch.setattr(updates.update_install, 'prepare_handoff', forbidden)
    monkeypatch.setattr(updates, 'create_backup', forbidden)
    with TestClient(app) as client:
        response = client.post('/api/updates/install', json={'version':'0.1.0b7','confirm':True}, headers={'X-CSRF-Token':app.state.csrf})
        assert response.status_code == 400
        assert app.state.lifecycle_request == '' and not app.state.maintenance
