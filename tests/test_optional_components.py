from types import SimpleNamespace
import pytest
from studycenter_public.platform.components import ComponentService


def test_browser_verification_does_not_require_ocr(tmp_path, monkeypatch):
    from studycenter_public.platform import components
    monkeypatch.setattr(components.sys, 'platform', 'win32')
    paths = SimpleNamespace(cache=tmp_path/'cache')
    (paths.cache/'components/browsers/chromium-test').mkdir(parents=True)
    result = ComponentService(paths).verify('browser')
    assert result['browser'] is True
    assert result['ocr'] is False
    assert result['components']['ocr']['state'] == 'missing'


def test_verify_without_component_reports_partial_availability(tmp_path):
    paths = SimpleNamespace(cache=tmp_path/'cache')
    result = ComponentService(paths).verify()
    assert result['components']['browser']['state'] == 'missing'
    assert result['components']['ocr']['state'] == 'missing'
    assert not paths.cache.exists()


def test_component_install_requires_explicit_consent(tmp_path):
    paths = SimpleNamespace(cache=tmp_path/'cache')
    with pytest.raises(ValueError, match='bestätigen'):
        ComponentService(paths).install('ocr', allow_download=False)
    assert not paths.cache.exists()


def test_ocr_settings_refresh_after_later_installation(tmp_path):
    from studycenter_public.platform.paths import AppPaths
    from studycenter_public.services.documents import DocumentService
    from studycenter_public.storage.db import Database
    import os
    paths = AppPaths.create(tmp_path/'data')
    service = DocumentService(Database(paths.database), paths)
    assert service.ocr.tesseract is None
    root = paths.cache/'components/ocr'
    executable = root/('tesseract.exe' if os.name == 'nt' else 'usr/bin/tesseract')
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'synthetic executable placeholder')
    assert service.ocr.tesseract == str(executable.resolve())
    assert service.ocr.temp_directory == str(paths.cache/'ocr')


def test_core_http_and_native_pdf_without_optional_libraries(tmp_path, monkeypatch):
    import builtins
    import importlib.util
    import pymupdf
    from fastapi.testclient import TestClient
    from studycenter_public.platform.paths import AppPaths
    from studycenter_public.platform.secrets import SecretStore
    from studycenter_public.web import create_app
    real_import = builtins.__import__
    real_find = importlib.util.find_spec
    optional = {'playwright', 'faster_whisper', 'av', 'ctranslate2', 'onnxruntime'}
    def guarded_import(name, *args, **kwargs):
        if name.split('.')[0] in optional:
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)
    def absent(name, *args, **kwargs):
        return None if name.split('.')[0] in optional else real_find(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded_import)
    monkeypatch.setattr(importlib.util, 'find_spec', absent)
    paths = AppPaths.create(tmp_path/'data')
    app = create_app(paths, SecretStore(paths.instance_id, session_only=True))
    client = TestClient(app, base_url=app.state.endpoint)
    for page in ('/overview', '/settings', '/api/components', '/api/speech/status'):
        assert client.get(page).status_code == 200
    assert client.get('/api/speech/status').json()['runtime_installed'] is False
    token = client.get('/api/session').json()['csrf']
    headers = {'Origin':app.state.endpoint, 'X-CSRF-Token':token}
    from uuid import uuid4
    response = client.post('/api/actions', headers=headers, json={
        'actions':[{'type':'create_task','title':'Synthetic core task'}], 'request_id':str(uuid4())})
    assert response.status_code == 200
    pdf = pymupdf.open()
    pdf.new_page().insert_text((40,40), 'Native PDF text remains available without optional components.')
    raw = pdf.tobytes()
    pdf.close()
    document = app.state.documents.import_bytes(raw, title='Synthetic PDF', mime='application/pdf')
    assert 'Native PDF text' in app.state.documents.source_text(document['id'])
    assert app.state.documents.page_image(document['id'], 1).startswith(b'\x89PNG')
    denied = client.post('/api/components/install', headers=headers, json={'component':'ocr'})
    assert denied.status_code == 400
    assert 'bestätigen' in denied.json()['error']
    assert client.get('/api/components').json()['ocr'] is False


