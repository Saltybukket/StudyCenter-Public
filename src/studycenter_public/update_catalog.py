"""Explicit, bounded release discovery and checksum-verified staging."""
import asyncio
import copy
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import sys
from urllib.parse import quote, urljoin, urlsplit
from uuid import uuid4

import httpx
from packaging.version import InvalidVersion, Version

from . import __version__
from .platform.paths import atomic_json, owned_path, private_directory, reject_links

REPOSITORY = 'Saltybukket/StudyCenter-Public'
API_URL = f'https://api.github.com/repos/{REPOSITORY}/releases?per_page=100'
RELEASE_ROOT = f'https://github.com/{REPOSITORY}/releases'
MAX_METADATA = 2 * 1024 * 1024
MAX_CHECKSUMS = 256 * 1024
MAX_CAPABILITY = 16 * 1024
MAX_ARTIFACT = 1024 * 1024 * 1024


class UpdateError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _version(value):
    if not isinstance(value, str) or len(value) > 80 or not re.fullmatch(r'[A-Za-z0-9.!+_-]+', value):
        raise UpdateError('version')
    try:
        return Version(value.removeprefix('v'))
    except InvalidVersion:
        raise UpdateError('version') from None


def _system(name):
    if name in ('windows-x86_64', 'linux-x86_64'):
        return name
    system = (name or sys.platform).lower()
    machine = platform.machine().lower()
    if machine not in ('x86_64', 'amd64'):
        return 'unsupported'
    if system in ('win32', 'windows'):
        return 'windows-x86_64'
    if system.startswith('linux'):
        return 'linux-x86_64'
    return 'unsupported'


def _asset_name(version, system):
    suffix = {'windows-x86_64': 'windows-x86_64-setup.exe',
              'linux-x86_64': 'linux-x86_64-slim.tar.gz'}.get(system)
    if not suffix:
        raise UpdateError('unsupported_platform')
    return f'studycenter-public-{version}-{suffix}'


def _capability_name(version, system):
    _asset_name(version, system)
    return f'studycenter-public-{version}-{system}-update.json'


def _release_url(tag):
    return f'{RELEASE_ROOT}/tag/{quote(tag, safe="")}'


def _download_url(tag, name):
    return f'{RELEASE_ROOT}/download/{quote(tag, safe="")}/{quote(name, safe="")}'


def _valid_redirect(url):
    parts = urlsplit(url)
    try:
        return (parts.scheme == 'https' and parts.hostname == 'release-assets.githubusercontent.com'
                and parts.port in (None, 443) and not parts.username and not parts.password
                and not parts.fragment and parts.path.startswith('/'))
    except ValueError:
        return False


