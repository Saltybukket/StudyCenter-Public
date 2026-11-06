import asyncio
from types import SimpleNamespace

import pytest

from studycenter_public.core.browser import BrowserService, BrowserUnavailable
from studycenter_public.core.google_calendar import GoogleAuth
from studycenter_public.core.llm import ModelAdapter


class Store:
    def __init__(self):
        self.value = {}

    def read(self):
        return self.value

    def write(self, value):
        self.value = value


def test_injected_google_and_browser_stores_do_not_use_private_profile(tmp_path, monkeypatch):
    private = tmp_path / 'private'
    monkeypatch.setenv('CAMPUSBOARD_BROWSER_DIR', str(private))
    browser_store, google_store = Store(), Store()
    settings = SimpleNamespace(data_dir=tmp_path, browser_dir=tmp_path / 'browser',
                               browser_store=browser_store, configure_browser=lambda: None,
                               system_browser=lambda: None)
    browser = BrowserService(settings)
    auth = GoogleAuth(tmp_path, google_store)
    auth.save({'refresh_token': 'example'})
    assert auth.read() == {'refresh_token': 'example'}
    assert browser.profile_dir == (tmp_path / 'browser').resolve()
    assert not private.exists()


def test_missing_isolation_adapters_fail_before_private_directory(tmp_path, monkeypatch):
    private = tmp_path / 'private'
    monkeypatch.setenv('CAMPUSBOARD_BROWSER_DIR', str(private))
    with pytest.raises(RuntimeError, match='instanzgebundene'):
        BrowserService(SimpleNamespace(data_dir=tmp_path))
    with pytest.raises(RuntimeError, match='instanzgebundene'):
        GoogleAuth(tmp_path)
    with pytest.raises(ValueError, match='Browserkonfiguration'):
        BrowserService(SimpleNamespace(data_dir=tmp_path, browser_store=Store(), browser_dir=tmp_path / 'browser'))
    assert not private.exists()


def test_unavailable_session_store_does_not_launch_browser(tmp_path, monkeypatch):
    class Unavailable(Store):
        def read(self): raise RuntimeError('unavailable')

    class Chromium:
        async def launch_persistent_context(self, *args, **kwargs):
            raise AssertionError('browser must not launch without session store')

    class Driver:
        chromium = Chromium()
        async def stop(self): pass

    class Factory:
        async def start(self): return Driver()

    monkeypatch.setattr('studycenter_public.core.browser.async_playwright', lambda: Factory())
    settings = SimpleNamespace(data_dir=tmp_path, browser_dir=tmp_path / 'browser',
                               browser_store=Unavailable(), configure_browser=lambda: None,
                               system_browser=lambda: None)
    with pytest.raises(BrowserUnavailable):
        asyncio.run(BrowserService(settings)._ensure())


def test_model_http_defaults_to_no_environment_proxy(monkeypatch):
    seen = []

    class Response:
        def raise_for_status(self): pass
        def json(self): return {'choices': [{'message': {'content': 'ok'}}]}

    class Client:
        def __init__(self, **kwargs): seen.append(kwargs['trust_env'])
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs): return Response()

    monkeypatch.setattr('studycenter_public.core.llm.httpx.AsyncClient', Client)
    settings = SimpleNamespace(llm_api_key='', llm_model='test', llm_base_url='https://example.test')
    adapter = ModelAdapter(None, settings)
    assert asyncio.run(adapter.chat_completions('test', 'test')) == 'ok'
    settings.llm_trust_env = True
    assert asyncio.run(adapter.chat_completions('test', 'test')) == 'ok'
    assert seen == [False, True]
