"""Fetch immutable, previously reviewed slim components for a CI candidate."""
import argparse
import json
from pathlib import Path
import urllib.request

try:
    from . import build_slim
except ImportError:
    import build_slim

ROOT = Path(__file__).resolve().parents[1]
PROXY_NAME = 'proxy_tools-0.1.0-py3-none-any.whl'
PREVIOUS = {
    'studycenter_public-0.1.0b5-py3-none-any.whl': (985834, 'd01ab63cdef8997cdc4565ed84a7597bfcbbdaa70a2ad1d9bcfd938a0782e77c'),
    'requirements-windows.lock': (1979, '61125a06d66813a14349cdcc8bba8b1b6aeb3e775b5055263b9f9fe89c524ffe'),
    'requirements-linux.lock': (1626, 'c2e19ac3f20e9174f63421eb1d5f6d1fce831598a1cb3c7e12f1418a62ad4c49'),
    'studycenter-public-0.1.0b5-windows-x86_64-setup.exe': (2153737, '3aee438e61c3e132b8fc57330bd96fddaead4f4e2460fbaeb3a668490890c77b'),
    'studycenter-public-0.1.0b5-linux-x86_64-slim.tar.gz': (24690, '04c906ff3ddaa8d1eabf56766802c346b9dd01ec1eef752ef7864c04f8ee6c5d'),
    PROXY_NAME: (3928, build_slim.PROXY_WHEEL_SHA),
}


def fetch(url, target, size, sha256):
    target = Path(target)
    if target.is_file() and target.stat().st_size == size and build_slim.digest(target) == sha256:
        return
    temporary = target.with_name(target.name + '.partial')
    try:
        with urllib.request.urlopen(url, timeout=120) as incoming, temporary.open('wb') as output:
            total = 0
            while chunk := incoming.read(1024 * 1024):
                total += len(chunk)
                if total > size:
                    raise ValueError('Asset exceeds its pinned size')
                output.write(chunk)
        if total != size or build_slim.digest(temporary) != sha256:
            raise ValueError('Downloaded asset differs from its pinned size or SHA256')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def prepare(platform, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    old = json.loads((ROOT/'packaging'/f'slim-{platform}-x86_64.json').read_text(encoding='utf-8'))
    components = old['components']
    # Validate the manifest identity before issuing any network request.
    for name, expected in build_slim.BETA3_SHA256[platform].items():
        archive = components[name]['archive']
        suffix = 'speech-notices' if name == 'speech' else name
        filename = f'studycenter-public-0.1.0b3-{platform}-x86_64-{suffix}.tar.gz'
        if (archive['filename'] != filename or archive['sha256'] != expected or
                archive['url'] != build_slim.ORIGIN+'v0.1.0-beta.3/'+filename or
                type(archive['size']) is not int or not 0 < archive['size'] < 2_000_000_000):
            raise ValueError('Unreviewed component identity')
        fetch(archive['url'], output/filename, archive['size'], expected)
    build_slim.verify_cached_components(components, platform, output)
    (output/f'{platform}-components.json').write_text(json.dumps(components, indent=2)+'\n', encoding='utf-8')
    if platform == 'windows':
        proxy = old['extra_wheels'][0]
        expected_url = build_slim.ORIGIN+'v0.1.0-beta.4/'+PROXY_NAME
        if (proxy['filename'] != PROXY_NAME or proxy['url'] != expected_url or
                proxy['sha256'] != build_slim.PROXY_WHEEL_SHA or proxy['size'] != 3928):
            raise ValueError('Unreviewed proxy wheel identity')
        fetch(expected_url, output/PROXY_NAME, proxy['size'], build_slim.PROXY_WHEEL_SHA)


def prepare_previous(platform, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    suffix = 'windows-x86_64-setup.exe' if platform == 'windows' else 'linux-x86_64-slim.tar.gz'
    names = [f'studycenter-public-0.1.0b5-{suffix}', 'studycenter_public-0.1.0b5-py3-none-any.whl',
             f'requirements-{platform}.lock']
    if platform == 'windows':
        names.append(PROXY_NAME)
    for name in names:
        size, digest = PREVIOUS[name]
        fetch(build_slim.ORIGIN+'v0.1.0-beta.5/'+name, output/name, size, digest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--platform', required=True, choices=('windows', 'linux'))
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--previous-output', type=Path)
    args = parser.parse_args()
    prepare(args.platform, args.output)
    if args.previous_output:
        prepare_previous(args.platform, args.previous_output)
