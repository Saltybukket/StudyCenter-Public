"""Real Linux filesystem controls for the optional own desktop entry."""
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux openat and filesystem controls')


@pytest.fixture
def program(tmp_path):
    directory = tmp_path / 'program space "quote" \\ $cash `tick` %percent'
    directory.mkdir()
    (directory / 'start.sh').write_text('#!/bin/sh\nexit 0\n')
    (directory / 'start.sh').chmod(0o755)
    recipe = ROOT / 'scripts/package_linux_desktop.py'
    spec = importlib.util.spec_from_file_location('desktop_package', recipe)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.prepare(directory)
    return directory


def install(program, tmp_path, data):
    environment = os.environ.copy()
    environment.update(HOME=str(tmp_path / 'synthetic-home'), XDG_DATA_HOME=str(data))
    return subprocess.run(['/bin/sh', str(program / 'install-desktop.sh')], env=environment,
                          capture_output=True, timeout=5)


@pytest.mark.parametrize('kind', ['regular', 'symlink', 'fifo'])
def test_existing_leaf_never_changes_foreign_bytes_or_mode(program, tmp_path, kind):
    data = tmp_path / 'xdg'
    applications = data / 'applications'
    applications.mkdir(parents=True)
    foreign = tmp_path / 'foreign.txt'
    foreign.write_bytes(b'Synthetic foreign content')
    foreign.chmod(0o640)
    leaf = applications / 'studycenter-public.desktop'
    if kind == 'regular':
        leaf.write_bytes(b'Unknown existing desktop entry')
        leaf.chmod(0o644)
    elif kind == 'symlink':
        leaf.symlink_to(foreign)
    else:
        os.mkfifo(leaf, 0o620)
    original_leaf = leaf.lstat()
    original_bytes = leaf.read_bytes() if kind == 'regular' else None
    result = install(program, tmp_path, data)
    assert result.returncode != 0
    assert leaf.lstat().st_ino == original_leaf.st_ino
    assert stat.S_IMODE(leaf.lstat().st_mode) == stat.S_IMODE(original_leaf.st_mode)
    if original_bytes is not None:
        assert leaf.read_bytes() == original_bytes
    assert foreign.read_bytes() == b'Synthetic foreign content'
    assert stat.S_IMODE(foreign.stat().st_mode) == 0o640


@pytest.mark.parametrize('link_level', ['applications', 'ancestor'])
def test_directory_links_never_create_foreign_desktop(program, tmp_path, link_level):
    foreign = tmp_path / 'foreign-directory'
    foreign.mkdir()
    marker = foreign / 'preserved.txt'
    marker.write_bytes(b'Preserved')
    data = tmp_path / 'xdg'
    if link_level == 'applications':
        data.mkdir()
        (data / 'applications').symlink_to(foreign, target_is_directory=True)
    else:
        data.symlink_to(foreign, target_is_directory=True)
        data = data / 'child'
    result = install(program, tmp_path, data)
    assert result.returncode != 0
    assert list(foreign.iterdir()) == [marker]
    assert marker.read_bytes() == b'Preserved'


def test_legitimate_empty_desktop_and_exact_exec_escaping(program, tmp_path):
    data = tmp_path / 'fresh' / 'xdg'
    result = install(program, tmp_path, data)
    assert result.returncode == 0, result.stderr.decode()
    leaf = data / 'applications' / 'studycenter-public.desktop'
    assert stat.S_IMODE(leaf.stat().st_mode) == 0o600
    escaped = str(program / 'start.sh').replace('\\', '\\\\\\\\')
    for character in ('"', '$', '`'):
        escaped = escaped.replace(character, '\\\\' + character)
    escaped = escaped.replace('%', '%%')
    lines = leaf.read_text().splitlines()
    assert 'Exec=/usr/bin/env -- "' + escaped + '"' in lines
    assert lines.count('[Desktop Entry]') == 1
    original = leaf.read_bytes()
    assert install(program, tmp_path, data).returncode != 0
    assert leaf.read_bytes() == original


