import pytest

from studycenter_public.storage.db import Database
from studycenter_public.platform.paths import AppPaths
from studycenter_public.services.documents import DocumentService
from studycenter_public.services.sync import SyncService


def candidate(text='Assignment A1 deadline 2026-11-10.'):
    return {'title':'Assignment A1', 'kind':'task', 'page':1, 'anchor':'A1',
            'identity_key':'A1', 'identity_scope':'course', 'quote':text,
            'date':'2026-11-10', 'date_meaning':'deadline'}


def test_missing_trusted_course_never_merges_two_document_families(tmp_path):
    db = Database(tmp_path / 'db.sqlite3')
    source = db.upsert_source(name='Institution', kind='teachcenter')
    first = db.save_document({'source_id':source['id'], 'url':'https://example.org/course/100'}, pages=[{'page':1, 'text':candidate()['quote']}])
    second = db.save_document({'source_id':source['id'], 'url':'https://example.org/course/200'}, pages=[{'page':1, 'text':candidate()['quote']}])
    first_item = db.ingest(first, candidate() | {'course_id':100})
    second_item = db.ingest(second, candidate() | {'course_id':100})
    assert first_item['id'] != second_item['id']
    assert first_item['course_id'] is None and second_item['course_id'] is None


def test_selected_courses_with_same_assignment_key_remain_separate(tmp_path):
    paths = AppPaths.create(tmp_path)
    db = Database(paths.database)
    service = DocumentService(db, paths)
    source = db.upsert_source(name='Institution', kind='teachcenter')
    first_course = db.upsert_course(source['id'], '100', 'Synthetic algebra')
    second_course = db.upsert_course(source['id'], '200', 'Synthetic geometry')
    text = candidate()['quote']
    first_document = service.import_bytes(text.encode(), title='A1', source_id=source['id'], trusted_course_id=first_course['id'], source_url='https://example.org/course/100/a1', mime='text/plain')
    second_document = service.import_bytes(text.encode(), title='A1', source_id=source['id'], trusted_course_id=second_course['id'], source_url='https://example.org/course/200/a1', mime='text/plain')
    first = db.ingest(first_document, candidate() | {'course_id':second_course['id']})
    second = db.ingest(second_document, candidate() | {'course_id':first_course['id']})
    assert first['id'] != second['id']
    assert first['course_id'] == first_course['id']
    assert second['course_id'] == second_course['id']
    assert len(db.items()) == 2
    assert not first['conflict'] and not second['conflict']


def test_course_document_revisions_keep_binding_and_merge_same_assignment(tmp_path):
    paths = AppPaths.create(tmp_path)
    db = Database(paths.database)
    service = DocumentService(db, paths)
    source = db.upsert_source(name='Institution', kind='teachcenter')
    course = db.upsert_course(source['id'], '100', 'Synthetic algebra')
    first = service.import_bytes(candidate()['quote'].encode(), title='A1', mime='text/plain',
        source_id=source['id'], trusted_course_id=course['id'], source_url='https://example.org/course/100/a1')
    item = db.ingest(first, candidate())
    second = service.import_bytes((candidate()['quote'] + '\nNew source observation.').encode(), title='A1', mime='text/plain', replaces_document_id=first['id'])
    assert second['trusted_course_id'] == course['id']
    assert second['course_title'] == 'Synthetic algebra'
    assert second['revision'] == 2
    assert db.ingest(second, candidate())['id'] == item['id']
    assert len(db.item(item['id'])['evidence']) == 2
    with pytest.raises(ValueError, match='Quellidentität'):
        service.import_bytes(b'New unrelated source', title='A1', replaces_document_id=first['id'], source_url='https://elsewhere.org/')


def test_shared_document_url_is_bound_to_each_selected_course(tmp_path):
    db = Database(tmp_path / 'db.sqlite3')
    source = db.upsert_source(name='Institution', kind='teachcenter')
    one = db.upsert_course(source['id'], '100', 'Synthetic algebra')
    two = db.upsert_course(source['id'], '200', 'Synthetic geometry')
    document = {'source_id':source['id'], 'url':'https://example.org/shared/a1', 'hash':'same'}
    first = db.save_document(document | {'trusted_course_id':one['id']}, pages=[{'page':1, 'text':candidate()['quote']}])
    second = db.save_document(document | {'trusted_course_id':two['id']}, pages=[{'page':1, 'text':candidate()['quote']}])
    assert first['id'] != second['id']
    assert db.ingest(first, candidate())['id'] != db.ingest(second, candidate())['id']
    other_source = db.upsert_source(name='Other institution', kind='teachcenter')
    with pytest.raises(ValueError, match='retained source'):
        db.save_document(document | {'source_id':other_source['id'], 'trusted_course_id':one['id']}, pages=[{'page':1, 'text':'Source'}])


def test_same_named_local_imports_are_distinct_until_explicit_revision(tmp_path):
    paths = AppPaths.create(tmp_path)
    service = DocumentService(Database(paths.database), paths)
    first = service.import_bytes(b'First unrelated original', title='Schedule.txt', mime='text/plain')
    second = service.import_bytes(b'Second unrelated original', title='Schedule.txt', mime='text/plain')
    assert first['url'] != second['url']
    assert first['revision'] == second['revision'] == 1
    revised = service.import_bytes(b'First explicitly revised original', title='Schedule.txt', mime='text/plain', replaces_document_id=first['id'])
    assert revised['url'] == first['url']
    assert revised['revision'] == 2
    assert service.source_text(first['id']) == 'First unrelated original'
    assert service.source_text(second['id']) == 'Second unrelated original'


@pytest.mark.asyncio
async def test_manual_selected_course_sync_imports_trusted_identity(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager
    from studycenter_public.collectors.web import Fetched
    import studycenter_public.services.sync as module
    paths = AppPaths.create(tmp_path)
    db = Database(paths.database)
    class Browser:
        @asynccontextmanager
        async def session(self):
            yield object()
    class Collector:
        def __init__(self, context, kind):
            pass
        async def fetch(self, url, root, policy):
            return Fetched(url, candidate()['quote'].encode(), 'text/plain', 'A1')
        async def close(self):
            pass
    monkeypatch.setattr(module, 'InstitutionalCollector', Collector)
    service = SyncService(db, paths, browser=Browser())
    source = service.register_source('Institution', kind='teachcenter')
    db.upsert_source(source['id'], name='Institution', metadata={'discovered_courses':[
        {'external_id':'100', 'title':'Synthetic algebra', 'url':'https://tc.tugraz.at/main/course/view.php?id=100'},
        {'external_id':'200', 'title':'Synthetic geometry', 'url':'https://tc.tugraz.at/main/course/view.php?id=200'}]})
    selected = service.select_courses(source['id'], ['100','200'])
    assert len(db.courses(source['id'])) == 2
    result = await service.sync(source['id'])
    assert result['complete']
    documents = db.documents(source['id'])
    assert {doc['trusted_course_id'] for doc in documents} == {course['trusted_course_id'] for course in selected['selected_courses']}
    assert {doc['course_title'] for doc in documents} == {'Synthetic algebra','Synthetic geometry'}
    assert len({db.ingest(doc, candidate())['id'] for doc in documents}) == 2
