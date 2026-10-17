"""Synthetic HTTP checks for the settings layout and retained Public controls."""
from uuid import uuid4

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
import pytest

from studycenter_public.platform.paths import AppPaths
from studycenter_public.platform.secrets import SecretStore
from studycenter_public.web import create_app


@pytest.fixture
def public_client(tmp_path):
    paths = AppPaths.create(tmp_path / 'synthetic-ui-data')
    app = create_app(paths, SecretStore(paths.instance_id, session_only=True))
    app.state.endpoint = 'http://127.0.0.1:8767'
    with TestClient(app, base_url=app.state.endpoint) as client:
        token = client.get('/api/session').json()['csrf']
        client.headers.update({'Origin': app.state.endpoint, 'X-CSRF-Token': token})
        yield client


def test_settings_sections_have_unique_linked_panels_and_keep_public_controls(public_client):
    page = public_client.get('/settings')
    assert page.status_code == 200
    soup = BeautifulSoup(page.text, 'html.parser')
    tabs = soup.select('[data-settings-nav] a')
    assert [a.get_text(strip=True) for a in tabs] == [
        'Benachrichtigungen', 'Sprache', 'Darstellung',
        'Verbindungen', 'Sicherungen', 'Betrieb',
    ]
    assert len(soup.select('[data-settings-pane]')) == 6
    for tab in tabs:
        pane = soup.find(id=tab['aria-controls'])
        assert pane is not None
        assert pane['aria-labelledby'] == tab['id']
        assert pane.has_attr('data-settings-pane')
    ids = [el['id'] for el in soup.select('[id]')]
    assert len(ids) == len(set(ids))
    for control_id, pane in {
        'reminder-settings': 'notifications', 'bell': 'notifications',
        'speech-settings': 'speech', 'speech-model-status': 'speech',
        'theme': 'display', 'direct-mode': 'display',
        'mcp-enable': 'connections', 'mcp-write': 'connections',
        'mcp-token': 'connections', 'mcp-result': 'connections',
        'backup': 'backups', 'restore-form': 'backups', 'restore-preview': 'backups',
        'background': 'background', 'component-list': 'background',
        'component-status': 'background', 'remove-data': 'background',
    }.items():
        element = soup.find(id=control_id)
        assert element is not None, control_id
        assert element.find_parent(attrs={'data-settings-pane': pane}) is not None
    assert not soup.select('[data-settings-pane="google"], [data-tunnel-action]')
    assert soup.select_one('a[href="/settings#bell"]') is not None


def test_linux_marks_windows_tray_unavailable_and_preserves_shutdown(public_client, monkeypatch):
    from studycenter_public.web import pages
    with monkeypatch.context() as platform:
        platform.setattr(pages.sys, 'platform', 'linux')
        page = public_client.get('/settings')
    soup = BeautifulSoup(page.text, 'html.parser')
    assert soup.select_one('#background').has_attr('disabled')
    assert soup.select_one('#background')['aria-describedby'] == 'background-availability'
    assert 'Nur unter Windows verfügbar' in soup.select_one('#background-availability').get_text()
    assert soup.select_one('.app-shutdown') is not None


def test_settings_reflect_saved_theme_and_speech_model(public_client):
    result = public_client.post('/api/settings', json={'theme': 'dark', 'speech_model': 'small'})
    assert result.status_code == 200
    soup = BeautifulSoup(public_client.get('/settings').text, 'html.parser')
    assert soup.select_one('#theme option[selected]')['value'] == 'dark'
    assert soup.select_one('#speech-settings [name=model] option[selected]')['value'] == 'small'


def test_end_dates_and_personal_reminder_round_trip_without_implicit_write(public_client):
    create = BeautifulSoup(public_client.get('/board').text, 'html.parser').select_one('#create-form')
    for name in ['date', 'time', 'end_date', 'end_time', 'date_meaning']:
        assert create.select_one(f'[name={name}]') is not None
    action = {'type': 'create_task', 'title': 'Synthetic time window',
              'date': '2026-10-17', 'time': '10:00', 'end_date': '2026-10-19',
              'end_time': '12:30', 'date_meaning': 'window'}
    preview = public_client.post('/api/actions/preview', json={'actions': [action]})
    assert preview.status_code == 200
    assert public_client.get('/api/items').json() == []
    applied = public_client.post('/api/actions', json={'actions': [action], 'request_id': str(uuid4())})
    assert applied.status_code == 200
    item_id = applied.json()['item_ids'][0]
    reminder = {'type': 'set_reminder', 'item_id': item_id, 'enabled': False, 'offsets': [60, 10]}
    assert public_client.post('/api/actions', json={
        'actions': [reminder], 'request_id': str(uuid4()),
    }).status_code == 200
    page = BeautifulSoup(public_client.get(f'/items/{item_id}').text, 'html.parser')
    edit = page.select_one('#edit-form')
    assert edit.select_one('[name=end_date]')['value'] == '2026-10-19'
    assert edit.select_one('[name=end_time]')['value'] == '12:30'
    assert edit.select_one('[name=date_meaning] option[selected]')['value'] == 'window'
    assert page.select_one('#personal-reminder [name=minutes]')['value'] == '60,10'
    assert not page.select_one('#personal-reminder [name=enabled]').has_attr('checked')


@pytest.mark.parametrize('path', ['/overview', '/board', '/pins', '/archive', '/sources', '/documents', '/model'])
def test_main_pages_render_empty_without_adding_data(public_client, path):
    assert public_client.get(path).status_code == 200
    assert public_client.get('/api/items').json() == []
    assert public_client.get('/api/sources').json() == []
