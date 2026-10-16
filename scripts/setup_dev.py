"""One project-local developer setup, called by start.cmd / start.sh."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]


def regular_directory(path):
    path = Path(path).absolute()
    if path.is_symlink() or (path.exists() and
            getattr(path.lstat(), 'st_file_attributes', 0) & 0x400):
        raise ValueError(f'Refusing linked/reparse setup directory: {path}')
    if path.exists() and not path.is_dir():
        raise ValueError(f'Setup needs a directory: {path}')
    return path


def environment_python(root):
    return Path(root) / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def environment_usable(root, python):
    own = regular_directory(Path(root) / '.venv')
    if not python.is_file():
        return False
    try:
        config = (own / 'pyvenv.cfg').read_text(encoding='utf-8').lower()
        if 'include-system-site-packages = false' not in config:
            return False
        result = subprocess.run([str(python), '-I', '-c',
            'import json,sys; print(json.dumps([sys.prefix,sys.base_prefix,list(sys.version_info[:2])]))'],
            capture_output=True, text=True, timeout=15, check=True)
        prefix, base, version = json.loads(result.stdout)
        return Path(prefix).resolve() == own.resolve() and prefix != base and version == [3, 12]
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def preserve_environment(root):
    root = Path(root)
    own = regular_directory(root / '.venv')
    local = regular_directory(root / '.local')
    local.mkdir(exist_ok=True)
    previous = local / ('previous-venv-' + uuid4().hex)
    own.rename(previous)
    print(f'Previous environment kept at {previous}', flush=True)
    return previous


def run(command, **kwargs):
    print(' '.join(map(str, command[:4])), flush=True)
    subprocess.run(list(map(str, command)), check=True, cwd=ROOT, **kwargs)


def dependencies(uv, python):
    platform = 'windows' if os.name == 'nt' else 'linux'
    lock = ROOT / f'requirements-{platform}.lock'
    stamp = ROOT / '.venv/.studycenter-public-setup'
    digest = hashlib.sha256(lock.read_bytes() + (ROOT / 'pyproject.toml').read_bytes()
                            + Path(__file__).read_bytes() + str(ROOT).encode()).hexdigest()
    if stamp.is_file() and stamp.read_text() == digest:
        return
    # Constraints keep runtime and test dependencies on the reviewed platform pins.
    # Speech/model packages in the full release lock remain optional here.
    common = [uv, '--no-config', 'pip', 'install', '--python', python, '--constraint', lock]
    run([*common, 'setuptools==84.0.0', 'pytest==9.1.1', 'pytest-asyncio==1.4.0', 'zstandard==0.25.0'])
    run([*common, '--no-build-isolation', '--editable', ROOT])
    run([uv, '--no-config', 'pip', 'check', '--python', python])
    stamp.write_text(digest)


def prepare_components(python, data):
    # Import only after re-entering the exact own environment.
    from studycenter_public.platform.paths import AppPaths, reject_links
    from studycenter_public.platform.components import ComponentService, component_root
    paths = AppPaths.create(data)
    components = reject_links(component_root(paths))
    status = ComponentService(paths).status()
    if status['browser'] and status['ocr']:
        return components
    staging = regular_directory(ROOT / '.local/setup')
    staging.mkdir(parents=True, exist_ok=True)
    platform = 'windows' if os.name == 'nt' else 'linux'
    with tempfile.TemporaryDirectory(prefix='components-', dir=staging) as temporary:
        stage = Path(temporary)
        components.mkdir(parents=True, exist_ok=True)
        if not status['browser']:
            target = reject_links(components / 'browsers')
            if target.exists():
                raise ValueError(f'Incomplete browser directory kept untouched: {target}. Move it aside and retry.')
            original = stage / 'playwright'
            original.mkdir()
            if platform == 'linux':
                environment = os.environ.copy()
                environment['PLAYWRIGHT_BROWSERS_PATH'] = str(original)
                run([python, '-m', 'playwright', 'install', '--only-shell', 'chromium'], env=environment)
            run([python, ROOT / 'scripts/build_browser_runtime.py', '--platform', platform,
                 '--original', original, '--output', stage / 'browser'])
            shutil.copytree(stage / 'browser/runtime', target)
        if not status['ocr']:
            target = reject_links(components / 'ocr')
            if target.exists():
                raise ValueError(f'Incomplete OCR directory kept untouched: {target}. Move it aside and retry.')
            if platform == 'linux' and not all(shutil.which(name) for name in ('apt-get', 'apt-cache', 'dpkg-deb')):
                raise RuntimeError('Portable OCR setup needs Ubuntu 24.04 x64 with apt-get, apt-cache and dpkg-deb. Use the complete Linux release archive on other systems.')
            run([python, ROOT / f'scripts/build_{platform}_ocr.py', '--output', stage / 'ocr'])
            shutil.copytree(stage / 'ocr/runtime', target)
    ComponentService(paths).verify()
    return components


def main(argv=None):
    parser = argparse.ArgumentParser(description='Prepare this clone and start StudyCenter Public.')
    parser.add_argument('--uv', required=True, type=Path)
    parser.add_argument('--setup-only', action='store_true', help='Prepare the environment and portable components without starting the app')
    parser.add_argument('--data-dir', default=os.environ.get('STUDYCENTER_PUBLIC_DATA_DIR', str(ROOT / '.local/data')))
    args, application_args = parser.parse_known_args(argv)
    regular_directory(ROOT / '.local')
    python = environment_python(ROOT)
    if not environment_usable(ROOT, python):
        if (ROOT / '.venv').exists():
            preserve_environment(ROOT)
        run([args.uv, '--no-config', 'venv', '--managed-python', '--python', '3.12.11', ROOT / '.venv'])
        if not environment_usable(ROOT, python):
            raise RuntimeError('The project environment could not be created. No system package installation attempted.')
    dependencies(args.uv, python)
    if Path(sys.prefix).resolve() != (ROOT / '.venv').resolve():
        return subprocess.call([str(python), str(Path(__file__)), *sys.argv[1:]], cwd=ROOT)
    stopping = '--quit' in application_args or '--stop' in application_args
    environment = os.environ.copy()
    if not stopping:
        components = prepare_components(python, args.data_dir)
        if sys.platform == 'linux':
            # desktop.browser restores this for xdg-open in the normal browser.
            environment['LD_LIBRARY_PATH_ORIG'] = environment.get('LD_LIBRARY_PATH', '')
            environment['LD_LIBRARY_PATH'] = str(components / 'ocr/usr/lib/x86_64-linux-gnu')
    if args.setup_only:
        print('Ready. Run start.cmd / sh start.sh to open the app.', flush=True)
        return 0
    application_args = ['--quit' if arg == '--stop' else arg for arg in application_args]
    return subprocess.call([str(python), '-m', 'studycenter_public', '--data-dir', args.data_dir,
                            *application_args], cwd=ROOT, env=environment)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'Developer setup failed: {error}\nCheck the connection and retry the same starter. See docs/DEVELOPMENT.md.', file=sys.stderr)
        raise SystemExit(1)
