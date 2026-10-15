"""HTTP regressions for background jobs; no models or browsers are launched."""
import asyncio
from contextlib import contextmanager
import threading
import time

from fastapi.testclient import TestClient
import pytest

from studycenter_public.platform.paths import AppPaths
from studycenter_public.platform.secrets import SecretStore
from studycenter_public.web import create_app


@contextmanager
def own_client(tmp_path):
    paths = AppPaths.create(tmp_path / 'synthetic-public-data')
    app = create_app(paths, SecretStore(paths.instance_id, session_only=True))
    with TestClient(app, base_url=app.state.endpoint, raise_server_exceptions=False) as client:
        token = client.get('/api/session').json()['csrf']
        yield client, {'Origin': app.state.endpoint, 'X-CSRF-Token': token}


def configure_provider(client, headers, consent=True):
    response = client.post('/api/providers', headers=headers, json={
        'provider': 'openai', 'model': 'synthetic-model',
        'content_consent': consent,
    })
    assert response.status_code == 200


def job_target(client, headers, operation):
    if operation == 'extract':
        document = client.post('/api/documents/import', headers=headers,
                               files={'file': ('synthetic.txt', b'Synthetic document', 'text/plain')})
        assert document.status_code == 200
        identifier = document.json()['id']
        return f'/api/documents/{identifier}/extract', f'extract/{identifier}', identifier, client.app.state.extraction, 'extract'
    source = client.post('/api/sources', headers=headers,
                         json={'name': 'Synthetic source', 'kind': 'teachcenter'})
    assert source.status_code == 200
    identifier = source.json()['id']
    return f'/api/sources/{identifier}/discover', f'discover/{identifier}', identifier, client.app.state.sync, 'discover_courses'


def wait_for_job(client, name, state):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get('/api/jobs')
        assert response.status_code == 200
        job = response.json().get(name)
        if job and job['state'] == state:
            return job
        time.sleep(.005)
    pytest.fail(f'Job did not become {state}: {response.json()}')


@pytest.mark.parametrize('operation', ['extract', 'discover'])
def test_job_routes_start_async_work_and_expose_completion(tmp_path, monkeypatch, operation):
    release = threading.Event()
    with own_client(tmp_path) as (client, headers):
        configure_provider(client, headers)
        route, name, identifier, service, method = job_target(client, headers, operation)
        async def worker(actual_identifier):
            assert actual_identifier == identifier
            while not release.is_set():
                await asyncio.sleep(.001)
            return {'sentinel': 'synthetic-completion'}
        monkeypatch.setattr(service, method, worker)
        try:
            response = client.post(route, headers=headers)
            assert response.status_code == 200, response.text
            assert response.json() == {'job': name, 'state': 'working'}
            assert wait_for_job(client, name, 'working') == {'state': 'working'}
            release.set()
            assert wait_for_job(client, name, 'complete') == {
                'state': 'complete', 'result': {'sentinel': 'synthetic-completion'},
            }
        finally:
            release.set()


@pytest.mark.parametrize('operation', ['extract', 'discover'])
def test_job_routes_expose_async_worker_failure(tmp_path, monkeypatch, operation):
    with own_client(tmp_path) as (client, headers):
        configure_provider(client, headers)
        route, name, identifier, service, method = job_target(client, headers, operation)
        async def worker(actual_identifier):
            assert actual_identifier == identifier
            await asyncio.sleep(0)
            raise ValueError('Synthetic worker failure')
        monkeypatch.setattr(service, method, worker)
        response = client.post(route, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json() == {'job': name, 'state': 'working'}
        assert wait_for_job(client, name, 'failed') == {
            'state': 'failed', 'error': 'Synthetic worker failure',
        }


def test_extraction_without_content_consent_creates_no_job(tmp_path, monkeypatch):
    with own_client(tmp_path) as (client, headers):
        configure_provider(client, headers, consent=False)
        route, _, _, service, method = job_target(client, headers, 'extract')
        called = []
        async def worker(identifier):
            called.append(identifier)
            return {'sentinel': 'must-not-run'}
        monkeypatch.setattr(service, method, worker)
        response = client.post(route, headers=headers)
        assert response.status_code == 400
        assert 'Übertragung' in response.json()['error']
        assert client.get('/api/jobs').json() == {}
        assert called == []
