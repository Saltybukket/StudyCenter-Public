from types import SimpleNamespace

import httpx
import pytest

from scripts.write_update_manifest import write_update_manifest
from studycenter_public.update_catalog import API_URL, RELEASE_ROOT, UpdateCatalog


@pytest.mark.parametrize('platform', ['windows','linux'])
async def test_build_output_can_be_verified_by_updater(tmp_path, platform):
    assets = tmp_path/'assets'
    assets.mkdir()
    version, tag = '0.1.0b7', 'v0.1.0-beta.7'
    suffix = 'windows-x86_64-setup.exe' if platform == 'windows' else 'linux-x86_64-slim.tar.gz'
    artifact = assets/f'studycenter-public-{version}-{suffix}'
    artifact.write_bytes(b'synthetic installer, never executed')
    with pytest.raises(ValueError, match='freshly completed'):
        write_update_manifest(assets, version, platform)
    manifest = write_update_manifest(assets, version, platform, fresh_build=True)
    files = [artifact, manifest, assets/'SHA256SUMS']
    release = dict(tag_name=tag, draft=False, prerelease=True, body='Synthetic release',
                   html_url=f'{RELEASE_ROOT}/tag/{tag}',
                   assets=[{'name':file.name,'browser_download_url':f'{RELEASE_ROOT}/download/{tag}/{file.name}'} for file in files])
    def handle(request):
        if str(request.url) == API_URL:
            return httpx.Response(200, json=[release])
        for file in files:
            if request.url.path.endswith('/'+file.name):
                return httpx.Response(200, content=file.read_bytes())
        return httpx.Response(404)
    config, cache = tmp_path/'config', tmp_path/'cache'
    config.mkdir(); cache.mkdir()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        catalog = UpdateCatalog(SimpleNamespace(config=config,cache=cache), '0.1.0b6', platform+'-x86_64', client)
        await catalog.check()
        ready = await catalog.download(version)
        assert ready['installer_compatible']
        assert ready['installer_update_lock'] == (platform == 'windows')
        assert catalog.get_ready(version)['sha256'] == ready['sha256']
