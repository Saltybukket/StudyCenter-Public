"""Thin release assets from the application wheel and verified beta.2 components.

No installers or application processes are executed. Existing native binaries,
notices and corresponding-source offers are retained byte for byte.
"""
import argparse
import hashlib
import io
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'https://github.com/Saltybukket/StudyCenter-Public/releases/download/'
BASE = None
BETA3_SHA256 = {
    'windows': {'browser': '1e62cae8a64eaad14c79453cfc97d2fbda508c58fc1d0a2e8821aeb9c723cbd5', 'ocr': '460cf67e76cc6922303f1e74b4ecf47e33a743a84431838e94077c8d1029288f', 'speech': '92ee71f1e7842f643738e20d6a5ae814256d922bceeb7e4f07f5913b2170e358'},
    'linux': {'browser': 'ad29088be7c8029ffc467b5f50b484e1de27fafd4416b4274338e0ebf8c84d47', 'ocr': 'f648d985f6b7d819bf4a90a810dce55f5460d6bd663fc677e8eec952ef70831b', 'speech': 'effdb5bbb482176bf6676692a591bb5a03925a8b5049be799111b3d8aa84a886'},
}
OLD_SOURCES = 'https://github.com/Saltybukket/StudyCenter-Public/releases/download/v0.1.0-beta.2/StudyCenter-Public-corresponding-sources-0.1.0b1-all-platform-notices.zip'
BETA2_LINUX_SHA = '10a01f2638092d51e2b9e07ad9167ecbad72df836fb9c9a39f6b766429642bbb'
# Generated project text from beta.2 source 62b235eedae220823229e10823d933b6718ccf63,
# verified in installer 00a4f6c8452af411179f4664400324e5d2c4a1c183b4e93aee11ca3695d1990a.
# It is also identical to the beta.3 copy; project license is AGPL-3.0-only.
RUNTIME_REQUIREMENTS_SHA = '188a1d7149565492460b8450576b0fbd7cb6ae079c477a7a15726cc41775eef7'
PROXY_WHEEL_SHA = 'e230f9c9936e3dfc56d00b20000043f530010455bfad63cee56fef8b80288aa0'
BUILD_INPUTS = ('src/studycenter_public', 'packaging', 'scripts/build_slim.py',
                'scripts/prepare_slim_assets.py', 'scripts/artifact_reviews.json',
                'start.cmd', 'start.sh', 'LICENSE', 'README.md', 'docs/QUICKSTART.md',
                'pyproject.toml', 'requirements-windows.lock', 'requirements-linux.lock')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def record(path):
    path = Path(path)
    return dict(filename=path.name, url=BASE + path.name, sha256=digest(path), size=path.stat().st_size)


