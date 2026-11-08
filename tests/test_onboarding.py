from contextlib import contextmanager
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from studycenter_public.onboarding import install


class FakeDB:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()


def test_local_state_survives_restart_and_replay_is_explicit(tmp_path):
    def client():
        app = FastAPI()
        app.state.db = FakeDB(tmp_path / 'synthetic.sqlite')
        install(app)
        return TestClient(app)
    with client() as c:
        assert c.get('/api/introduction').json() == {'version': 1, 'status': 'new', 'step': 0}
        assert c.post('/api/introduction', json={'status':'active','step':2}).json()['step'] == 2
        assert c.post('/api/introduction', json={'status':'paused','step':2}).json()['status'] == 'paused'
        assert c.post('/api/introduction', json={'status':'active','step':2}).status_code == 200
        assert c.post('/api/introduction', json={'status':'invalid','step':2}).status_code == 422
        assert c.post('/api/introduction', json={'status':'active','step':5}).status_code == 422
    with client() as c:
        assert c.get('/api/introduction').json()['status'] == 'active'
        assert c.get('/api/introduction').json()['step'] == 2
        assert c.post('/api/introduction', json={'status':'dismissed','step':2}).status_code == 200
        assert c.get('/api/introduction').json()['status'] == 'dismissed'
        replay = c.get('/introduction', follow_redirects=False)
        assert replay.status_code == 303
        assert replay.headers['location'] == '/overview?introduction=start'
        assert c.get('/api/introduction').json()['status'] == 'dismissed'


def test_public_core_rejects_cross_origin_and_requires_csrf(tmp_path):
    from studycenter_public.platform.paths import AppPaths
    from studycenter_public.platform.secrets import SecretStore
    from studycenter_public.product import create_app

    paths = AppPaths.create(tmp_path / 'isolated-public')
    app = create_app(paths, SecretStore(paths.instance_id, session_only=True))
    with TestClient(app) as client:
        payload = {'status': 'active', 'step': 1}
        assert client.post('/api/introduction', json=payload).status_code == 403
        response = client.post('/api/introduction', json=payload, headers={
            'Origin': 'https://foreign.example', 'X-CSRF-Token': app.state.csrf,
        })
        assert response.status_code == 403
        assert client.get('/api/introduction').json()['status'] == 'new'
        response = client.post('/api/introduction', json=payload, headers={
            'Origin': 'http://testserver', 'X-CSRF-Token': app.state.csrf,
        })
        assert response.status_code == 200
        assert client.get('/api/introduction').json()['step'] == 1


def test_failed_dismissal_stays_hidden_for_session_and_returns_in_new_session(tmp_path):
    from pathlib import Path
    from urllib.parse import urlsplit
    import pytest
    playwright = pytest.importorskip('playwright.sync_api')
    from studycenter_public.platform.paths import AppPaths
    from studycenter_public.platform.secrets import SecretStore
    from studycenter_public.product import create_app

    paths = AppPaths.create(tmp_path / 'public')
    app = create_app(paths, SecretStore(paths.instance_id, session_only=True))
    client = TestClient(app)
    with playwright.sync_playwright() as driver:
        if not Path(driver.chromium.executable_path).exists():
            pytest.skip('Public test Chromium is not installed')
        browser = driver.chromium.launch()
        def route_request(route):
            url = urlsplit(route.request.url)
            assert url.hostname == 'public.test'
            if url.path == '/api/introduction' and route.request.method == 'POST':
                return route.fulfill(status=500, body='synthetic failure')
            response = client.get(url.path + ('?' + url.query if url.query else ''))
            route.fulfill(status=response.status_code, body=response.content,
                          content_type=response.headers.get('content-type', 'application/octet-stream'))
        context = browser.new_context()
        page = context.new_page()
        page.route('**/*', route_request)
        page.goto('http://public.test/overview')
        playwright.expect(page.locator('#introduction')).to_be_visible()
        page.locator('.intro-skip').click()
        playwright.expect(page.locator('#introduction')).to_be_hidden()
        page.goto('http://public.test/board')
        playwright.expect(page.locator('#introduction')).to_be_hidden()
        context.close()
        context = browser.new_context()
        page = context.new_page()
        page.route('**/*', route_request)
        page.goto('http://public.test/overview')
        playwright.expect(page.locator('#introduction')).to_be_visible()
        browser.close()
