from pathlib import Path

from fastapi.testclient import TestClient

from studycenter_public.platform.paths import AppPaths
from studycenter_public.platform.secrets import SecretStore
from studycenter_public.product import create_app
from studycenter_public.core.ical import export_calendar


def test_product_isolated_runtime_and_credentials(tmp_path, monkeypatch):
    private = tmp_path / 'private'
    monkeypatch.setenv('CAMPUSBOARD_DATA_DIR', str(private))
    monkeypatch.setenv('CAMPUSBOARD_BROWSER_DIR', str(private / 'browser'))
    monkeypatch.setenv('CAMPUSBOARD_SPEECH_DIR', str(private / 'speech'))
    monkeypatch.setenv('LLM_API_KEY', 'must-not-be-inherited')
    first = AppPaths.create(tmp_path / 'first')
    second = AppPaths.create(tmp_path / 'second')
    first_store = SecretStore(first.instance_id, session_only=True)
    second_store = SecretStore(second.instance_id, session_only=True)
    app = create_app(first, first_store)
    other = create_app(second, second_store)
    app.state.settings.auth_store.write({'host_id':'one', 'active':'', 'profiles':[]})
    assert other.state.settings.auth_store.read()['host_id'] == second.host_id
    app.state.settings.mail_store.write({'password':'not-on-disk'})
    assert other.state.settings.mail_store.read() == {}
    assert app.state.browser.profile_dir.is_relative_to(first.profiles)
    assert app.state.speech.root.is_relative_to(first.cache)
    assert app.state.settings.llm_api_key == ''
    assert not private.exists()
    assert b'not-on-disk' not in (first.data / 'core' / 'campusboard.sqlite3').read_bytes()
    with TestClient(app) as client:
        app.state.endpoint = 'http://127.0.0.1:43123'
        for path in ('/overview', '/board', '/settings', '/api/voice/status'):
            assert client.get(path).status_code == 200
        assert client.get('/api/health').json()['instance_id'] == first.instance_id
        assert app.state.tunnel.public()['tunnel_id'] == ''
        assert app.state.tunnel.settings()['auto_start'] is False


def test_secret_store_requires_explicit_secure_or_session_backend(tmp_path):
    paths = AppPaths.create(tmp_path / 'data')
    store = SecretStore(paths.instance_id, backend=False)
    app = create_app(paths, store)
    from studycenter_public.platform.secrets import SecretStoreUnavailable
    import pytest
    with pytest.raises(SecretStoreUnavailable):
        app.state.settings.google_store.write({'refresh_token':'secret'})
    assert not any(b'secret' in p.read_bytes() for p in paths.data.rglob('*') if p.is_file() and p.stat().st_size < 1_000_000)


def test_session_opt_in_and_optional_component_setup(tmp_path):
    paths = AppPaths.create(tmp_path / 'data')
    app = create_app(paths, SecretStore(paths.instance_id, backend=False))
    with TestClient(app) as client:
        csrf = app.state.csrf
        assert client.post('/settings/secrets/session', data={'confirm':'yes'}).status_code == 403
        response = client.post('/settings/secrets/session', data={'csrf':csrf, 'confirm':'yes'})
        assert response.status_code == 200
        assert app.state.secrets.session_only
        app.state.settings.google_store.write({'refresh_token':'session-only'})
        assert app.state.settings.google_store.read()['refresh_token'] == 'session-only'
        assert client.get('/api/components').status_code == 200
        denied = client.post('/api/components/install', headers={'X-CSRF-Token':csrf}, json={'component':'browser'})
        assert denied.status_code == 400


def test_missing_browser_module_does_not_break_start(tmp_path, monkeypatch):
    import builtins
    original = builtins.__import__
    def no_playwright(name, *args, **kwargs):
        if name.startswith('playwright'): raise ImportError('not installed')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', no_playwright)
    paths = AppPaths.create(tmp_path / 'data')
    app = create_app(paths, SecretStore(paths.instance_id, session_only=True))
    with TestClient(app) as client:
        assert client.get('/overview').status_code == 200
    import asyncio
    from studycenter_public.core.browser import BrowserUnavailable
    import pytest
    with pytest.raises(BrowserUnavailable, match='Komponente fehlt'):
        asyncio.run(app.state.browser._ensure())


def test_session_only_tunnel_fails_before_network(tmp_path):
    import asyncio
    import pytest
    paths = AppPaths.create(tmp_path / 'data')
    app = create_app(paths, SecretStore(paths.instance_id, session_only=True))
    async def fail_network(*args, **kwargs):
        raise AssertionError('No network should be reached')
    app.state.tunnel.validate_key = fail_network
    with pytest.raises(ValueError, match='OS-Schlüsselspeicher'):
        asyncio.run(app.state.tunnel.start())


def test_source_link_uses_selected_runtime_port():
    from studycenter_public.core.mcp import public_source_page
    page = {'page': 3, 'text':'text', 'reading_history':[{'reading_id':'a'*64}]}
    result = public_source_page(page, 'text', 7, 'http://127.0.0.1:43123')
    assert result['historical_reader_url'].startswith('http://127.0.0.1:43123/documents/7?')


def test_locked_keyring_keeps_settings_accessible_and_session_opt_in(tmp_path):
    class BrokenBackend:
        def get_password(self, *args): raise RuntimeError('locked')
    paths = AppPaths.create(tmp_path / 'data')
    store = SecretStore(paths.instance_id, backend=BrokenBackend())
    app = create_app(paths, store)
    with TestClient(app) as client:
        assert client.get('/settings').status_code == 200
        assert 'Schlüsselspeicher' in app.state.secrets.status
        assert client.post('/settings/secrets/session', data={'csrf':app.state.csrf,'confirm':'yes'}).status_code == 200
        assert app.state.secrets.session_only
        app.state.settings.google_store.write({'refresh_token':'temporary'})
        assert app.state.settings.google_store.read()['refresh_token'] == 'temporary'


def test_model_api_consent_persistence_and_secret_isolation(tmp_path):
    paths = AppPaths.create(tmp_path / 'data')
    store = SecretStore(paths.instance_id, session_only=True)
    app = create_app(paths, store)
    with TestClient(app) as client:
        csrf = app.state.csrf
        fields = {'csrf':csrf, 'base_url':'https://models.example/v1', 'model':'test-model', 'api_key':'private-key'}
        assert client.post('/settings/model-api', data=fields).status_code == 400
        assert not (paths.config / 'model-api.json').exists()
        fields['cloud_consent'] = 'yes'
        assert client.post('/settings/model-api', data=fields).status_code == 200
        assert app.state.settings.llm_api_key == 'private-key'
        assert 'private-key' not in (paths.config / 'model-api.json').read_text()
        fields.update(base_url='http://127.0.0.1:11434/v1',api_key='',cloud_consent='')
        assert client.post('/settings/model-api', data=fields).status_code == 200
        assert app.state.settings.llm_api_key == ''
        assert app.state.settings.api_store.read() == {}
        fields.update(base_url='http://example.com/v1',api_key='another-key')
        assert client.post('/settings/model-api', data=fields).status_code == 400
        assert app.state.settings.llm_base_url == 'http://127.0.0.1:11434/v1'
    from studycenter_public.product import make_settings
    settings, _ = make_settings(paths, store)
    assert settings.llm_base_url == 'http://127.0.0.1:11434/v1'
    assert settings.llm_model == 'test-model'
    assert settings.llm_api_key == ''
