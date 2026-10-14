"""Pure child-environment boundary; this does not authorize a VM/browser run."""
import importlib.util
import os
from pathlib import Path
import stat


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('system_browser_vm_controller', ROOT / 'scripts/smoke_system_browser_vm.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


def test_child_home_xdg_and_temp_are_owned_without_changing_parent(tmp_path):
    parent = {
        'HOME': '/synthetic-runner-home', 'XDG_CACHE_HOME': '/synthetic-runner-cache',
        'XDG_CONFIG_HOME': '/synthetic-runner-config', 'XDG_DATA_HOME': '/synthetic-runner-data',
        'TMPDIR': '/synthetic-runner-tmp', 'TMP': '/synthetic-other-tmp', 'TEMP': '/synthetic-other-temp',
        'LD_LIBRARY_PATH': '/synthetic-bundled-libraries', 'LD_PRELOAD': '/synthetic-preload',
        'CHROME_DEVEL_SANDBOX': '/synthetic-borrowed-helper', 'PYTHONHOME': '/synthetic-python-home',
        'PYTHONPATH': '/synthetic-python-path', 'VIRTUAL_ENV': '/synthetic-venv',
        'PATH': '/synthetic-existing-path',
    }
    original = dict(parent)
    global_environment = dict(os.environ)
    child = controller.isolated_child(tmp_path, parent)
    assert child is not parent and parent == original and dict(os.environ) == global_environment
    names = ('HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'TMPDIR')
    paths = {Path(child[name]) for name in names}
    assert len(paths) == len(names)
    assert all(path.is_dir() and path.parent == tmp_path and not path.is_symlink() for path in paths)
    if os.name == 'posix':
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in paths)
    assert child['TMP'] == child['TEMP'] == child['TMPDIR']
    assert Path(child['CHROME_LOG_FILE']).parent == tmp_path
    assert all(name not in child for name in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'CHROME_DEVEL_SANDBOX', 'PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV'))
    assert child['PATH'] == original['PATH']
