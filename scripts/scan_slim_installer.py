"""Extract and scan the actual slim installer with the pinned portable decoder."""
import argparse
from pathlib import Path
import subprocess
import sys
import zipfile

try:
    from . import package_extract_installer as extraction, prepare_slim_assets
except ImportError:
    import package_extract_installer as extraction
    import prepare_slim_assets

ROOT = Path(__file__).resolve().parents[1]
# Author's fixed GitHub release. The older rolling-site pin is retained in
# package_extract_installer.py; the author's current ZIP no longer matches it.
UNPACKER_URL = 'https://github.com/jrathlev/InnoUnpacker-Windows-GUI/releases/download/ui_2_2_13/innounp-2.zip'
UNPACKER_ARCHIVE_SIZE = 620210
UNPACKER_ARCHIVE_SHA = 'f8e45c9676bebc7d7570108ab572673b541fcf7a7d3d24a04319ac6b5315b4e8'
UNPACKER_SHA = 'ab58400460cfda503ec88702dd3d82f6ee207b28e0548c9f8861ef008f85b644'


def scan(installer, bootstrap, output, unpacker=None):
    installer = Path(installer).resolve(strict=True)
    bootstrap = Path(bootstrap).resolve(strict=True)
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Choose a new dedicated installer scan directory')
    output.mkdir(parents=True)
    if unpacker is None:
        download = output/'innounp.zip'
        prepare_slim_assets.fetch(UNPACKER_URL, download, UNPACKER_ARCHIVE_SIZE, UNPACKER_ARCHIVE_SHA)
        with zipfile.ZipFile(download) as archive:
            matches = [member for member in archive.infolist() if member.filename == 'innounp.exe']
            if len(matches) != 1 or matches[0].file_size > 10_000_000:
                raise ValueError('Portable decoder archive differs')
            unpacker = output/'innounp.exe'
            unpacker.write_bytes(archive.read(matches[0]))
    unpacker = Path(unpacker).resolve(strict=True)
    if extraction.digest(unpacker) != UNPACKER_SHA:
        raise ValueError('Portable decoder differs from reviewed upstream binary')
    unpacked = output/'unpacked'
    unpacked.mkdir()
    subprocess.run([str(unpacker), '-x', '-b', '-q', '-a', '-m', '-d'+str(unpacked), str(installer)], check=True, timeout=300)
    application = unpacked/'{app}'
    actual = {path.relative_to(application).as_posix() for path in application.rglob('*') if path.is_file()}
    expected = {path.relative_to(bootstrap).as_posix() for path in bootstrap.rglob('*') if path.is_file()} - {'backup_core.py', 'installer-recovery.ps1'}
    if not expected or actual != expected:
        raise ValueError('Installer application inventory differs from its bootstrap')
    for name in expected:
        if extraction.digest(application/name) != extraction.digest(bootstrap/name):
            raise ValueError('Installer application bytes differ: '+name)
    helper = unpacked/'{tmp}'/'backup_core.py'
    if not helper.is_file() or extraction.digest(helper) != extraction.digest(bootstrap/'backup_core.py'):
        raise ValueError('Installer backup helper differs from reviewed bootstrap')
    recovery = unpacked/'{tmp}'/'installer-recovery.ps1'
    if not recovery.is_file() or extraction.digest(recovery) != extraction.digest(bootstrap/'installer-recovery.ps1'):
        raise ValueError('Installer recovery helper differs from reviewed bootstrap')
    subprocess.run([sys.executable, str(ROOT/'scripts/check_public.py'), '--artifact', str(unpacked)], check=True)
    print('Slim installer contents match bootstrap and passed the unpacked artifact scan.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--installer', required=True, type=Path)
    parser.add_argument('--bootstrap', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--unpacker', type=Path)
    args = parser.parse_args()
    scan(args.installer, args.bootstrap, args.output, args.unpacker)
