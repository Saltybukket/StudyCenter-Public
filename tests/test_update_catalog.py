import hashlib
from types import SimpleNamespace

import httpx
import pytest

from studycenter_public.update_catalog import API_URL, RELEASE_ROOT, UpdateCatalog, UpdateError


def release(version, *, draft=False):
    tag = 'v' + version
    name = f'studycenter-public-{version}-windows-x86_64-setup.exe'
    base = f'{RELEASE_ROOT}/download/{tag}/'
    return {'tag_name': tag, 'draft': draft, 'prerelease': 'b' in version,
            'html_url': f'{RELEASE_ROOT}/tag/{tag}', 'body': 'Release notes',
            'assets': [{'name': name, 'browser_download_url': base + name},
                       {'name': 'SHA256SUMS', 'browser_download_url': base + 'SHA256SUMS'}]}


def setup(tmp_path, handler, current='0.1.0b6'):
    config, cache = tmp_path / 'config', tmp_path / 'cache'
    config.mkdir(exist_ok=True); cache.mkdir(exist_ok=True)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return UpdateCatalog(SimpleNamespace(config=config, cache=cache), current,
                         'windows-x86_64', client), client


@pytest.mark.asyncio
async def test_local_status_and_version_ordering(tmp_path):
    calls = []
    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=[release('0.1.0b9'), release('0.1.0b10'),
                                        release('0.2.0', draft=True), release('0.1.0b8')])
    catalog, client = setup(tmp_path, handle)
    assert catalog.status()['auto_check'] is False
    assert catalog.status()['state'] == 'idle'
    assert not calls
    status = await catalog.check()
    assert status['state'] == 'available'
    assert status['release']['version'] == '0.1.0b10'
    assert calls == [API_URL]
    catalog.skip('0.1.0b10')
    catalog.set_preferences(True)
    other = UpdateCatalog(catalog.paths, '0.1.0b6', 'windows-x86_64', client)
    assert other.status()['skipped_version'] == '0.1.0b10'
    assert other.status()['auto_check'] is True
    assert len(calls) == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_verified_download_replay_and_tamper_detection(tmp_path):
    artifact = b'synthetic update'
    item = release('0.1.0b7')
    filename = item['assets'][0]['name']
    def handle(request):
        if str(request.url) == API_URL:
            return httpx.Response(200, json=[item])
        if request.url.path.endswith('/SHA256SUMS'):
            return httpx.Response(200, text=f'{hashlib.sha256(artifact).hexdigest()}  {filename}\n')
        return httpx.Response(200, content=artifact)
    catalog, client = setup(tmp_path, handle)
    await catalog.check()
    result = await catalog.download('0.1.0b7')
    assert result['sha256'] == hashlib.sha256(artifact).hexdigest()
    from pathlib import Path
    target = Path(result['path'])
    assert target.read_bytes() == artifact
    replay = UpdateCatalog(catalog.paths, '0.1.0b6', 'windows-x86_64', client)
    assert replay.get_ready('0.1.0b7')['path'] == result['path']
    target.write_bytes(b'tampered')
    with pytest.raises(UpdateError, match='checksum'):
        replay.get_ready('0.1.0b7')
    await client.aclose()


@pytest.mark.asyncio
async def test_bad_checksum_preserves_previous_ready_and_data(tmp_path):
    item = release('0.1.0b7')
    name = item['assets'][0]['name']
    valid = True
    def handle(request):
        if str(request.url) == API_URL:
            return httpx.Response(200, json=[item])
        if request.url.path.endswith('/SHA256SUMS'):
            return httpx.Response(200, text=f'{hashlib.sha256(b"good").hexdigest()}  {name}\n')
        return httpx.Response(200, content=b'good' if valid else b'bad')
    catalog, client = setup(tmp_path, handle)
    data = tmp_path / 'personal-data'; data.write_bytes(b'keep')
    await catalog.check()
    before = await catalog.download('0.1.0b7')
    valid = False
    with pytest.raises(UpdateError, match='checksum'):
        await catalog.download('0.1.0b7')
    assert catalog.get_ready('0.1.0b7') == before
    assert data.read_bytes() == b'keep'
    assert not list((tmp_path / 'cache' / 'updates').glob('*.partial'))
    await client.aclose()


