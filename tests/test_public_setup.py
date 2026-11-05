from fastapi.testclient import TestClient

from studycenter_public.platform.paths import AppPaths
from studycenter_public.platform.secrets import SecretStore
from studycenter_public.product import create_app


def test_first_setup_is_local_and_never_displays_saved_secret(tmp_path):
    paths = AppPaths.create(tmp_path / 'public')
    store = SecretStore(paths.instance_id, session_only=True)
    app = create_app(paths, store)
    with TestClient(app) as client:
        calls = []
        app.state.components.install = lambda *args, **kwargs: calls.append((args, kwargs))
        page = client.get('/setup')
        assert page.status_code == 200
        assert 'Eigene Kurse verbinden' in page.text and 'TU-Browser' in page.text
        assert '/introduction' in page.text and 'data-model-api' in page.text
        assert calls == []
        denied = client.post('/settings/model-api', data={'base_url':'https://example.invalid/v1', 'model':'synthetic', 'api_key':'test-key'})
        assert denied.status_code == 403
        result = client.post('/settings/model-api', data={'csrf':app.state.csrf, 'base_url':'https://example.invalid/v1', 'model':'synthetic', 'api_key':'synthetic-never-render', 'cloud_consent':'yes'})
        assert result.status_code == 200
        for path in ('/setup', '/settings', '/model'):
            response = client.get(path)
            assert response.status_code == 200
            assert 'synthetic-never-render' not in response.text
        assert calls == []


def test_session_storage_form_has_explicit_consent_and_csrf(tmp_path):
    paths = AppPaths.create(tmp_path / 'public')
    app = create_app(paths, SecretStore(paths.instance_id, backend=False))
    with TestClient(app) as client:
        page = client.get('/settings')
        assert 'data-session-secrets' in page.text
        assert 'name="confirm" value="yes" required' in page.text
        assert app.state.csrf in page.text
        assert client.post('/settings/secrets/session', data={'csrf':app.state.csrf}).status_code == 400
        assert not app.state.secrets.session_only
