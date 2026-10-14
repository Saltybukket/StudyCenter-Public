import json
import os
from pathlib import Path
import runpy
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_hook_defers_owned_folder_refusal_without_changing_environment(tmp_path, monkeypatch):
    program = tmp_path / 'owned-program'
    program.mkdir()
    executable = program / 'StudyCenter-Public.exe'
    executable.write_bytes(b'Synthetic public executable')
    foreign = tmp_path / 'foreign-data'
    foreign.mkdir()
    sentinel = foreign / 'sentinel.txt'
    sentinel.write_text('Synthetic unrelated content')
    monkeypatch.setattr(sys, 'executable', str(executable))
    monkeypatch.setattr(sys, '_studycenter_startup_error', None, raising=False)
    monkeypatch.setattr(sys, 'argv', ['synthetic-public-app', '--data-dir', str(foreign), '--no-browser'])
    monkeypatch.setenv('HF_HOME', 'synthetic-preserved-cache')
    runpy.run_path(str(ROOT / 'packaging/runtime_hook.py'))
    assert isinstance(sys._studycenter_startup_error, ValueError)
    assert 'Public' in str(sys._studycenter_startup_error)
    assert os.environ['HF_HOME'] == 'synthetic-preserved-cache'
    assert list(foreign.iterdir()) == [sentinel]
    assert sentinel.read_text() == 'Synthetic unrelated content'


def test_deferred_refusal_exits_before_application_or_diagnostics(tmp_path, monkeypatch):
    import studycenter_public.__main__ as application
    foreign = tmp_path / 'foreign-data'
    foreign.mkdir()
    sentinel = foreign / 'sentinel.txt'
    sentinel.write_text('Synthetic unrelated content')
    monkeypatch.setattr(sys, '_studycenter_startup_error', ValueError('Synthetic foreign-folder refusal'), raising=False)
    monkeypatch.setattr(sys, 'argv', ['synthetic-public-app', '--data-dir', str(foreign), '--component-test'])
    monkeypatch.setattr(application, 'main', lambda: pytest.fail('Application must not start after a preflight refusal'))
    namespace = runpy.run_path(str(ROOT / 'packaging/frozen_entry.py'))
    execute = namespace['execute']
    messages = []
    execute.__globals__['show_startup_error'] = messages.append
    execute.__globals__['component_check'] = lambda argv: pytest.fail('Component diagnostics must not start after refusal')
    assert execute() == 2
    assert messages == ['Synthetic foreign-folder refusal']
    assert list(foreign.iterdir()) == [sentinel]


@pytest.mark.parametrize('flag', ['--no-browser', '--self-test', '--component-test', '--quit'])
def test_noninteractive_refusal_does_not_display_a_windows_dialog(monkeypatch, capsys, flag):
    namespace = runpy.run_path(str(ROOT / 'packaging/frozen_entry.py'))
    show = namespace['show_startup_error']
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'argv', ['synthetic-public-app', flag])
    import ctypes
    class DialogTrap:
        def MessageBoxW(self, *args):
            pytest.fail('Noninteractive diagnostics must never open a modal dialog')
    monkeypatch.setattr(ctypes, 'windll', type('SyntheticWindows', (), {'user32': DialogTrap()})(), raising=False)
    show('Synthetic folder refusal')
    assert 'Synthetic folder refusal' in capsys.readouterr().err


def test_interactive_refusal_displays_only_the_friendly_message(monkeypatch):
    show = runpy.run_path(str(ROOT / 'packaging/frozen_entry.py'))['show_startup_error']
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'argv', ['synthetic-public-app'])
    import ctypes
    messages = []
    class SyntheticDialog:
        def MessageBoxW(self, *args):
            messages.append(args)
    monkeypatch.setattr(ctypes, 'windll', type('SyntheticWindows', (), {'user32': SyntheticDialog()})(), raising=False)
    show('Synthetic folder refusal')
    assert messages == [(None, 'Synthetic folder refusal', 'StudyCenter-Public: Start nicht möglich', 0x10)]


def test_owned_data_hook_and_application_continue_normally(tmp_path, monkeypatch):
    program = tmp_path / 'owned-program'
    program.mkdir()
    executable = program / 'StudyCenter-Public.exe'
    executable.write_bytes(b'Synthetic public executable')
    data = tmp_path / 'owned-data'
    data.mkdir()
    (data / 'instance.json').write_text(json.dumps({'application': 'StudyCenter.Public',
        'instance_id': '00000000-0000-4000-8000-000000000001',
        'host_id': 'urn:uuid:00000000-0000-4000-8000-000000000002'}))
    monkeypatch.setattr(sys, 'executable', str(executable))
    monkeypatch.setattr(sys, '_studycenter_startup_error', ValueError('Synthetic prior refusal'), raising=False)
    monkeypatch.setattr(sys, 'argv', ['synthetic-public-app', '--data-dir', str(data), '--no-browser'])
    for name in ('PLAYWRIGHT_BROWSERS_PATH', 'HF_HOME', 'HF_HUB_CACHE', 'HF_XET_CACHE', 'HF_ASSETS_CACHE',
                 'HF_HUB_DISABLE_XET', 'HF_HUB_DISABLE_TELEMETRY', 'HF_HUB_OFFLINE', 'LD_LIBRARY_PATH'):
        monkeypatch.setenv(name, os.environ.get(name, 'synthetic-previous-value'))
    runpy.run_path(str(ROOT / 'packaging/runtime_hook.py'))
    assert sys._studycenter_startup_error is None
    assert Path(os.environ['HF_HOME']) == data / 'cache/speech-library'
    assert Path(os.environ['PLAYWRIGHT_BROWSERS_PATH']) == program / 'components/browsers'
    import studycenter_public.__main__ as application
    calls = []
    monkeypatch.setattr(application, 'main', lambda: calls.append('normal application') or 0)
    execute = runpy.run_path(str(ROOT / 'packaging/frozen_entry.py'))['execute']
    assert execute() == 0
    assert calls == ['normal application']
