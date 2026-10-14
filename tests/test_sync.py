import httpx
import pytest

from studycenter_public.services.sync import SyncService
from studycenter_public.collectors.web import PublicCollector
from studycenter_public.storage.db import Database
from studycenter_public.platform.paths import AppPaths


@pytest.mark.asyncio
async def test_manual_sync_bounded_resume_and_unchanged_originals(tmp_path):
    paths = AppPaths.create(tmp_path)
    db = Database(paths.database)
    async def serve(request):
        if request.url.path == '/course/':
            return httpx.Response(200, headers={'content-type':'text/html'}, content=b'<main>Start<a href="page">Page</a></main>')
        return httpx.Response(200, headers={'content-type':'text/plain'}, content=b'Complete retained linked page')
    factory = lambda: PublicCollector(client=httpx.AsyncClient(transport=httpx.MockTransport(serve)), resolver=lambda host:['93.184.216.34'])
    service = SyncService(db, paths, collector_factory=factory)
    source = service.register_source('Synthetic course', 'https://example.org/course/', options={'max_pages':1})
    first = await service.sync(source['id'])
    assert first['state'] == 'partial'
    assert first['changed']
    second = await service.sync(source['id'], resume=True)
    assert second['complete']
    assert len(db.documents(source['id'])) == 2
    service.register_source('Synthetic course', 'https://example.org/course/', options={'max_pages':3}, source_id=source['id'])
    third = await service.sync(source['id'])
    assert third['complete']
    assert not third['changed']
    assert len(db.documents(source['id'])) == 2


def test_course_selection_requires_discovered_course(tmp_path):
    paths = AppPaths.create(tmp_path)
    service = SyncService(Database(paths.database), paths)
    source = service.register_source('TU', kind='teachcenter')
    with pytest.raises(ValueError, match='entdeckte'):
        service.select_courses(source['id'], [{'external_id':'fake', 'url':'https://example.org/'}])
