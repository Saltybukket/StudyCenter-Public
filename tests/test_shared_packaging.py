"""Distribution wiring for the shared product, independent of real accounts."""
import tomllib
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def test_distribution_includes_shared_resources_and_optional_runtimes():
    from studycenter_public import __version__
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    assert project['project']['version'] == __version__
    patterns = project['tool']['setuptools']['package-data']['studycenter_public']
    assert {'core/templates/*.html', 'core/static/*', 'core/static/vendor/*'} <= set(patterns)
    base = ' '.join(project['project']['dependencies']).lower()
    assert 'python-dotenv==' in base
    assert all(name not in base for name in ('playwright', 'faster-whisper', 'sherpa-onnx'))
    speech = ' '.join(project['project']['optional-dependencies']['speech'])
    assert 'faster-whisper==' in speech and 'sherpa-onnx==' in speech


def test_default_launcher_uses_product_and_owned_shutdown(tmp_path, monkeypatch):
    import studycenter_public.__main__ as launcher
    import studycenter_public.product as product
    calls = []
    app = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setattr(product, 'create_app', lambda paths: calls.append(('product', paths)) or app)
    class FakeRuntime:
        def __init__(self, paths, port):
            self.paths = paths
            self.thread = SimpleNamespace(join=lambda: calls.append(('join',)))
        def acquire(self): return 'http://127.0.0.1:43123'
        def start(self, received):
            assert received is app and received.state.stop_callback == self.stop
            calls.append(('start',))
        def stop(self): calls.append(('stop',))
        def close(self): calls.append(('close',))
    monkeypatch.setattr(launcher, 'Runtime', FakeRuntime)
    assert launcher.main(['--data-dir', str(tmp_path / 'synthetic'), '--no-browser', '--port', '0']) == 0
    assert [entry[0] for entry in calls] == ['product', 'start', 'join', 'close']


def test_autostart_uses_instance_link_and_never_deletes_neighbor(tmp_path, monkeypatch):
    import base64
    from studycenter_public.platform import desktop_lifecycle as lifecycle
    monkeypatch.setattr(lifecycle.sys, 'platform', 'win32')
    monkeypatch.setenv('APPDATA', str(tmp_path / 'appdata'))
    monkeypatch.setenv('SystemRoot', str(tmp_path / 'windows'))
    root = tmp_path / 'install'; root.mkdir(); (root / 'start.cmd').write_text('fixture')
    monkeypatch.setenv('STUDYCENTER_PUBLIC_INSTALL_ROOT', str(root))
    paths = SimpleNamespace(data=tmp_path / 'data', instance_id='synthetic-instance')
    called = []
    monkeypatch.setattr(lifecycle.subprocess, 'run', lambda args, **kw: called.append(args))
    lifecycle.set_autostart(True, paths)
    script = base64.b64decode(called[0][-1]).decode('utf-16le')
    assert 'StudyCenter-Public-synthetic-instance.lnk' in script
    assert str(paths.data) in script and str(root / 'start.cmd') in script
    owned = lifecycle._startup(paths); owned.touch()
    neighbor = owned.with_name('StudyCenter.lnk'); neighbor.write_text('preserve')
    lifecycle.set_autostart(False, paths)
    assert not owned.exists() and neighbor.read_text() == 'preserve'


