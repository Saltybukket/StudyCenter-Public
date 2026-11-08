import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from studycenter_public.core.app import create_app, action_state
from studycenter_public.core.assistant import Assistant, Command
from studycenter_public.core.chatgpt import ChatGPTError
from .reference_settings import Settings
from studycenter_public.core.db import Database
from studycenter_public.core.llm import ModelAdapter
from studycenter_public.core.providers import validate_endpoint
from studycenter_public.core.search import SearchIndex


def install_transport(monkeypatch, handler):
    client = httpx.AsyncClient
    monkeypatch.setattr('studycenter_public.core.providers.httpx.AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))


@pytest.mark.parametrize('base,key,json_mode', [
    ('http://localhost:11434/v1', '', False),
    ('http://localhost:1234/v1', '', True),
    ('https://openrouter.ai/api/v1', 'test-secret', False),
    ('https://gateway.example/api/v1', 'another-secret', True),
])
async def test_all_dispatch_and_repairs_stay_on_selected_provider(tmp_path, monkeypatch, base, key, json_mode):
    settings = Settings(data_dir=tmp_path, llm_base_url=base, llm_model='local/9b', llm_api_key=key, llm_json_mode=json_mode)
    auth = SimpleNamespace(list_models=AsyncMock(side_effect=AssertionError('OAuth forbidden')), models=[])
    adapter = ModelAdapter(Database(tmp_path/'test.db'), settings, auth)
    seen = []
    def server(request):
        seen.append(request)
        body = json.loads(request.content)
        assert str(request.url) == base + '/chat/completions'
        assert body['model'] == 'local/9b'
        assert ('response_format' in body) == json_mode
        assert request.headers.get('authorization', '') == ('Bearer '+key if key else '')
        return httpx.Response(200, json={'choices': [{'message': {'content': '```json\n{"ok": true}\n```'}, 'finish_reason': 'stop'}]})
    install_transport(monkeypatch, server)
    for method in (adapter.responses, adapter.chat_completions, adapter.repair_response):
        assert json.loads(await method('Return JSON', 'test')) == {'ok': True}
    assert len(seen) == 3
    auth.list_models.assert_not_awaited()


@pytest.mark.parametrize('status,payload', [
    (401, {'error': 'secret must not appear'}), (302, {}), (429, {}),
    (200, {'error': {'message': 'secret must not appear'}}),
    (200, {'choices': [{'message': {'content': None}}]}),
    (200, {'choices': [{'message': {'content': '{"ok":true}'}, 'finish_reason': 'length'}]}),
])
async def test_errors_are_visible_without_retry_or_secret_echo(tmp_path, monkeypatch, status, payload):
    settings = Settings(data_dir=tmp_path, llm_base_url='http://localhost:11434/v1', llm_model='9b')
    adapter = ModelAdapter(Database(tmp_path/'test.db'), settings)
    seen = []
    def server(request):
        seen.append(request)
        return httpx.Response(status, json=payload, headers={'location': 'https://other.example/v1'})
    install_transport(monkeypatch, server)
    with pytest.raises(ChatGPTError) as error:
        await adapter.responses('Return JSON', 'test')
    assert 'secret must not appear' not in str(error.value)
    assert len(seen) == 1


@pytest.mark.parametrize('url', ['file:///tmp/model', 'https://key:secret@example.com/v1', 'https://example.com/v1?key=secret', 'http://example.com/v1'])
def test_invalid_endpoint(url):
    with pytest.raises(ValueError):
        validate_endpoint(url)


def test_settings_roundtrip_ui_and_endpoint_key_isolation(tmp_path):
    from studycenter_public.platform.paths import AppPaths
    from studycenter_public.platform.secrets import SecretStore
    from studycenter_public.product import create_app as public_app, make_settings
    paths = AppPaths.create(tmp_path/'public')
    secrets = SecretStore(paths.instance_id, session_only=True)
    app = public_app(paths, secrets)
    settings = app.state.settings
    client = TestClient(app)
    csrf = BeautifulSoup(client.get('/model').text, 'html.parser').select_one('meta[name=csrf-token]')['content']
    form = {'csrf': csrf, 'provider': 'chat-completions', 'base_url': 'https://openrouter.ai/api/v1',
            'api_model': 'test/9b', 'api_key': 'private-test-key'}
    assert client.post('/model/configure', data=form, follow_redirects=False).status_code == 400
    form['cloud_consent'] = 'yes'
    assert client.post('/model/configure', data=form, follow_redirects=False).status_code == 303
    assert 'private-test-key' not in client.get('/model').text
    assert 'private-test-key' not in (settings.data_dir/'settings.json').read_text()
    restored, _ = make_settings(paths, secrets)
    assert restored.llm_api_key == 'private-test-key' and restored.llm_model == 'test/9b'
    settings.api_store.write({'api_key': 'wrong-endpoint-key', 'base_url': 'https://other.example/v1'})
    restored, _ = make_settings(paths, secrets)
    assert restored.llm_api_key == ''
    settings.api_store.write({'api_key': 'private-test-key', 'base_url': form['base_url']})
    form.update(base_url='http://localhost:11434/v1', api_key='')
    assert client.post('/model/configure', data=form, follow_redirects=False).status_code == 303
    restored, _ = make_settings(paths, secrets)
    assert settings.llm_api_key == restored.llm_api_key == ''
    form.update(base_url='https://openrouter.ai/api/v1')
    assert client.post('/model/configure', data=form, follow_redirects=False).status_code == 303
    assert settings.llm_api_key == ''
    form.update(api_key='new-test-key', clear_api_key='on')
    assert client.post('/model/configure', data=form, follow_redirects=False).status_code == 303
    restored, _ = make_settings(paths, secrets)
    assert restored.llm_api_key == ''


async def test_local_studi_creates_task_without_oauth(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, llm_base_url='http://localhost:11434/v1', llm_model='9b')
    db = Database(tmp_path/'test.db')
    adapter = ModelAdapter(db, settings)
    service = Assistant(db, settings, adapter, SearchIndex(db), action_state)
    def server(request):
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({'action': 'create_task', 'title': 'Mit Julia Anime schauen'})}}]})
    install_transport(monkeypatch, server)
    request = Command(request_id=uuid4(), text='Erstelle eine Aufgabe: Mit Julia Anime schauen.', preview=True)
    result = await service.command(request)
    assert result['preview']['plan']['title'] == 'Mit Julia Anime schauen'
    result = await service.command(Command(request_id=uuid4(), text='Übernehmen', previous_id=request.request_id, preview=True, apply_preview=True))
    assert result.get('item_id')
    assert len(db.rows('SELECT * FROM items')) == 1


