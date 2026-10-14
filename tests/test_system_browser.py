import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_system_browser_child_restores_original_libraries_without_mutating_parent(tmp_path, monkeypatch):
    from studycenter_public.desktop.browser import open_system_browser
    program = tmp_path / 'owned-public-program'
    program.mkdir()
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(sys, 'executable', str(program / 'StudyCenter-Public'))
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', str(program / 'components/ocr/lib'))
    monkeypatch.setenv('LD_LIBRARY_PATH_ORIG', 'synthetic-original-library-path')
    calls = []
    class Started:
        def poll(self):
            return None
    monkeypatch.setattr(subprocess, 'Popen', lambda command, **kwargs: calls.append((command, kwargs)) or Started())
    assert open_system_browser('http://127.0.0.1:8767/')
    command, options = calls[0]
    assert command == ['xdg-open', 'http://127.0.0.1:8767/']
    assert options['env']['LD_LIBRARY_PATH'] == os.path.realpath('synthetic-original-library-path')
    assert os.environ['LD_LIBRARY_PATH'] == str(program / 'components/ocr/lib')


@pytest.mark.parametrize('original', [None, ''])
def test_system_browser_child_without_original_library_path_uses_system_defaults(monkeypatch, original):
    from studycenter_public.desktop.browser import external_browser_environment
    monkeypatch.setenv('LD_LIBRARY_PATH', 'synthetic-frozen-library-path')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    if original is None:
        monkeypatch.delenv('LD_LIBRARY_PATH_ORIG', raising=False)
    else:
        monkeypatch.setenv('LD_LIBRARY_PATH_ORIG', original)
    environment = external_browser_environment()
    forwarded = 'LD_LIBRARY_PATH' in environment
    assert forwarded is False
    assert os.environ['LD_LIBRARY_PATH'] == 'synthetic-frozen-library-path'


def test_dispatch_failure_is_reported_without_launching_another_browser(monkeypatch):
    from studycenter_public.desktop.browser import open_system_browser
    monkeypatch.setattr(sys, 'platform', 'linux')
    calls = []
    def missing(command, **kwargs):
        calls.append(command)
        raise FileNotFoundError('Synthetic missing system launcher')
    monkeypatch.setattr(subprocess, 'Popen', missing)
    assert open_system_browser('http://127.0.0.1:8767/') is False
    assert len(calls) == 1


def test_original_environment_cannot_reintroduce_own_bundle_libraries(tmp_path, monkeypatch):
    from studycenter_public.desktop.browser import external_browser_environment
    program = tmp_path / 'owned-program'
    own_library = program / 'components/ocr/lib'
    unrelated = tmp_path / 'original-system-library'
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(program / 'StudyCenter-Public'))
    original = os.pathsep.join([str(own_library), str(unrelated)])
    monkeypatch.setenv('LD_LIBRARY_PATH_ORIG', original)
    environment = external_browser_environment()
    assert environment['LD_LIBRARY_PATH'] == str(unrelated)
    assert os.environ['LD_LIBRARY_PATH_ORIG'] == original


def test_original_library_alias_to_bundle_is_not_forwarded(tmp_path, monkeypatch):
    from studycenter_public.desktop.browser import external_browser_environment
    program = tmp_path / 'owned-program'
    library = program / 'components/ocr/lib'
    library.mkdir(parents=True)
    alias = tmp_path / 'original-library-alias'
    if sys.platform == 'win32':
        import _winapi
        _winapi.CreateJunction(str(library), str(alias))
    else:
        alias.symlink_to(library, target_is_directory=True)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(program / 'StudyCenter-Public'))
    monkeypatch.setenv('LD_LIBRARY_PATH_ORIG', str(alias))
    forwarded = 'LD_LIBRARY_PATH' in external_browser_environment()
    assert forwarded is False
    assert os.environ['LD_LIBRARY_PATH_ORIG'] == str(alias)


@pytest.mark.skipif(sys.platform != 'linux', reason='Real Linux child environment')
def test_actual_dispatch_child_gets_clean_environment(tmp_path, monkeypatch):
    from studycenter_public.desktop.browser import open_system_browser
    launcher = tmp_path / 'xdg-open'
    captured = tmp_path / 'child-environment.json'
    launcher.write_text('#!' + sys.executable + '\nimport json,os,sys\nfrom pathlib import Path\nPath(os.environ["SYNTHETIC_BROWSER_CAPTURE"]).write_text(json.dumps({"library":os.environ.get("LD_LIBRARY_PATH"),"url":sys.argv[1]}))\n')
    launcher.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ.get('PATH', ''))
    monkeypatch.setenv('SYNTHETIC_BROWSER_CAPTURE', str(captured))
    monkeypatch.setenv('LD_LIBRARY_PATH', 'synthetic-frozen-library-path')
    monkeypatch.setenv('LD_LIBRARY_PATH_ORIG', 'synthetic-original-library-path')
    assert open_system_browser('http://127.0.0.1:8767/')
    import time
    deadline = time.monotonic() + 5
    while not captured.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert json.loads(captured.read_text()) == {'library': 'synthetic-original-library-path', 'url': 'http://127.0.0.1:8767/'}
    assert os.environ['LD_LIBRARY_PATH'] == 'synthetic-frozen-library-path'