class UpdateCatalog:
    def __init__(self, paths, current_version=None, platform_name=None, client=None):
        self.paths = paths
        if current_version is None:
            try:
                current_version = metadata.version('studycenter-public')
            except metadata.PackageNotFoundError:
                current_version = __version__
        _version(current_version)
        self.current_version = current_version
        self.platform = _system(platform_name)
        self.client = client
        self._lock = asyncio.Lock()
        self._file = owned_path(Path(paths.config) / 'updates.json', paths.config)
        self._root = owned_path(Path(paths.cache) / 'updates', paths.cache)
        self._data = {'auto_check': False, 'last_checked': None, 'skipped_version': None,
                      'release': None, 'ready': None}
        try:
            cached = json.loads(reject_links(self._file).read_text(encoding='utf-8'))
            if isinstance(cached, dict):
                self._data.update({k: cached[k] for k in self._data if k in cached})
            if type(self._data['auto_check']) is not bool:
                self._data['auto_check'] = False
            self._validated_release(self._data['release']) if self._data['release'] else None
        except (OSError, ValueError, UpdateError, TypeError, json.JSONDecodeError):
            self._data = {'auto_check': False, 'last_checked': None, 'skipped_version': None,
                          'release': None, 'ready': None}
        self._state = 'idle'
        self._error = None
        release = self._data.get('release')
        if release and self._data.get('last_checked'):
            self._state = ('available' if _version(release['version']) > _version(self.current_version) else 'current')
            ready = self._data.get('ready')
            if (self._state == 'available' and isinstance(ready, dict)
                    and ready.get('version') == release['version']
                    and ready.get('platform') == self.platform
                    and ready.get('filename') == _asset_name(release['version'], self.platform)
                    and re.fullmatch(r'[0-9a-f]{64}', str(ready.get('sha256', '')))):
                try:
                    if owned_path(self._root / ready['filename'], self.paths.cache).is_file():
                        self._state = 'ready'
                except (OSError, ValueError):
                    pass

    def _save(self):
        private_directory(Path(self.paths.config))
        atomic_json(owned_path(self._file, self.paths.config), self._data)

    def status(self):
        release = self._data.get('release')
        public = {key: release[key] for key in ('version', 'tag', 'notes', 'url')} if release else None
        return {'current_version': self.current_version, 'platform': self.platform,
                'supported': self.platform != 'unsupported', 'auto_check': self._data['auto_check'],
                'state': self._state, 'release': copy.deepcopy(public),
                'last_checked': self._data['last_checked'], 'skipped_version': self._data['skipped_version'],
                'error': self._error,
                'installer_compatible': self._state == 'ready' and self._compatible_record()}

    def _compatible_record(self):
        record = self._data.get('ready')
        release = self._data.get('release')
        if not isinstance(record, dict) or not isinstance(release, dict):
            return False
        if (not release.get('capability_url') or record.get('version') != release.get('version')
                or record.get('tag') != release.get('tag') or record.get('platform') != self.platform
                or type(record.get('update_protocol')) is not int or record['update_protocol'] != 1):
            return False
        return self.platform != 'windows-x86_64' or record.get('installer_update_lock') is True

    def set_preferences(self, auto_check):
        if type(auto_check) is not bool:
            raise UpdateError('preferences')
        self._data['auto_check'] = auto_check
        self._save()
        return self.status()

    def skip(self, version):
        release = self._validated_release(self._data.get('release'))
        if version != release['version']:
            raise UpdateError('version')
        self._data['skipped_version'] = version
        self._save()
        return self.status()

    def _validated_release(self, release):
        if not isinstance(release, dict):
            raise UpdateError('release')
        tag, version = release.get('tag'), release.get('version')
        _version(version)
        if not isinstance(tag, str) or _version(tag) != _version(version) or version != str(_version(version)):
            raise UpdateError('release')
        filename = _asset_name(version, self.platform)
        if (release.get('url') != _release_url(tag)
                or release.get('artifact_url') != _download_url(tag, filename)
                or release.get('checksums_url') != _download_url(tag, 'SHA256SUMS')
                or not isinstance(release.get('notes'), str) or len(release['notes']) > 100000):
            raise UpdateError('release')
        capability_url = release.get('capability_url')
        if capability_url is not None and capability_url != _download_url(tag, _capability_name(version, self.platform)):
            raise UpdateError('release')
        return release

    async def _fetch(self, client, url, limit, sink=None):
        redirects = 0
        while True:
            async with client.stream('GET', url, follow_redirects=False) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    target = urljoin(url, response.headers.get('location', ''))
                    if not _valid_redirect(target) or redirects >= 3:
                        raise UpdateError('redirect')
                    url = target
                    redirects += 1
                    continue
                if response.status_code != 200:
                    raise UpdateError('network')
                size = 0
                output = bytearray() if sink is None else None
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise UpdateError('size')
                    if sink is None:
                        output.extend(chunk)
                    else:
                        await asyncio.to_thread(sink.write, chunk)
                return bytes(output) if output is not None else size

    async def _using_client(self, operation):
        async with asyncio.timeout(300):
            if self.client is not None:
                return await operation(self.client)
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10), follow_redirects=False,
                                         trust_env=False, headers={'User-Agent': 'StudyCenter-Public-Updater',
                                                                  'Accept': 'application/vnd.github+json'}) as client:
                return await operation(client)

    async def check(self):
        async with self._lock:
            self._state, self._error = 'checking', None
            try:
                if self.platform == 'unsupported':
                    raise UpdateError('unsupported_platform')
                async def request(client):
                    return await self._fetch(client, API_URL, MAX_METADATA)
                items = json.loads(await self._using_client(request))
                if not isinstance(items, list) or len(items) > 100:
                    raise UpdateError('metadata')
                choices = []
                newest_seen = None
                for item in items:
                    if not isinstance(item, dict) or item.get('draft') is not False:
                        continue
                    tag = item.get('tag_name')
                    try:
                        parsed = _version(tag)
                        version = str(parsed)
                        filename = _asset_name(version, self.platform)
                    except (UpdateError, AttributeError):
                        continue
                    if newest_seen is None or parsed > newest_seen:
                        newest_seen = parsed
                    assets = item.get('assets')
                    if not isinstance(assets, list):
                        continue
                    capability_name = _capability_name(version, self.platform)
                    matches = {}
                    for asset in assets:
                        if isinstance(asset, dict) and asset.get('name') in (filename, 'SHA256SUMS', capability_name):
                            name = asset['name']
                            if name in matches:
                                raise UpdateError('metadata')
                            matches[name] = asset.get('browser_download_url')
                    if not {filename, 'SHA256SUMS'}.issubset(matches):
                        continue
                    if capability_name in matches and not isinstance(matches[capability_name], str):
                        raise UpdateError('release')
                    release = {'version': version, 'tag': tag, 'notes': item.get('body') or '',
                               'url': item.get('html_url'), 'artifact_url': matches[filename],
                               'checksums_url': matches['SHA256SUMS'],
                               'capability_url': matches.get(capability_name)}
                    self._validated_release(release)
                    choices.append((parsed, release))
                best = max(choices, key=lambda x: x[0]) if choices else None
                if newest_seen is not None and newest_seen > _version(self.current_version) and (best is None or newest_seen > best[0]):
                    raise UpdateError('missing_assets')
                selected = best[1] if best else None
                self._data['last_checked'] = datetime.now(timezone.utc).isoformat()
                self._data['release'] = selected
                self._state = 'available' if selected and _version(selected['version']) > _version(self.current_version) else 'current'
                self._save()
                return self.status()
            except (UpdateError, httpx.HTTPError, TimeoutError, OSError, ValueError, TypeError) as exc:
                self._error = exc.code if isinstance(exc, UpdateError) else 'check_failed'
                self._state = 'error'
                raise UpdateError(self._error) from None

    async def download(self, version):
        async with self._lock:
            self._state, self._error = 'downloading', None
            temp = None
            try:
                release = self._validated_release(self._data.get('release'))
                if version != release['version'] or _version(version) <= _version(self.current_version):
                    raise UpdateError('version')
                filename = _asset_name(version, self.platform)
                private_directory(self._root)
                root = owned_path(self._root, self.paths.cache)
                temp = owned_path(root / (uuid4().hex + '.partial'), root)
                async def request(client):
                    checksums = await self._fetch(client, release['checksums_url'], MAX_CHECKSUMS)
                    try:
                        lines = checksums.decode('utf-8').splitlines()
                    except UnicodeDecodeError:
                        raise UpdateError('checksum') from None
                    def checksum_for(name):
                        matching = []
                        for line in lines:
                            match = re.fullmatch(r'([0-9a-fA-F]{64}) [ *](.+)', line)
                            if match and match.group(2) == name:
                                matching.append(match.group(1).lower())
                        if len(matching) != 1:
                            raise UpdateError('checksum')
                        return matching[0]
                    expected = checksum_for(filename)
                    capability = None
                    if release.get('capability_url') is not None:
                        name = _capability_name(version, self.platform)
                        expected_capability = checksum_for(name)
                        raw = await self._fetch(client, release['capability_url'], MAX_CAPABILITY)
                        if hashlib.sha256(raw).hexdigest() != expected_capability:
                            raise UpdateError('checksum')
                        try:
                            capability = json.loads(raw, object_pairs_hook=_unique_object)
                        except (ValueError, UnicodeDecodeError):
                            raise UpdateError('capability') from None
                        required = {'format', 'application', 'version', 'platform', 'artifact', 'sha256', 'protocol'}
                        if self.platform == 'windows-x86_64':
                            required.add('installer_update_lock')
                        if (not isinstance(capability, dict) or set(capability) != required
                                or type(capability.get('format')) is not int or capability['format'] != 1
                                or capability.get('application') != 'StudyCenter.Public'
                                or capability.get('version') != version or capability.get('platform') != self.platform
                                or capability.get('artifact') != filename or capability.get('sha256') != expected
                                or type(capability.get('protocol')) is not int or capability['protocol'] != 1
                                or (self.platform == 'windows-x86_64' and capability.get('installer_update_lock') is not True)):
                            raise UpdateError('capability')
                    with temp.open('xb') as output:
                        await self._fetch(client, release['artifact_url'], MAX_ARTIFACT, output)
                    actual = await asyncio.to_thread(_hash_file, temp)
                    if actual != expected:
                        raise UpdateError('checksum')
                    return actual, capability
                digest, capability = await self._using_client(request)
                target = owned_path(root / filename, root)
                reject_links(target)
                os.replace(temp, target)
                temp = None
                self._data['ready'] = {'version': version, 'filename': filename, 'sha256': digest,
                                       'platform': self.platform, 'tag': release['tag']}
                if capability is not None:
                    self._data['ready']['update_protocol'] = 1
                    if self.platform == 'windows-x86_64':
                        self._data['ready']['installer_update_lock'] = True
                self._save()
                self._state = 'ready'
                return await asyncio.to_thread(self.get_ready, version)
            except (UpdateError, httpx.HTTPError, TimeoutError, OSError, ValueError, TypeError) as exc:
                self._error = exc.code if isinstance(exc, UpdateError) else 'download_failed'
                self._state = 'error'
                raise UpdateError(self._error) from None
            finally:
                if temp is not None:
                    try:
                        reject_links(temp).unlink(missing_ok=True)
                    except (OSError, ValueError):
                        pass

    def get_ready(self, version):
        record = self._data.get('ready')
        release = self._validated_release(self._data.get('release'))
        if (not isinstance(record, dict) or record.get('version') != version
                or record.get('platform') != self.platform or release['version'] != version
                or release['tag'] != record.get('tag') or _version(version) <= _version(self.current_version)):
            raise UpdateError('not_ready')
        if record.get('filename') != _asset_name(version, self.platform) or not re.fullmatch(r'[0-9a-f]{64}', str(record.get('sha256', ''))):
            raise UpdateError('not_ready')
        path = owned_path(self._root / record['filename'], self.paths.cache)
        try:
            if not path.is_file() or path.stat().st_size > MAX_ARTIFACT or _hash_file(path) != record['sha256']:
                raise UpdateError('checksum')
        except OSError:
            raise UpdateError('not_ready') from None
        result = dict(record, path=str(path))
        result['installer_compatible'] = self._compatible_record()
        result['installer_update_lock'] = (self.platform == 'windows-x86_64' and self._compatible_record())
        return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpdateError('capability')
        result[key] = value
    return result


def _hash_file(path):
    with reject_links(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()
