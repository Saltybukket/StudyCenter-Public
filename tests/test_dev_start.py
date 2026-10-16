"""Small offline checks for the developer bootstrap's environment boundary."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import venv

import pytest


ROOT = Path(__file__).resolve().parents[1]


def bootstrap():
    source = ROOT / 'scripts/setup_dev.py'
    assert source.is_file(), 'The single-command developer bootstrap is missing'
    spec = importlib.util.spec_from_file_location('setup_dev', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_exact_environment_is_reused_without_global_site_packages(tmp_path):
    module = bootstrap()
    own = tmp_path / '.venv'
    venv.EnvBuilder(with_pip=False).create(own)
    python = module.environment_python(tmp_path)
    assert module.environment_usable(tmp_path, python)
    result = subprocess.run([str(python), '-I', '-c',
                             'import sys; print(sys.prefix); print(sys.prefix != sys.base_prefix)'],
                            capture_output=True, text=True, check=True)
    assert Path(result.stdout.splitlines()[0]).resolve() == own.resolve()
    assert result.stdout.splitlines()[1] == 'True'
    assert not module.environment_usable(tmp_path, Path(sys.executable))


def test_system_site_packages_environment_is_not_reused(tmp_path):
    module = bootstrap()
    venv.EnvBuilder(with_pip=False, system_site_packages=True).create(tmp_path / '.venv')
    assert not module.environment_usable(tmp_path, module.environment_python(tmp_path))


def test_stale_environment_is_preserved_before_replacement(tmp_path):
    module = bootstrap()
    own = tmp_path / '.venv'
    own.mkdir()
    marker = own / 'synthetic-marker'
    marker.write_text('keep')
    previous = module.preserve_environment(tmp_path)
    assert not own.exists()
    assert (previous / marker.name).read_text() == 'keep'
    assert previous.parent == tmp_path / '.local'


def test_linked_environment_is_refused_without_touching_target(tmp_path):
    module = bootstrap()
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    (foreign / 'marker').write_text('unchanged')
    try:
        (tmp_path / '.venv').symlink_to(foreign, target_is_directory=True)
    except OSError:
        pytest.skip('Symlink creation unavailable for this Windows account')
    with pytest.raises(ValueError, match='link|reparse'):
        module.preserve_environment(tmp_path)
    assert (foreign / 'marker').read_text() == 'unchanged'
