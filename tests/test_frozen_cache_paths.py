import os
import json
from pathlib import Path
import runpy
import sys

import pytest


@pytest.fixture
def frozen_program(tmp_path, monkeypatch):
    program = tmp_path / 'owned-public-program'
    program.mkdir()
    executable = program / 'StudyCenter-Public.exe'
    executable.write_bytes(b'Synthetic frozen executable fixture')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(executable))
    monkeypatch.setattr(sys, '_studycenter_startup_error', None, raising=False)
    return executable


@pytest.mark.parametrize('location', ['data', 'cache', 'hub'])
def test_frozen_hook_rejects_redirected_cache_before_assigning_library_paths(tmp_path, monkeypatch, location, frozen_program):
    foreign = tmp_path / 'unrelated-fixture'
    foreign.mkdir()
    sentinel = foreign / 'sentinel.txt'
    sentinel.write_text('Synthetic unrelated content', encoding='utf-8')
    data = tmp_path / 'public-data'
    linked = {'data': data, 'cache': data / 'cache', 'hub': data / 'cache/speech-library/hub'}[location]
    linked.parent.mkdir(parents=True, exist_ok=True)
    if location != 'data':
        (data / 'instance.json').write_text(json.dumps({'application': 'StudyCenter.Public',
            'instance_id': '00000000-0000-4000-8000-000000000001',
            'host_id': 'urn:uuid:00000000-0000-4000-8000-000000000002'}), encoding='utf-8')
    if sys.platform == 'win32':
        import _winapi
        _winapi.CreateJunction(str(foreign), str(linked))
    else:
        linked.symlink_to(foreign, target_is_directory=True)
    variables = ('PLAYWRIGHT_BROWSERS_PATH', 'HF_HOME', 'HF_HUB_CACHE', 'HF_XET_CACHE', 'HF_ASSETS_CACHE',
                 'HF_HUB_DISABLE_XET', 'HF_HUB_DISABLE_TELEMETRY', 'HF_HUB_OFFLINE', 'LD_LIBRARY_PATH')
    for name in variables:
        monkeypatch.setenv(name, 'synthetic-preserved-value')
    monkeypatch.setattr(sys, 'argv', ['synthetic-public-app', '--data-dir', str(data)])
    hook = Path(__file__).resolve().parents[1] / 'packaging/runtime_hook.py'
    runpy.run_path(str(hook))
    assert isinstance(sys._studycenter_startup_error, ValueError)
    assert 'Reparse' in str(sys._studycenter_startup_error) or 'Verknüpfung' in str(sys._studycenter_startup_error)
    assert os.environ['HF_HOME'] == 'synthetic-preserved-value'
    assert os.environ['HF_HUB_CACHE'] == 'synthetic-preserved-value'
    assert list(foreign.iterdir()) == [sentinel]
    assert sentinel.read_text(encoding='utf-8') == 'Synthetic unrelated content'


def test_frozen_hook_rejects_unowned_nonempty_data_before_library_cache(tmp_path, monkeypatch, frozen_program):
    foreign = tmp_path / 'unrelated-fixture'
    foreign.mkdir()
    sentinel = foreign / 'sentinel.txt'
    sentinel.write_text('Synthetic unrelated content', encoding='utf-8')
    for name in ('PLAYWRIGHT_BROWSERS_PATH', 'HF_HOME', 'HF_HUB_CACHE', 'HF_XET_CACHE', 'HF_ASSETS_CACHE',
                 'HF_HUB_DISABLE_XET', 'HF_HUB_DISABLE_TELEMETRY', 'HF_HUB_OFFLINE', 'LD_LIBRARY_PATH'):
        monkeypatch.setenv(name, 'synthetic-preserved-value')
    monkeypatch.setattr(sys, 'argv', ['synthetic-public-app', '--data-dir', str(foreign)])
    hook = Path(__file__).resolve().parents[1] / 'packaging/runtime_hook.py'
    runpy.run_path(str(hook))
    assert isinstance(sys._studycenter_startup_error, ValueError)
    assert 'Public' in str(sys._studycenter_startup_error) or 'Ordner' in str(sys._studycenter_startup_error)
    assert os.environ['HF_HOME'] == 'synthetic-preserved-value'
    assert list(foreign.iterdir()) == [sentinel]


def test_frozen_data_selection_rejects_the_entire_program_tree(tmp_path, monkeypatch):
    from studycenter_public.platform.paths import AppPaths
    program = tmp_path / 'synthetic-public-program'
    program.mkdir()
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(program / 'synthetic-public.exe'))
    data = program / 'data'
    with pytest.raises(ValueError, match='Programmdateien'):
        AppPaths.create(data)
    assert not data.exists()
    outside = tmp_path / 'synthetic-public-data'
    paths = AppPaths.create(outside)
    assert paths.program == program
    assert paths.data == outside