@pytest.mark.parametrize('adopted', [False, True])
def test_install_uses_only_running_receipted_public_environment(tmp_path, monkeypatch, adopted):
    import json
    from studycenter_public.platform import components, setup
    root = tmp_path/'public'
    environment = root/'.venv' if adopted else root/'.local/environments/synthetic'
    python = environment/'Scripts/python.exe'
    python.parent.mkdir(parents=True)
    python.write_bytes(b'synthetic own Python')
    (root/'.local').mkdir(exist_ok=True)
    receipt = {'schema':1, 'application':'StudyCenter.Public', 'root':str(root),
        'environment':str(environment), 'python':str(python), 'python_origin':'existing-own' if adopted else 'system'}
    (root/'.local/installation.json').write_text(json.dumps(receipt))
    monkeypatch.setenv('STUDYCENTER_PUBLIC_INSTALL_ROOT', str(root))
    monkeypatch.setenv('STUDYCENTER_PUBLIC_COMPONENT_ROOT', str(tmp_path/'foreign-component-hint'))
    monkeypatch.setattr(components.sys, 'prefix', str(environment))
    monkeypatch.setattr(components.sys, 'executable', str(python))
    monkeypatch.setattr(components.sys, 'platform', 'win32')
    paths = SimpleNamespace(cache=tmp_path/'cache', data=tmp_path/'data')
    assert components.component_root(paths) == root/'.local/components'
    calls = []
    def install(selected_root, name, **kwargs):
        calls.append((selected_root, name, kwargs))
        target = root/'.local/components'
        files = {'ocr/tesseract.exe':b'synthetic binary', 'ocr/tessdata/eng.traineddata':b'eng', 'ocr/tessdata/deu.traineddata':b'deu'}
        import hashlib
        for relative, content in files.items():
            file = target/relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(content)
        (target/'ocr.json').write_text(json.dumps({'schema':1, 'name':'ocr', 'files':{
            relative:{'size':len(content), 'sha256':hashlib.sha256(content).hexdigest()} for relative,content in files.items()}}))
    monkeypatch.setattr(setup, 'ensure_component', install)
    service = ComponentService(paths)
    assert service.install('ocr', allow_download=True)['ocr']
    assert service.install('ocr')['ocr']
    assert len(calls) == 1
    assert calls[0] == (root, 'ocr', {'data_dir':paths.data, 'allow_download':True})
    # A receipt pointing to another environment cannot grant package writes.
    monkeypatch.setattr(components.sys, 'prefix', str(tmp_path/'another-environment'))
    assert components.installation() is None
    with pytest.raises(ValueError, match='Umgebung'):
        service.install('ocr', allow_download=True)
    assert len(calls) == 1


def test_full_bundle_component_hashes_remain_independent(tmp_path, monkeypatch):
    import hashlib
    import json
    from studycenter_public.platform import components
    root = tmp_path/'full'
    browser = root/'components/browsers/chromium-synthetic/chrome.exe'
    browser.parent.mkdir(parents=True)
    browser.write_bytes(b'valid')
    (root/'BUILD-MANIFEST.json').write_text(json.dumps({'files':{
        'components/browsers/chromium-synthetic/chrome.exe':{'size':5, 'sha256':hashlib.sha256(b'valid').hexdigest()},
        'components/ocr/absent-file':{'size':3, 'sha256':'0'*64}}}))
    monkeypatch.setattr(components, 'bundle_root', lambda:root)
    monkeypatch.setattr(components.sys, 'platform', 'win32')
    paths = SimpleNamespace(cache=tmp_path/'cache')
    service = ComponentService(paths)
    assert service.verify('browser')['verified_files'] == 1
    assert service.status()['ocr'] is False
    browser.write_bytes(b'wrong')
    with pytest.raises(ValueError, match='prüfsumme'):
        service.verify('browser')
    assert service.status()['components']['browser']['state'] == 'error'