def test_api_probe_uses_only_test_payload(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, llm_base_url='http://localhost:11434/v1', llm_model='9b')
    app = create_app(settings)
    seen = []
    def server(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok": true}'}}]})
    install_transport(monkeypatch, server)
    client = TestClient(app)
    csrf = BeautifulSoup(client.get('/model').text, 'html.parser').select_one('meta[name=csrf-token]')['content']
    response = client.post('/model/api-test', data={'csrf': csrf})
    assert response.status_code == 200 and response.json()['ok']
    assert len(seen) == 1
    assert seen[0]['messages'][1]['content'] == 'Verbindungstest ohne Kursdaten.'


def test_switching_to_api_preserves_oauth_choice_and_probe(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path/'secrets'))
    settings = Settings(data_dir=tmp_path/'data', model_provider='chatgpt', oauth_model='account-model', oauth_probe_passed=True)
    app = create_app(settings)
    client = TestClient(app)
    csrf = BeautifulSoup(client.get('/model').text, 'html.parser').select_one('meta[name=csrf-token]')['content']
    response = client.post('/model/configure', data={'csrf':csrf, 'provider':'chat-completions',
        'base_url':'http://localhost:11434/v1','api_model':'local-9b'}, follow_redirects=False)
    assert response.status_code == 303
    assert settings.oauth_model == 'account-model' and settings.oauth_probe_passed
    from studycenter_public.core.config import Settings as StoredSettings
    restored = StoredSettings(data_dir=settings.data_dir)
    assert restored.oauth_model == 'account-model' and restored.oauth_probe_passed


def test_return_to_saved_oauth_model_after_restart_without_background_catalog_request(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, model_provider='chat-completions', oauth_model='account-model', oauth_probe_passed=True)
    app = create_app(settings)
    auth = app.state.sync.auth
    monkeypatch.setattr(auth, 'status', lambda:{'connected':True,'plan_enabled':True,'models':[], 'profiles':[], 'message':''})
    catalog = AsyncMock(return_value=[{'slug':'account-model','display_name':'Saved model'}])
    monkeypatch.setattr(auth, 'list_models', catalog)
    client = TestClient(app)
    soup = BeautifulSoup(client.get('/model').text, 'html.parser')
    catalog.assert_not_called()
    selected = soup.select_one('select[name=model] option[selected]')
    assert selected['value'] == 'account-model'
    csrf = soup.select_one('meta[name=csrf-token]')['content']
    result = client.post('/model/configure', data={'csrf':csrf,'provider':'chatgpt','model':selected['value']}, follow_redirects=False)
    assert result.status_code == 303 and settings.model_provider == 'chatgpt'
    assert settings.oauth_model == 'account-model' and settings.oauth_probe_passed
    catalog.assert_awaited_once()
