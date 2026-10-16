"""Thin release assets from the application wheel and verified beta.2 components.

No installers or application processes are executed. Existing native binaries,
notices and corresponding-source offers are retained byte for byte.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://github.com/Saltybukket/StudyCenter-Public/releases/download/v0.1.0-beta.3/'
OLD_SOURCES = 'https://github.com/Saltybukket/StudyCenter-Public/releases/download/v0.1.0-beta.2/StudyCenter-Public-corresponding-sources-0.1.0b1-all-platform-notices.zip'
BETA2_LINUX_SHA = '10a01f2638092d51e2b9e07ad9167ecbad72df836fb9c9a39f6b766429642bbb'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def record(path):
    path = Path(path)
    return dict(filename=path.name, url=BASE + path.name, sha256=digest(path), size=path.stat().st_size)


def safe_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
        raise ValueError('Unsafe archive member')
    return path


def verified_files(bundle):
    bundle = Path(bundle)
    manifest = json.loads((bundle/'BUILD-MANIFEST.json').read_text())
    if manifest.get('version') != '0.1.0b2' or manifest.get('source_git_sha') != '62b235eedae220823229e10823d933b6718ccf63':
        raise ValueError('Use the verified, immutable beta.2 component source')
    files = {}
    for relative, expected in manifest['files'].items():
        if not relative.startswith(('components/', 'licenses/')):
            continue
        path = bundle/Path(*safe_name(relative).parts)
        if path.is_symlink() or not path.is_file() or path.stat().st_size != expected['size'] or digest(path) != expected['sha256']:
            raise ValueError('Component source bytes differ: ' + relative)
        files[relative] = path
    return files


def unpack_linux(archive, target):
    if digest(archive) != BETA2_LINUX_SHA:
        raise ValueError('Linux beta.2 archive checksum mismatch')
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, 'r:gz') as source:
        for member in source:
            name = safe_name(member.name)
            if not name.parts or name.parts[0] != 'StudyCenter-Public':
                raise ValueError('Unexpected Linux archive root')
            relative = PurePosixPath(*name.parts[1:])
            if not str(relative).startswith(('components/', 'licenses/')) and str(relative) != 'BUILD-MANIFEST.json':
                continue
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError('Component archives must contain regular files')
            destination = target/Path(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(member) as incoming, destination.open('wb') as output:
                shutil.copyfileobj(incoming, output)
            destination.chmod(member.mode & 0o777)
    return target


def components(bundle, platform, output, version):
    files = verified_files(bundle)
    result = {}
    for name, directory in [('browser', 'browsers'), ('ocr', 'ocr')]:
        target = output/f'studycenter-public-{version}-{platform}-x86_64-{name}.tar.gz'
        selected = {}
        prefix = f'components/{directory}/'
        for relative, path in files.items():
            if relative.startswith(prefix):
                selected['runtime/' + relative[len(prefix):]] = path
            elif relative.startswith('licenses/'):
                selected['notices/' + relative] = path
            # Browser libraries were coupled to OCR in the full bundle. Preserve
            # their exact bytes here so choosing the browser never pulls OCR.
            elif platform == 'linux' and name == 'browser' and relative.startswith('components/ocr/usr/lib/x86_64-linux-gnu/'):
                selected['runtime/host-libraries/' + path.name] = path
        with tarfile.open(target, 'w:gz', compresslevel=6) as archive:
            for relative, path in sorted(selected.items()):
                info = archive.gettarinfo(str(path), arcname=relative)
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                info.mtime = 0
                with path.open('rb') as incoming:
                    archive.addfile(info, incoming)
            notice = ('Exact native files and notices from StudyCenter Public beta.2.\n'
                      'Corresponding sources for unchanged copyleft components:\n' + OLD_SOURCES + '\n')
            import io
            data = notice.encode()
            info = tarfile.TarInfo('notices/CORRESPONDING-SOURCES.txt')
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        result[name] = {'archive': record(target), 'installed_bytes': sum(p.stat().st_size for p in selected.values())}
        if name == 'browser':
            result[name]['extra'] = 'browser'
        print(json.dumps({'component': name, 'platform': platform, **result[name]}), flush=True)
    result['speech'] = {'extra': 'speech', 'models_separate': True}
    return result


def speech_notices(bundle, platform, output, version):
    files = verified_files(bundle)
    target = output/f'studycenter-public-{version}-{platform}-x86_64-speech-notices.tar.gz'
    with tarfile.open(target, 'w:gz', compresslevel=6) as archive:
        for relative, path in sorted(files.items()):
            if not relative.startswith('licenses/'):
                continue
            info = archive.gettarinfo(str(path), arcname='notices/'+relative)
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mtime = 0
            with path.open('rb') as incoming:
                archive.addfile(info, incoming)
        import io
        data = ('Unchanged beta.2 speech libraries; model weights are separate.\nCorresponding sources:\n'+OLD_SOURCES+'\n').encode()
        for name in ('runtime/CORRESPONDING-SOURCES.txt', 'notices/CORRESPONDING-SOURCES.txt'):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return {'extra': 'speech', 'models_separate': True, 'archive': record(target)}


def make_bootstrap(platform, manifest, output, version, iscc=None, runtime_requirements=None):
    stage = output/f'{platform}-bootstrap'
    stage.mkdir(exist_ok=True)
    for name in ('start.cmd', 'start.sh', 'LICENSE', 'README.md'):
        shutil.copy2(ROOT/name, stage/name)
    shutil.copy2(ROOT/'docs/QUICKSTART.md', stage/'README.md')
    shutil.copy2(ROOT/'src/studycenter_public/platform/setup.py', stage/'setup.py')
    (stage/'slim-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    if platform == 'windows':
        shutil.copy2(ROOT/'packaging/windows/launch.vbs', stage/'launch.vbs')
        if not runtime_requirements:
            raise ValueError('Existing verified Windows runtime requirements are required')
        shutil.copy2(runtime_requirements, stage/'SYSTEM-RUNTIME-REQUIREMENTS.iss')
        if iscc:
            subprocess.run([str(iscc), '/DAppVersion='+version, '/DBundleDir='+str(stage), '/DOutputDir='+str(output), str(ROOT/'packaging/windows/slim.iss')], check=True)
    else:
        target = output/f'studycenter-public-{version}-linux-x86_64-slim.tar.gz'
        with tarfile.open(target, 'w:gz') as archive:
            for path in sorted(stage.iterdir()):
                if path.suffix == '.cmd':
                    continue
                info = archive.gettarinfo(str(path), arcname='StudyCenter-Public/'+path.name)
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                info.mtime = 0
                if path.suffix == '.sh':
                    info.mode = 0o755
                with path.open('rb') as incoming:
                    archive.addfile(info, incoming)
    return stage


def slim_artifact_reviews(output):
    original = json.loads((ROOT/'scripts/artifact_reviews.json').read_text())
    result = {}
    for name, records in original.items():
        destinations = []
        if name.startswith('licenses/'):
            destinations.append('notices/'+name)
        for directory in ('browsers', 'ocr'):
            prefix = 'components/'+directory+'/'
            if name.startswith(prefix):
                destinations.append('runtime/'+name[len(prefix):])
        prefix = 'components/ocr/usr/lib/x86_64-linux-gnu/'
        if name.startswith(prefix):
            destinations.append('runtime/host-libraries/'+name[len(prefix):])
        for destination in destinations:
            result.setdefault(destination, []).extend(records if isinstance(records, list) else [records])
    (output/'artifact-reviews.json').write_text(json.dumps(result, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--windows-bundle', type=Path)
    parser.add_argument('--linux-archive', type=Path)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT/'dist/slim')
    parser.add_argument('--iscc', type=Path)
    parser.add_argument('--reuse-components', action='store_true')
    parser.add_argument('--proxy-wheel', type=Path)
    parser.add_argument('--source-commit')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    slim_artifact_reviews(output)
    version = tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
    wheel = output/args.wheel.name
    if args.wheel.resolve() != wheel:
        shutil.copy2(args.wheel, wheel)
    for platform, bundle in [('windows', args.windows_bundle), ('linux', None)]:
        cached = output/(platform+'-components.json')
        if args.reuse_components and cached.is_file():
            optional = json.loads(cached.read_text())
            for definition in optional.values():
                if definition.get('archive') and record(output/definition['archive']['filename']) != definition['archive']:
                    raise ValueError('Cached component archive changed')
        else:
            if platform == 'linux':
                if not args.linux_archive:
                    continue
                bundle = unpack_linux(args.linux_archive, ROOT/'build/beta3-linux-components')
            if not bundle:
                continue
            optional = components(bundle, platform, output, version)
            cached.write_text(json.dumps(optional, indent=2))
        lock = output/f'requirements-{platform}.lock'
        notice_bundle = args.windows_bundle if platform == 'windows' else ROOT/'build/beta3-linux-components'
        if not optional.get('speech', {}).get('archive'):
            optional['speech'] = speech_notices(notice_bundle, platform, output, version)
            cached.write_text(json.dumps(optional, indent=2))
        shutil.copy2(ROOT/lock.name, lock)
        manifest = {'schema': 1, 'version': version, 'platform': platform+'-x86_64', 'python': '3.12',
                    'wheel': record(wheel), 'lock': record(lock), 'components': optional,
                    'corresponding_sources': OLD_SOURCES}
        if platform == 'windows':
            proxy = args.proxy_wheel or output/'proxy_tools-0.1.0-py3-none-any.whl'
            if not proxy.is_file():
                raise ValueError('Build the audited pure-Python proxy-tools wheel first (packaging/slim-proxy-tools.json)')
            if proxy.resolve() != output/proxy.name:
                shutil.copy2(proxy, output/proxy.name)
            manifest['extra_wheels'] = [record(output/proxy.name)]
        measurements = ROOT/'reports-local/beta3/python-wheel-measurements.json'
        if measurements.is_file():
            measured = json.loads(measurements.read_text())['manifest_values'][platform]
            # Size facts are filled after actual wheel retrieval, never guessed.
            manifest['base_python_download_bytes'] = measured['base_python_download_bytes']
            for name, sizes in measured['components'].items():
                manifest['components'][name].update(sizes)
        manifest_path = ROOT/'packaging'/('slim-'+platform+'-x86_64.json')
        manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
        release_manifest = dict(manifest)
        if args.source_commit:
            actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
            if args.source_commit != actual:
                raise ValueError('Release source must match the current committed checkpoint')
            release_manifest['source_git_sha'] = actual
        (output/manifest_path.name).write_text(json.dumps(release_manifest, indent=2)+'\n')
        runtime = args.windows_bundle/'SYSTEM-RUNTIME-REQUIREMENTS.iss' if args.windows_bundle else None
        make_bootstrap(platform, release_manifest, output, version, args.iscc, runtime)


if __name__ == '__main__':
    main()
