"""Internal build step: bind capabilities immediately after native compilation.

There is deliberately no CLI that can bless an arbitrary existing installer.
Only build_slim invokes this after make_bootstrap completed successfully.
"""
import hashlib
import json
from pathlib import Path
import re

from packaging.version import Version


def digest(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def write_update_manifest(directory, version, platform, *, fresh_build=False):
    if not fresh_build:
        raise ValueError('Update capabilities require a freshly completed build pipeline')
    directory = Path(directory)
    if str(Version(version)) != version or platform not in ('windows', 'linux'):
        raise ValueError('Expected normalized release version and supported platform')
    suffix = 'windows-x86_64-setup.exe' if platform == 'windows' else 'linux-x86_64-slim.tar.gz'
    artifact = directory / f'studycenter-public-{version}-{suffix}'
    if artifact.is_symlink() or not artifact.is_file():
        raise ValueError('Build the installer/archive before generating update metadata')
    record = {'format': 1, 'application': 'StudyCenter.Public', 'version': version,
              'platform': platform+'-x86_64', 'artifact': artifact.name,
              'sha256': digest(artifact), 'protocol': 1}
    if platform == 'windows':
        record['installer_update_lock'] = True
    manifest = directory / f'studycenter-public-{version}-{platform}-x86_64-update.json'
    if manifest.is_symlink():
        raise ValueError('Linked metadata output refused')
    manifest.write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8')
    # Keep the other platform's entries when builds share a release directory.
    sums = directory / 'SHA256SUMS'
    if sums.is_symlink():
        raise ValueError('Linked checksum output refused')
    entries = {}
    if sums.exists():
        for line in sums.read_text(encoding='utf-8').splitlines():
            match = re.fullmatch(r'([0-9a-fA-F]{64}) [ *]([^/\\\r\n]+)', line)
            if not match:
                raise ValueError('Invalid existing release checksum list')
            entries[match[2]] = match[1].lower()
    entries.update({artifact.name: record['sha256'], manifest.name: digest(manifest)})
    sums.write_text(''.join(f'{value}  {name}\n' for name, value in sorted(entries.items())), encoding='utf-8')
    return manifest