@pytest.mark.asyncio
async def test_wrong_asset_url_and_redirect_are_rejected(tmp_path):
    item = release('0.1.0b7')
    item['assets'][0]['browser_download_url'] = 'https://evil.example/installer.exe'
    calls = []
    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=[item])
    catalog, client = setup(tmp_path, handle)
    with pytest.raises(UpdateError, match='release'):
        await catalog.check()
    assert calls == [API_URL]
    item['assets'][0]['browser_download_url'] = f"{RELEASE_ROOT}/download/v0.1.0b7/{item['assets'][0]['name']}"
    await catalog.check()
    async def redirected(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={'location': 'https://evil.example/secret'})
    catalog.client = httpx.AsyncClient(transport=httpx.MockTransport(redirected))
    with pytest.raises(UpdateError, match='redirect'):
        await catalog.download('0.1.0b7')
    assert not any('evil.example' in call for call in calls)
    await catalog.client.aclose(); await client.aclose()


@pytest.mark.asyncio
async def test_unsupported_platform_never_networks(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr('studycenter_public.update_catalog.platform.machine', lambda: 'aarch64')
    catalog, client = setup(tmp_path, lambda request: calls.append(request))
    catalog.platform = 'unsupported'
    with pytest.raises(UpdateError, match='unsupported_platform'):
        await catalog.check()
    assert not calls
    await client.aclose()

@pytest.mark.asyncio
async def test_newer_release_missing_platform_asset_is_not_reported_as_current(tmp_path):
    item = release('0.1.0b7')
    item['assets'] = item['assets'][1:]
    catalog, client = setup(tmp_path, lambda req: httpx.Response(200, json=[item]))
    with pytest.raises(UpdateError, match='missing_assets'):
        await catalog.check()
    assert catalog.status()['state'] == 'error'
    await client.aclose()


@pytest.mark.asyncio
async def test_concurrent_checks_are_serialized(tmp_path):
    import asyncio
    active = 0
    peak = 0
    async def handle(request):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        await asyncio.sleep(0.03)
        active -= 1
        return httpx.Response(200, json=[release('0.1.0b7')])
    catalog, client = setup(tmp_path, handle)
    await asyncio.gather(catalog.check(), catalog.check())
    assert peak == 1
    await client.aclose()

@pytest.mark.asyncio
async def test_actual_beta_tag_normalizes_filename_and_rejects_replay_on_updated_app(tmp_path):
    artifact = b'beta installer'
    item = release('0.1.0b7')
    tag = 'v0.1.0-beta.7'
    item['tag_name'] = tag
    item['html_url'] = f'{RELEASE_ROOT}/tag/{tag}'
    for asset in item['assets']:
        asset['browser_download_url'] = f"{RELEASE_ROOT}/download/{tag}/{asset['name']}"
    def handle(request):
        if str(request.url) == API_URL:
            return httpx.Response(200, json=[item])
        if request.url.path.endswith('/SHA256SUMS'):
            return httpx.Response(200, text=f"{hashlib.sha256(artifact).hexdigest()}  {item['assets'][0]['name']}\n")
        return httpx.Response(200, content=artifact)
    catalog, client = setup(tmp_path, handle)
    status = await catalog.check()
    assert status['release']['version'] == '0.1.0b7'
    assert status['release']['tag'] == tag
    ready = await catalog.download('0.1.0b7')
    assert ready['tag'] == tag
    updated = UpdateCatalog(catalog.paths, '0.1.0b7', 'windows-x86_64', client)
    assert updated.status()['state'] == 'current'
    with pytest.raises(UpdateError, match='not_ready'):
        updated.get_ready('0.1.0b7')
    catalog._data['ready']['tag'] = 'v0.1.0-beta.6'
    with pytest.raises(UpdateError, match='not_ready'):
        catalog.get_ready('0.1.0b7')
    await client.aclose()

@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['valid', 'old', 'forged_hash', 'wrong_binding', 'wrong_lock'])
async def test_optional_bound_update_capability(tmp_path, mode):
    import json
    artifact = b'installer'
    digest = hashlib.sha256(artifact).hexdigest()
    item = release('0.1.0b7')
    filename = item['assets'][0]['name']
    cap_name = 'studycenter-public-0.1.0b7-windows-x86_64-update.json'
    capability = {'format': 1, 'application': 'StudyCenter.Public', 'version': '0.1.0b7',
                  'platform': 'windows-x86_64', 'artifact': filename, 'sha256': digest,
                  'protocol': 1, 'installer_update_lock': True}
    if mode == 'wrong_binding':
        capability['artifact'] = 'different.exe'
    if mode == 'wrong_lock':
        capability['installer_update_lock'] = False
    raw = json.dumps(capability).encode()
    if mode != 'old':
        item['assets'].append({'name': cap_name, 'browser_download_url':
                               f'{RELEASE_ROOT}/download/v0.1.0b7/{cap_name}'})
    expected = hashlib.sha256(b'forged' if mode == 'forged_hash' else raw).hexdigest()
    def handle(request):
        if str(request.url) == API_URL:
            return httpx.Response(200, json=[item])
        if request.url.path.endswith('/SHA256SUMS'):
            return httpx.Response(200, text=f'{digest}  {filename}\n{expected}  {cap_name}\n')
        if request.url.path.endswith('/' + cap_name):
            return httpx.Response(200, content=raw)
        return httpx.Response(200, content=artifact)
    catalog, client = setup(tmp_path, handle)
    assert (await catalog.check())['installer_compatible'] is False
    if mode in ('forged_hash', 'wrong_binding', 'wrong_lock'):
        with pytest.raises(UpdateError, match='checksum' if mode == 'forged_hash' else 'capability'):
            await catalog.download('0.1.0b7')
        assert catalog.status()['installer_compatible'] is False
    else:
        result = await catalog.download('0.1.0b7')
        assert result['installer_compatible'] is (mode == 'valid')
        assert result['installer_update_lock'] is (mode == 'valid')
        assert catalog.status()['installer_compatible'] is (mode == 'valid')
        restored = UpdateCatalog(catalog.paths, '0.1.0b6', 'windows-x86_64', client)
        assert restored.get_ready('0.1.0b7')['installer_compatible'] is (mode == 'valid')
        assert restored.status()['installer_compatible'] is (mode == 'valid')
    await client.aclose()

