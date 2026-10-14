"""Build only the Linux desktop helper in an explicit own staging directory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def exclusive_write(path, data, mode):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as output:
        os.fchmod(output.fileno(), mode)
        output.write(data)


def prepare(output, compiler='cc'):
    if sys.platform != 'linux':
        raise RuntimeError('Linux desktop helper requires an existing Linux compiler')
    executable = shutil.which(compiler)
    if not executable:
        raise RuntimeError('Existing Linux C compiler unavailable; no installation attempted')
    output = Path(output).absolute()
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError('Desktop staging path contains a symlink')
    output.mkdir(parents=True, exist_ok=True)
    names = ('desktop-install', 'install-desktop.sh', 'DESKTOP-INSTALL-RECIPE.json')
    if any(os.path.lexists(output / name) for name in names):
        raise FileExistsError('Desktop staging outputs must be new; existing files stay untouched')
    source = ROOT / 'packaging/linux/desktop-install.c'
    wrapper = ROOT / 'packaging/linux/install-desktop.sh'
    version = subprocess.run([executable, '--version'], capture_output=True, text=True,
                             check=True, timeout=10).stdout.splitlines()[0]
    environment = os.environ.copy()
    environment.update(SOURCE_DATE_EPOCH='0', LC_ALL='C')
    flags = ['-std=c11', '-O2', '-Wall', '-Wextra', '-Werror']
    maps = ['-ffile-prefix-map=', '-fdebug-prefix-map=', '-fmacro-prefix-map=']
    with tempfile.TemporaryDirectory(prefix='desktop-build-', dir=output) as temporary:
        environment['TMPDIR'] = temporary
        helper = Path(temporary) / 'desktop-install'
        subprocess.run([executable, *flags, *(prefix + str(ROOT) + '=.' for prefix in maps),
                        str(source), '-o', str(helper)], env=environment, capture_output=True,
                       check=True, timeout=30)
        helper_bytes = helper.read_bytes()
    wrapper_bytes = wrapper.read_text(encoding='utf-8').encode('utf-8')
    receipt = {
        'format_version': 1,
        'scope': 'Separate Linux desktop packaging step; frozen application runtime unchanged',
        'compiler': {'name': Path(executable).name, 'version': version,
                     'sha256': sha(Path(executable).resolve().read_bytes())},
        'flags': flags + [prefix + '<source-root>=.' for prefix in maps],
        'sources': {
            'desktop-install.c': {'sha256': sha(source.read_bytes())},
            'install-desktop.sh': {'sha256': sha(wrapper.read_bytes())},
            'package_linux_desktop.py': {'sha256': sha(Path(__file__).read_bytes())},
        },
        'outputs': {name: {'sha256': sha(content), 'size': len(content)}
                    for name, content in [('desktop-install', helper_bytes), ('install-desktop.sh', wrapper_bytes)]},
        'wrapper_normalization': 'UTF-8 source with LF line endings',
        'environment': {'SOURCE_DATE_EPOCH': '0', 'LC_ALL': 'C', 'TMPDIR': '<own-stage-temporary>'},
    }
    exclusive_write(output / 'desktop-install', helper_bytes, 0o755)
    exclusive_write(output / 'install-desktop.sh', wrapper_bytes, 0o755)
    exclusive_write(output / 'DESKTOP-INSTALL-RECIPE.json',
                    (json.dumps(receipt, indent=2) + '\n').encode('utf-8'), 0o644)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--compiler', default='cc')
    arguments = parser.parse_args()
    print(json.dumps(prepare(arguments.output, arguments.compiler)))