def test_packaging_receipt_binds_new_helper_and_rejects_overwrite(program):
    import json
    receipt = json.loads((program / 'DESKTOP-INSTALL-RECIPE.json').read_text())
    for name in ('desktop-install', 'install-desktop.sh'):
        assert receipt['outputs'][name]['sha256'] == hashlib.sha256((program / name).read_bytes()).hexdigest()
    assert receipt['sources']['desktop-install.c']['sha256'] == hashlib.sha256((ROOT / 'packaging/linux/desktop-install.c').read_bytes()).hexdigest()
    assert receipt['compiler']['version']
    assert not any(str(ROOT) in str(value) for value in receipt.values())
    spec = importlib.util.spec_from_file_location('desktop_package', ROOT / 'scripts/package_linux_desktop.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(FileExistsError):
        module.prepare(program)


def test_effective_owner_without_execute_permission_cannot_install(program, tmp_path):
    import pwd
    credentials = {}
    uid, gid = os.geteuid(), os.getegid()
    if uid == 0:
        try:
            account = pwd.getpwnam('nobody')
        except KeyError:
            pytest.skip('No existing unprivileged fixture account')
        uid, gid = account.pw_uid, account.pw_gid
        credentials = {'user': uid, 'group': gid, 'extra_groups': []}
        # Only these two pytest-created fixture ancestors need traversal.
        tmp_path.parent.chmod(0o755)
        tmp_path.chmod(0o755)
    start = program / 'start.sh'
    if os.geteuid() == 0:
        os.chown(start, uid, gid)
    start.chmod(0o401)
    data = tmp_path / 'owned-unprivileged-data'
    data.mkdir(mode=0o700)
    if os.geteuid() == 0:
        os.chown(data, uid, gid)
    environment = os.environ.copy()
    environment.update(HOME=str(data), XDG_DATA_HOME=str(data))
    probe = subprocess.run([sys.executable, '-c',
                            'import os,sys; print(os.access(sys.argv[1], os.X_OK, effective_ids=True))',
                            str(start)], env=environment, capture_output=True, timeout=5, **credentials)
    assert probe.returncode == 0 and probe.stdout.strip() == b'False'
    result = subprocess.run(['/bin/sh', str(program / 'install-desktop.sh')], env=environment,
                            capture_output=True, timeout=5, **credentials)
    assert result.returncode != 0
    assert not (data / 'applications' / 'studycenter-public.desktop').exists()
    assert stat.S_IMODE(start.stat().st_mode) == 0o401


@pytest.mark.parametrize('name', ['program%percent', 'program space "quote" \\ $cash `tick` %percent'])
def test_actual_gio_percent_and_combined_path_roundtrip(program, tmp_path, name):
    import shutil
    gio = shutil.which('gio')
    if not gio:
        pytest.skip('Existing gio parser unavailable; no installation attempted')
    target = tmp_path / 'parser-target' / name
    target.mkdir(parents=True)
    for file in ('desktop-install', 'install-desktop.sh'):
        shutil.copyfile(program / file, target / file)
        (target / file).chmod(0o755)
    sentinel = tmp_path / 'sentinel.py'
    sentinel.write_text('import os\nfrom pathlib import Path\nPath(os.environ["SYNTHETIC_RESULT"]).write_text(os.environ["SYNTHETIC_NONCE"])\n')
    (target / 'start.sh').write_text('#!/bin/sh\nexec "$SYNTHETIC_PYTHON" "$SYNTHETIC_SENTINEL"\n')
    (target / 'start.sh').chmod(0o755)
    environment = os.environ.copy()
    for variable in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'LD_LIBRARY_PATH', 'LD_PRELOAD'):
        environment.pop(variable, None)
    for variable in ('HOME', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_RUNTIME_DIR'):
        own = tmp_path / variable.lower()
        own.mkdir(mode=0o700)
        environment[variable] = str(own)
    result_file = tmp_path / 'sentinel-result'
    nonce = uuid.uuid4().hex
    environment.update(SYNTHETIC_RESULT=str(result_file), SYNTHETIC_NONCE=nonce,
                       SYNTHETIC_PYTHON=sys.executable, SYNTHETIC_SENTINEL=str(sentinel), GIO_USE_VFS='local')
    created = subprocess.run([str(target / 'desktop-install')], env=environment, capture_output=True, timeout=5)
    assert created.returncode == 0
    desktop = Path(environment['XDG_DATA_HOME']) / 'applications/studycenter-public.desktop'
    parsed = subprocess.run([gio, 'launch', str(desktop)], env=environment, capture_output=True, timeout=5)
    assert parsed.returncode == 0
    deadline = time.monotonic() + 3
    while not result_file.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert result_file.read_text() == nonce