def test_frozen_restart_uses_own_executable_and_loopback_port(tmp_path, monkeypatch):
    import json
    from studycenter_public.platform import desktop_lifecycle as lifecycle
    monkeypatch.setattr(lifecycle.sys, 'platform', 'win32')
    monkeypatch.setattr(lifecycle.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(lifecycle.sys, 'executable', str(tmp_path / 'StudyCenter-Public.exe'))
    (tmp_path / 'runtime.json').write_text(json.dumps({'url':'http://127.0.0.1:34567'}))
    calls = []
    monkeypatch.setattr(lifecycle.subprocess, 'Popen', lambda command, **kwargs: calls.append(command) or SimpleNamespace(pid=123))
    assert lifecycle.launch_restart(tmp_path) == 123
    assert calls[0][0] == str(tmp_path / 'StudyCenter-Public.exe')
    assert calls[0][calls[0].index('--port') + 1] == '34567'
    assert '--after-process' in calls[0] and '-m' not in calls[0]


def test_self_test_acquires_instance_lock_before_app_creation(tmp_path, monkeypatch):
    import studycenter_public.__main__ as launcher
    import studycenter_public.product as product
    calls = []
    class FakeRuntime:
        def __init__(self, paths, port): pass
        def acquire(self): calls.append('acquire')
        def close(self): calls.append('close')
    monkeypatch.setattr(launcher, 'Runtime', FakeRuntime)
    def create(paths):
        assert calls == ['acquire']
        calls.append('app')
        return SimpleNamespace(routes=[SimpleNamespace(path='/board')])
    monkeypatch.setattr(product, 'create_app', create)
    assert launcher.main(['--self-test', '--data-dir', str(tmp_path / 'data')]) == 0
    assert calls == ['acquire', 'app', 'close']


def test_linux_restart_passes_owned_pidfd_and_loopback_port(tmp_path, monkeypatch):
    import json
    from studycenter_public.platform import desktop_lifecycle as lifecycle
    monkeypatch.setattr(lifecycle.sys, 'platform', 'linux')
    monkeypatch.setattr(lifecycle.sys, 'frozen', False, raising=False)
    monkeypatch.setattr(lifecycle.os, 'pidfd_open', lambda pid, flags: 177, raising=False)
    closed = []
    monkeypatch.setattr(lifecycle.os, 'close', lambda fd: closed.append(fd))
    (tmp_path / 'runtime.json').write_text(json.dumps({'url':'http://127.0.0.1:34567'}))
    calls = []
    monkeypatch.setattr(lifecycle.subprocess, 'Popen', lambda command, **kwargs: calls.append((command,kwargs)) or SimpleNamespace(pid=123))
    assert lifecycle.launch_restart(tmp_path) == 123
    command, options = calls[0]
    assert command[command.index('--port') + 1] == '34567'
    assert command[command.index('--after-pidfd') + 1] == '177'
    assert options['pass_fds'] == (177,) and options['start_new_session'] is True
    assert closed == [177]


def test_linux_restart_refuses_without_pidfd(tmp_path, monkeypatch):
    import pytest
    from studycenter_public.platform import desktop_lifecycle as lifecycle
    monkeypatch.setattr(lifecycle.sys, 'platform', 'linux')
    monkeypatch.delattr(lifecycle.os, 'pidfd_open', raising=False)
    monkeypatch.setattr(lifecycle.subprocess, 'Popen', lambda *a, **k: pytest.fail('No unsafe successor'))
    with pytest.raises(ValueError, match='Neustart'):
        lifecycle.launch_restart(tmp_path)


def test_frozen_build_has_separate_console_bridge_and_source_snapshot():
    from scripts import build_release
    text = (ROOT / 'scripts/build_release.py').read_text(encoding='utf-8')
    assert 'packaging/mcp_entry.py' in build_release.PACKAGE_DEFINITIONS
    assert "'--name',helper_name" in text and "'--console'" in text
    assert "verify_native_origins(helper_work/helper_name/'COLLECT-00.toc',allowed)" in text
    assert (ROOT / 'packaging/mcp_entry.py').is_file()


def test_speech_component_pins_platform_wheels_and_ships_notices():
    import json
    import re
    records = json.loads((ROOT / 'src/studycenter_public/resources/sherpa-provenance.json').read_text(encoding='utf-8'))
    assert records['version'] == '1.13.8' and records['onnxruntime_version'] == '1.28.2'
    for platform in ('windows', 'linux'):
        wheels = records['wheels'][platform]
        assert len(wheels) == 2
        assert all(re.fullmatch(r'[0-9a-f]{64}', wheel['sha256']) and wheel['url'].startswith('https://files.pythonhosted.org/') and wheel['size'] > 0 for wheel in wheels)
        assert {wheel['filename'].split('-')[0] for wheel in wheels} == {'sherpa_onnx', 'sherpa_onnx_core'}
        lock = (ROOT / f'requirements-{platform}.lock').read_text(encoding='utf-8')
        assert 'sherpa-onnx==1.13.8' in lock and 'sherpa-onnx-core==1.13.8' in lock
    for name in ('SHERPA-NOTICES.txt','SHERPA-APACHE-2.0.txt','ONNXRUNTIME-LICENSE.txt','ONNXRUNTIME-THIRD-PARTY-NOTICES.txt'):
        assert (ROOT / 'src/studycenter_public/resources' / name).stat().st_size > 100


def test_beta5_builder_does_not_reuse_beta3_python_size_estimates():
    source = (ROOT / 'scripts/build_slim.py').read_text(encoding='utf-8')
    assert 'reports-local/beta3/python-wheel-measurements.json' not in source
