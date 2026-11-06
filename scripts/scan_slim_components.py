"""Unpack only verified immutable component archives and scan their files."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tarfile

try:
    from . import build_slim
except ImportError:
    import build_slim

ROOT = Path(__file__).resolve().parents[1]


def scan(platform, assets, output):
    assets = Path(assets).resolve(strict=True)
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Choose a new dedicated component scan directory')
    records = json.loads((assets/f'{platform}-components.json').read_text(encoding='utf-8'))
    build_slim.verify_cached_components(records, platform, assets)
    output.mkdir(parents=True)
    for name in ('browser', 'ocr', 'speech'):
        target = output/name
        target.mkdir()
        with tarfile.open(assets/records[name]['archive']['filename'], 'r:gz') as archive:
            members = archive.getmembers()
            if len(members) > 100000 or sum(member.size for member in members) > 8 * 1024**3:
                raise ValueError('Component archive exceeds extraction bounds')
            if any(not (member.isfile() or member.isdir()) for member in members):
                raise ValueError('Component archive contains unsupported entries')
            archive.extractall(target, members=members, filter='data')
        subprocess.run([sys.executable, str(ROOT/'scripts/check_public.py'), '--artifact', str(target),
                        '--artifact-reviews', str(assets/'artifact-reviews.json')], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--platform', required=True, choices=('windows', 'linux'))
    parser.add_argument('--assets', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    scan(args.platform, args.assets, args.output)
