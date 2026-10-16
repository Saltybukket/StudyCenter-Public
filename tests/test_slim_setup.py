"""Setup policy checks use synthetic archives and isolated installation roots."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def engine():
    path = ROOT / 'src/studycenter_public/platform/setup.py'
    assert path.is_file(), 'The shared standalone setup engine is missing'
    spec = importlib.util.spec_from_file_location('slim_setup', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_help_and_stop_without_environment_never_invoke_uv(tmp_path, monkeypatch):
    setup = engine()
    monkeypatch.setattr(setup.subprocess, 'run', lambda *a, **k: pytest.fail('uv invoked'))
    assert setup.main(['--root', str(tmp_path), '--uv', 'missing-uv', '--stop']) == 0
    with pytest.raises(SystemExit) as result:
        setup.main(['--help'])
    assert result.value.code == 0
    assert not (tmp_path / '.local').exists()


def test_interpreter_selection_tries_system_before_managed(tmp_path, monkeypatch):
    setup = engine()
    calls = []
    def run(command, **kwargs):
        calls.append([str(x) for x in command])
        if 'find' in command:
            if '--no-python-downloads' in command:
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0, stdout=str(tmp_path / 'python.exe'))
        return subprocess.CompletedProcess(command, 0, stdout='')
    monkeypatch.setattr(setup, 'run', run)
    monkeypatch.setattr(setup, 'compatible_python', lambda path: True)
    python, origin = setup.select_python(tmp_path, 'uv')
    assert origin == 'managed'
    assert '--no-python-downloads' in calls[0]
    assert any('install' in call for call in calls)
    assert '3.12' in calls[0]  # compatible system patches are not excluded
    assert all('--system-site-packages' not in call for call in calls)
    environment = setup.uv_environment(tmp_path)
    assert environment['UV_CACHE_DIR'] == str(tmp_path / '.local/uv-cache')
    assert environment['UV_PYTHON_INSTALL_DIR'] == str(tmp_path / '.local/python')


def test_failed_update_retains_working_environment_and_receipt(tmp_path, monkeypatch):
    setup = engine()
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'pyproject.toml').write_text('[project]\nname="synthetic"')
    platform = 'windows' if sys.platform == 'win32' else 'linux'
    (source / f'requirements-{platform}.lock').write_text('synthetic==1')
    old = tmp_path / 'existing'
    old.mkdir()
    (old / 'marker').write_text('keep')
    receipt = {'application': 'StudyCenter.Public', 'environment': str(old),
               'python': str(old / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')), 'source': str(source), 'version': 'old'}
    (tmp_path / '.local').mkdir()
    setup.atomic_json(tmp_path / '.local/installation.json', receipt)
    monkeypatch.setattr(setup, 'select_python', lambda *a: (Path(sys.executable), 'system'))
    monkeypatch.setattr(setup, 'ensure_uv', lambda root, uv: uv, raising=False)
    monkeypatch.setattr(setup, 'run', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('synthetic failure')))
    with pytest.raises(RuntimeError, match='synthetic failure'):
        setup.ensure_environment(tmp_path, 'uv', source=source, update=True)
    assert json.loads((tmp_path / '.local/installation.json').read_text()) == receipt
    assert (old / 'marker').read_text() == 'keep'


def test_warm_environment_does_not_run_uv_or_download(tmp_path, monkeypatch):
    setup = engine()
    (tmp_path / '.local').mkdir()
    receipt = {'application': 'StudyCenter.Public', 'environment': str(tmp_path / 'own'),
               'python': str(tmp_path / 'own' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')), 'version': 'synthetic'}
    setup.atomic_json(tmp_path / '.local/installation.json', receipt)
    monkeypatch.setattr(setup, 'environment_usable', lambda *a: True)
    monkeypatch.setattr(setup, 'run', lambda *a, **k: pytest.fail('network/setup on warm start'))
    monkeypatch.setattr(setup, 'download', lambda *a, **k: pytest.fail('download on warm start'))
    assert setup.ensure_environment(tmp_path, 'missing-uv') == receipt


def test_verified_archive_cache_reused_and_interruption_retries(tmp_path, monkeypatch):
    setup = engine()
    payload = b'synthetic archive'
    record = {'url': 'https://example.invalid/component.zip', 'sha256': hashlib.sha256(payload).hexdigest(), 'size': len(payload), 'filename': 'component.zip'}
    attempts = []
    class Response(io.BytesIO):
        def geturl(self): return record['url']
    def open_url(*a, **k):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError('interrupted')
        return Response(payload)
    monkeypatch.setattr(setup.urllib.request, 'urlopen', open_url)
    with pytest.raises(OSError, match='interrupted'):
        setup.download(tmp_path, record)
    result = setup.download(tmp_path, record)
    assert result.read_bytes() == payload
    assert setup.download(tmp_path, record) == result
    assert len(attempts) == 2
    assert sum(x['bytes'] for x in json.loads((tmp_path / '.local/downloads.json').read_text())['transfers']) == len(payload)


def test_archive_traversal_rejected_before_any_extraction(tmp_path):
    setup = engine()
    archive = tmp_path / 'hostile.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('runtime/okay', b'no write on failure')
        output.writestr('../outside', b'escape')
    destination = tmp_path / 'unpacked'
    with pytest.raises(ValueError, match='archive|Archive'):
        setup.extract_archive(archive, destination)
    assert not (tmp_path / 'outside').exists()
    assert not (destination / 'runtime/okay').exists()


def test_lock_prevents_parallel_setup_and_releases_after_failure(tmp_path):
    setup = engine()
    with setup.setup_lock(tmp_path, timeout=1):
        command = [sys.executable, '-I', str(ROOT / 'src/studycenter_public/platform/setup.py'),
                   '--root', str(tmp_path), '--lock-check']
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode != 0
        assert 'setup' in result.stderr.lower()
    with setup.setup_lock(tmp_path, timeout=1):
        pass


def test_valid_legacy_own_environment_is_adopted_before_uv(tmp_path, monkeypatch):
    setup = engine()
    legacy = tmp_path / '.venv'
    legacy.mkdir()
    monkeypatch.setattr(setup, 'environment_usable', lambda *a: True)
    monkeypatch.setattr(setup, 'application_ready', lambda *a: True, raising=False)
    monkeypatch.setattr(setup, 'run', lambda *a, **k: pytest.fail('valid own environment replaced'))
    receipt = setup.ensure_environment(tmp_path, 'missing-uv', source=ROOT)
    assert receipt['environment'] == str(legacy)
    assert (tmp_path / '.local/python-path.txt').read_text().strip() == receipt['python']


def test_single_component_retry_reuses_valid_archive_and_preserves_other_components(tmp_path, monkeypatch):
    setup = engine()
    platform = 'windows' if sys.platform == 'win32' else 'linux'
    payload = tmp_path / 'ocr.zip'
    with zipfile.ZipFile(payload, 'w') as archive:
        archive.writestr('runtime/tesseract.exe', b'synthetic executable')
        archive.writestr('notices/LICENSE.txt', b'synthetic notice')
    record = {'url': 'https://example.invalid/ocr.zip', 'filename': 'ocr.zip',
              'size': payload.stat().st_size, 'sha256': hashlib.sha256(payload.read_bytes()).hexdigest()}
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'schema': 1, 'python': '3.12', 'platform': platform + '-x86_64', 'components': {'ocr': {'archive': record}}}))
    monkeypatch.setattr(setup, 'download', lambda *a: payload)
    base = tmp_path / '.local/components'
    (base / 'browsers').mkdir(parents=True)
    (base / 'browsers/keep').write_text('unchanged')
    (base / 'ocr').mkdir()
    (base / 'ocr/partial').write_text('interrupted setup')
    result = setup.ensure_component(tmp_path, 'ocr', manifest=manifest, allow_download=True)
    assert result['name'] == 'ocr'
    assert (base / 'ocr/tesseract.exe').read_bytes() == b'synthetic executable'
    assert (base / 'browsers/keep').read_text() == 'unchanged'
    monkeypatch.setattr(setup, 'download', lambda *a: pytest.fail('valid component downloaded again'))
    assert setup.ensure_component(tmp_path, 'ocr', manifest=manifest) == result


def test_launch_preserves_saved_data_directory(tmp_path, monkeypatch):
    setup = engine()
    receipt = {'application': 'StudyCenter.Public', 'environment': str(tmp_path / 'own'),
               'python': str(tmp_path / 'own/Scripts/python.exe'), 'data_dir': str(tmp_path / 'existing-data')}
    monkeypatch.setattr(setup, 'ensure_environment', lambda *a, **k: receipt)
    calls = []
    monkeypatch.setattr(setup.subprocess, 'call', lambda command, **kwargs: calls.append(command) or 0)
    assert setup.main(['--root', str(tmp_path), '--uv', 'uv']) == 0
    assert calls[0][calls[0].index('--data-dir') + 1] == str(tmp_path / 'existing-data')


def test_stop_legacy_environment_uses_it_without_installing(tmp_path, monkeypatch):
    setup = engine()
    monkeypatch.setattr(setup, 'environment_usable', lambda *a: True)
    monkeypatch.setattr(setup, 'run', lambda *a, **k: pytest.fail('setup on stop'))
    calls = []
    monkeypatch.setattr(setup.subprocess, 'call', lambda command, **kwargs: calls.append(command) or 0)
    assert setup.main(['--root', str(tmp_path), '--stop']) == 0
    assert calls[0][0] == str(tmp_path / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python'))
    assert calls[0][-1] == '--quit'


def test_missing_uv_is_repaired_only_inside_own_tools(tmp_path, monkeypatch):
    setup = engine()
    archive = tmp_path / 'uv.zip'
    binary = 'uv.exe' if sys.platform == 'win32' else 'uv-x86_64-unknown-linux-gnu/uv'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr(binary, b'synthetic tool')
    monkeypatch.setattr(setup, 'download', lambda *a: archive)
    uv = setup.ensure_uv(tmp_path, tmp_path / 'missing.exe')
    assert uv.is_relative_to(tmp_path / '.local/tools')
    assert uv.read_bytes() == b'synthetic tool'
    monkeypatch.setattr(setup, 'download', lambda *a: pytest.fail('valid tool downloaded again'))
    assert setup.ensure_uv(tmp_path, uv) == uv


def test_verified_system_browser_avoids_archive_and_is_revalidated(tmp_path, monkeypatch):
    setup = engine()
    environment = tmp_path / '.local/environments/synthetic'
    receipt = {'schema': 1, 'application': 'StudyCenter.Public', 'root': str(tmp_path),
               'environment': str(environment), 'python': str(environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')),
               'source': str(ROOT), 'uv': 'uv', 'extras': []}
    setup.atomic_json(tmp_path / '.local/installation.json', receipt)
    platform = 'windows' if sys.platform == 'win32' else 'linux'
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'schema': 1, 'python': '3.12', 'platform': platform + '-x86_64',
        'components': {'browser': {'extra': 'browser', 'archive': {'url': 'https://example.invalid/unused.zip'}}}}))
    monkeypatch.setattr(setup, 'ensure_uv', lambda *a: Path('uv'))
    monkeypatch.setattr(setup, 'verified_system_browser', lambda *a: '/opt/google/chrome/chrome', raising=False)
    monkeypatch.setattr(setup, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0))
    monkeypatch.setattr(setup.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0))
    monkeypatch.setattr(setup, 'download', lambda *a: pytest.fail('verified system browser should avoid the archive'))
    result = setup.ensure_component(tmp_path, 'browser', manifest=manifest, allow_download=True)
    assert result['system_browser'] is True
    assert setup.component_valid(tmp_path, 'browser')
    monkeypatch.setattr(setup, 'verified_system_browser', lambda *a: None)
    assert not setup.component_valid(tmp_path, 'browser')


def test_cold_source_setup_saves_original_local_data_default(tmp_path, monkeypatch):
    setup = engine()
    receipt = {'application': 'StudyCenter.Public', 'environment': str(tmp_path / 'own'),
               'python': str(tmp_path / 'own/Scripts/python.exe'), 'source': str(tmp_path)}
    monkeypatch.setattr(setup, 'ensure_environment', lambda *a, **k: receipt)
    assert setup.main(['--root', str(tmp_path), '--source', str(tmp_path), '--uv', 'uv', '--setup-only']) == 0
    saved = json.loads((tmp_path / '.local/installation.json').read_text())
    assert saved['data_dir'] == str(tmp_path / '.local/data')
    assert not (tmp_path / '.local/data').exists()


def test_prebuilt_release_dependency_installed_before_source_application(tmp_path, monkeypatch):
    setup = engine()
    source = tmp_path / 'source'
    (source / 'packaging').mkdir(parents=True)
    (source / 'pyproject.toml').write_text('[project]\nname="synthetic"')
    platform = 'windows' if sys.platform == 'win32' else 'linux'
    (source / f'requirements-{platform}.lock').write_text('proxy-tools==0.1.0')
    wheel = tmp_path / 'proxy_tools-0.1.0-py3-none-any.whl'
    wheel.write_bytes(b'synthetic verified wheel')
    (source / f'packaging/slim-{platform}-x86_64.json').write_text(json.dumps({
        'schema': 1, 'python': '3.12', 'platform': platform + '-x86_64', 'version': 'synthetic',
        'extra_wheels': [{'url': 'https://example.invalid/' + wheel.name, 'filename': wheel.name,
                          'size': wheel.stat().st_size, 'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest()}]}))
    calls = []
    monkeypatch.setattr(setup, 'ensure_uv', lambda root, uv: uv)
    monkeypatch.setattr(setup, 'select_python', lambda *a: (Path(sys.executable), 'system'))
    monkeypatch.setattr(setup, 'environment_usable', lambda *a: True)
    monkeypatch.setattr(setup, 'application_ready', lambda *a: False)
    monkeypatch.setattr(setup, 'download', lambda *a: wheel)
    monkeypatch.setattr(setup, 'run', lambda command, **kwargs: calls.append([str(x) for x in command]))
    setup.ensure_environment(tmp_path, 'uv', source=source)
    dependency = next(index for index, call in enumerate(calls) if str(wheel) in call)
    application = next(index for index, call in enumerate(calls) if '--editable' in call)
    assert dependency < application
    assert '--only-binary' in calls[dependency]


def test_existing_own_managed_python_avoids_install_command(tmp_path, monkeypatch):
    setup = engine()
    managed = tmp_path / '.local/python/cpython-3.12/python.exe'
    managed.parent.mkdir(parents=True)
    managed.write_bytes(b'synthetic interpreter')
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if '--python-preference' in command:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout=str(managed))
    monkeypatch.setattr(setup, 'run', run)
    monkeypatch.setattr(setup, 'compatible_python', lambda *a: True)
    assert setup.select_python(tmp_path, 'uv') == (managed, 'managed')
    assert not any('install' in command for command in calls)