def release_version(tag):
    match = re.fullmatch(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-beta\.([1-9][0-9]*)', tag)
    if not match:
        raise ValueError('Release tag must be vMAJOR.MINOR.PATCH-beta.N')
    return '.'.join(match.groups()[:3]) + 'b' + match.group(4)


def verify_checkout(source_commit):
    if not re.fullmatch(r'[0-9a-f]{40}', source_commit):
        raise ValueError('Release source must be a full immutable Git SHA')
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if source_commit != actual:
        raise ValueError('Release source must match the current committed checkpoint')
    changed = subprocess.check_output(['git', 'diff', '--name-only', '-z', source_commit, '--', *BUILD_INPUTS], cwd=ROOT)
    untracked = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z', '--', *BUILD_INPUTS], cwd=ROOT)
    if changed or untracked:
        raise ValueError('Release build inputs differ from the committed checkpoint')


def verify_source_wheel(wheel, source_commit):
    """Bind every shipped application byte to the requested Git tree."""
    archive = subprocess.check_output(['git', 'archive', '--format=tar', source_commit,
                                       'src/studycenter_public'], cwd=ROOT)
    expected = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as tree:
        for member in tree:
            if member.isfile():
                name = member.name.removeprefix('src/')
                expected[name] = tree.extractfile(member).read()
            elif not member.isdir():
                raise ValueError('Source tree contains an unsupported package entry')
    with zipfile.ZipFile(wheel) as package:
        names = [entry.filename for entry in package.infolist()]
        if len(names) != len(set(names)):
            raise ValueError('Wheel contains duplicate archive entries')
        actual = {entry.filename: package.read(entry) for entry in package.infolist()
                  if entry.filename.startswith('studycenter_public/') and not entry.is_dir()}
    if not expected or expected != actual:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        changed = sorted(name for name in expected.keys() & actual.keys() if expected[name] != actual[name])
        raise ValueError(f'Wheel differs from source commit: missing={missing}, extra={extra}, changed={changed}')


def verify_cached_components(optional, platform, output):
    if set(optional) != {'browser', 'ocr', 'speech'}:
        raise ValueError('Incomplete cached components')
    for name, expected_hash in BETA3_SHA256[platform].items():
        archive = optional[name]['archive']
        suffix = 'speech-notices' if name == 'speech' else name
        filename = f'studycenter-public-0.1.0b3-{platform}-x86_64-{suffix}.tar.gz'
        if (archive.get('filename') != filename or
                archive.get('url') != ORIGIN + 'v0.1.0-beta.3/' + filename or
                archive.get('sha256') != expected_hash):
            raise ValueError('Cached component identity or origin differs')
        path = output / filename
        if not path.is_file() or path.stat().st_size != archive.get('size') or digest(path) != expected_hash:
            raise ValueError('Cached component archive changed')
    return optional


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
    shutil.copy2(ROOT/'packaging/backup_core.py', stage/'backup_core.py')
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
    parser.add_argument('--release-tag', required=True, help='Explicit release tag matching the wheel and project version')
    parser.add_argument('--output', type=Path, default=ROOT/'dist/slim')
    parser.add_argument('--iscc', type=Path)
    parser.add_argument('--reuse-components', action='store_true')
    parser.add_argument('--proxy-wheel', type=Path)
    parser.add_argument('--source-commit')
    parser.add_argument('--platform', choices=('windows', 'linux'), help='Build one native platform')
    parser.add_argument('--runtime-requirements', type=Path, default=ROOT/'packaging/windows/slim-system-runtime-requirements.iss')
    args = parser.parse_args()
    global BASE
    version = tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
    if release_version(args.release_tag) != version:
        raise ValueError('Release tag and project version differ')
    if args.wheel.name != f'studycenter_public-{version}-py3-none-any.whl':
        raise ValueError('Wheel filename and project version differ')
    with zipfile.ZipFile(args.wheel) as archive:
        metadata = f'studycenter_public-{version}.dist-info/METADATA'
        from email.parser import BytesParser
        fields = BytesParser().parsebytes(archive.read(metadata), headersonly=True)
        if fields.get('Name') != 'studycenter-public' or fields.get('Version') != version:
            raise ValueError('Wheel metadata and project version differ')
    if args.source_commit:
        verify_checkout(args.source_commit)
        verify_source_wheel(args.wheel, args.source_commit)
    BASE = ORIGIN + args.release_tag + '/'
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    slim_artifact_reviews(output)
    wheel = output/args.wheel.name
    if args.wheel.resolve() != wheel:
        shutil.copy2(args.wheel, wheel)
    for platform, bundle in [('windows', args.windows_bundle), ('linux', None)]:
        if args.platform and args.platform != platform:
            continue
        if platform == 'windows' and args.source_commit and not args.iscc:
            raise ValueError('A source-bound Windows build requires the Inno compiler and an installer')
        cached = output/(platform+'-components.json')
        if args.reuse_components and cached.is_file():
            optional = verify_cached_components(json.loads(cached.read_text()), platform, output)
        else:
            if args.reuse_components:
                raise ValueError('Missing cached component manifest: ' + str(cached))
            if platform == 'linux':
                if not args.linux_archive:
                    continue
                bundle = unpack_linux(args.linux_archive, ROOT/'build/beta3-linux-components')
            if not bundle:
                continue
            optional = components(bundle, platform, output, version)
            cached.write_text(json.dumps(optional, indent=2))
        # Download estimates belonged to an older dependency lock, not this build.
        for definition in optional.values():
            definition.pop('python_download_bytes', None)
        lock = output/f'requirements-{platform}.lock'
        notice_bundle = args.windows_bundle if platform == 'windows' else ROOT/'build/beta3-linux-components'
        if not optional.get('speech', {}).get('archive'):
            optional['speech'] = speech_notices(notice_bundle, platform, output, version)
            cached.write_text(json.dumps(optional, indent=2))
        shutil.copy2(ROOT/lock.name, lock)
        provenance = json.loads((ROOT/'src/studycenter_public/resources/sherpa-provenance.json').read_text(encoding='utf-8'))
        optional['speech']['verified_wheels'] = provenance['wheels'][platform]
        manifest = {'schema': 1, 'version': version, 'platform': platform+'-x86_64', 'python': '3.12',
                    'wheel': record(wheel), 'lock': record(lock), 'components': optional,
                    'corresponding_sources': OLD_SOURCES}
        if platform == 'windows':
            proxy = args.proxy_wheel or output/'proxy_tools-0.1.0-py3-none-any.whl'
            if not proxy.is_file():
                raise ValueError('Build the audited pure-Python proxy-tools wheel first (packaging/slim-proxy-tools.json)')
            if proxy.name != 'proxy_tools-0.1.0-py3-none-any.whl' or digest(proxy) != PROXY_WHEEL_SHA:
                raise ValueError('Proxy wheel differs from the reviewed beta.4 asset')
            if proxy.resolve() != output/proxy.name:
                shutil.copy2(proxy, output/proxy.name)
            manifest['extra_wheels'] = [record(output/proxy.name)]
        # Beta.3 Python size measurements omit new beta.5 dependencies.
        # Leave estimates unknown until measured for this exact candidate.
        manifest_path = output/('slim-'+platform+'-x86_64.json')
        release_manifest = dict(manifest)
        if args.source_commit:
            actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
            if args.source_commit != actual:
                raise ValueError('Release source must match the current committed checkpoint')
            release_manifest['source_git_sha'] = actual
        manifest_path.write_text(json.dumps(release_manifest, indent=2)+'\n')
        runtime = args.windows_bundle/'SYSTEM-RUNTIME-REQUIREMENTS.iss' if args.windows_bundle else args.runtime_requirements
        if platform == 'windows' and (not runtime.is_file() or digest(runtime) != RUNTIME_REQUIREMENTS_SHA):
            raise ValueError('Windows system runtime requirements differ from the reviewed beta.2 installer')
        make_bootstrap(platform, release_manifest, output, version, args.iscc, runtime)
    if args.source_commit:
        verify_checkout(args.source_commit)


if __name__ == '__main__':
    main()