@pytest.mark.asyncio
async def test_linux_capability_binds_linux_archive(tmp_path):
    import json
    payload = b'linux archive'
    digest = hashlib.sha256(payload).hexdigest()
    version = '0.1.0b7'
    tag = 'v0.1.0-beta.7'
    filename = f'studycenter-public-{version}-linux-x86_64-slim.tar.gz'
    cap_name = f'studycenter-public-{version}-linux-x86_64-update.json'
    manifest = json.dumps({'format': 1, 'application': 'StudyCenter.Public', 'version': version,
                           'platform': 'linux-x86_64', 'artifact': filename, 'sha256': digest,
                           'protocol': 1}).encode()
    base = f'{RELEASE_ROOT}/download/{tag}/'
    item = {'tag_name': tag, 'draft': False, 'html_url': f'{RELEASE_ROOT}/tag/{tag}',
            'body': '', 'assets': [{'name': name, 'browser_download_url': base + name}
                                   for name in (filename, cap_name, 'SHA256SUMS')]}
    def handle(request):
        if str(request.url) == API_URL:
            return httpx.Response(200, json=[item])
        if request.url.path.endswith('SHA256SUMS'):
            return httpx.Response(200, text=f'{digest}  {filename}\n{hashlib.sha256(manifest).hexdigest()}  {cap_name}\n')
        if request.url.path.endswith(cap_name):
            return httpx.Response(200, content=manifest)
        return httpx.Response(200, content=payload)
    catalog, client = setup(tmp_path, handle)
    catalog.platform = 'linux-x86_64'
    await catalog.check()
    result = await catalog.download(version)
    assert result['installer_compatible'] is True
    assert result['installer_update_lock'] is False
    await client.aclose()
