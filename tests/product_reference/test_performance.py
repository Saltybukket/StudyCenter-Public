"""Small performance contracts: no state cache, no source/network/model work."""
from pathlib import Path

import pymupdf
from fastapi.testclient import TestClient

from studycenter_public.core.app import create_app
from .reference_settings import Settings
from studycenter_public.core.db import encode, now
from studycenter_public.core.documents import DocumentStore
from studycenter_public.core.models import Candidate
from studycenter_public.core.page_images import PageImages


def pdf_bytes(text):
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40, 40), text)
        return pdf.tobytes()


def test_pdf_preview_cache_revalidates_bytes_and_is_bounded(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    db = app.state.db
    sid = db.execute("INSERT INTO sources(kind,title,url) VALUES('public','Probe','https://example.org')")
    identifier = DocumentStore(db, settings).save(sid, 'https://example.org/a.pdf', 'PDF', 'application/pdf', pdf_bytes('First'))
    original = pymupdf.Page.get_pixmap
    rendered = []
    def render(page, *args, **kwargs):
        rendered.append(page.number)
        return original(page, *args, **kwargs)
    monkeypatch.setattr(pymupdf.Page, 'get_pixmap', render)
    client = TestClient(app)
    url = f'/documents/{identifier}/pages/1.png'
    first = client.get(url)
    repeated = client.get(url)
    assert first.status_code == 200 and first.content == repeated.content and len(rendered) == 1
    assert first.headers['cache-control'] == 'private, max-age=0, must-revalidate'
    assert 'content-encoding' not in first.headers  # PNG is already compressed.
    unchanged = client.get(url, headers={'If-None-Match': 'W/' + first.headers['etag']})
    assert unchanged.status_code == 304 and not unchanged.content and len(rendered) == 1
    path = Path(db.one('SELECT original_path FROM documents WHERE id=?', (identifier,))['original_path'])
    path.write_bytes(pdf_bytes('Replacement'))
    changed = client.get(url, headers={'If-None-Match': first.headers['etag']})
    assert changed.status_code == 200 and changed.content != first.content and changed.headers['etag'] != first.headers['etag']
    assert client.get(url.replace('/1.png', '/0.png')).status_code == 404
    assert client.get(url.replace('/1.png', '/2.png')).status_code == 404
    cache = PageImages(tmp_path, max_entries=1)
    cache.image(path, 1)
    assert len(list(cache.directory.glob('*.png'))) <= 2  # Existing entries are pruned on new writes.
    path.write_bytes(pdf_bytes('Third'))
    cache.image(path, 1)
    assert len(list(cache.directory.glob('*.png'))) == 1
    cache = PageImages(tmp_path, max_bytes=1)
    path.write_bytes(pdf_bytes('Oversize'))
    assert cache.image(path, 1)[0].startswith(b'\x89PNG')


def test_document_list_selects_counts_and_compressed_feed_keeps_fresh_status(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    db = app.state.db
    sid = db.execute("INSERT INTO sources(kind,title,url) VALUES('public','Probe','https://example.org')")
    identifier = DocumentStore(db, settings).save(sid, 'https://example.org/doc', 'Readable', 'text/plain', b'Original ' * 1000)
    db.execute("UPDATE documents SET pages=?,processing_state='success',analysis_state='success' WHERE id=?",
               (encode([{'page': 1, 'text': 'long text' * 10000}, {'page': 2, 'text': 'second'}]), identifier))
    selected = []
    rows = db.rows
    def traced(sql, *args, **kwargs):
        result = rows(sql, *args, **kwargs)
        if 'page_count' in sql:
            selected.extend(result)
        return result
    monkeypatch.setattr(db, 'rows', traced)
    client = TestClient(app)
    response = client.get('/documents')
    assert response.status_code == 200 and 'Readable' in response.text
    original = client.get(f'/documents/{identifier}/original')
    assert original.content == b'Original ' * 1000 and 'content-encoding' not in original.headers
    assert selected[0]['page_count'] == 2 and 'pages' not in selected[0] and 'progress' not in selected[0]
    value = Candidate(title='Own appointment', kind='event', date='2030-10-14', time='09:00',
                      date_meaning='start', obligation='required', quote='Personal event', page=1,
                      anchor='own', description='Preserved description ' * 150)
    item = db.execute('INSERT INTO items(identity,payload,created_at,changed_at) VALUES(?,?,?,?)',
                      ('note:probe', value.model_dump_json(), now(), now()))
    feed = client.get('/api/calendar', headers={'Accept-Encoding': 'gzip'})
    assert feed.headers['content-encoding'] == 'gzip' and 'Accept-Encoding' in feed.headers['vary']
    # The human-readable display trims surrounding whitespace; compression must
    # retain every word, while the stored source text remains byte-for-byte intact.
    assert feed.json()[0]['extendedProps']['description'] == value.description.strip()
    import json
    assert json.loads(db.one('SELECT payload FROM items WHERE id=?',(item,))['payload'])['description'] == value.description
    db.execute("UPDATE items SET status='done' WHERE id=?", (item,))
    assert client.get('/api/calendar').json()[0]['extendedProps']['status'] == 'done'
    assert len(feed.content) > int(feed.headers['content-length']) * 5
    from fastapi.responses import Response
    @app.get('/performance-event-stream')
    async def event_stream():
        return Response('data: preserved\n\n' * 100, media_type='text/event-stream')
    assert 'content-encoding' not in client.get('/performance-event-stream').headers


def test_item_detail_resolves_related_items_once_and_reads_fresh_values(tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path))
    db = app.state.db
    course = db.execute("INSERT INTO courses(title) VALUES('Testkurs')")
    def add(title, identity, related=None):
        value = Candidate(title=title, kind='task', identity_key=identity, related_key=related,
                          date='2030-01-01', date_meaning='deadline', obligation='required',
                          quote='Test', page=1, anchor='test')
        return db.execute('INSERT INTO items(course_id,identity,payload,created_at,changed_at) VALUES(?,?,?,?,?)',
                          (course, 'test:' + identity, value.model_dump_json(), now(), now()))
    parent = add('Anmeldung', 'registration', 'exam')
    linked = add('Prüfung Original', 'exam')
    other_course = db.execute("INSERT INTO courses(title) VALUES('Anderer Kurs')")
    db.execute('UPDATE items SET course_id=? WHERE id=?', (other_course, add('Fremder Eintrag', 'foreign', 'exam')))
    calls = []
    original = db.items
    def traced(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(db, 'items', traced)
    client = TestClient(app)
    first = client.get(f'/items/{parent}')
    assert first.status_code == 200 and 'Prüfung Original' in first.text and 'Fremder Eintrag' not in first.text
    assert len(calls) == 1
    db.execute('UPDATE items SET overrides=? WHERE id=?', (encode({'title': 'Prüfung aktualisiert'}), linked))
    second = client.get(f'/items/{parent}')
    assert 'Prüfung aktualisiert' in second.text and 'Prüfung Original' not in second.text
    assert len(calls) == 2
def test_overview_reuses_one_request_item_projection_without_cross_request_cache(tmp_path,monkeypatch):
    app=create_app(Settings(data_dir=tmp_path));db=app.state.db
    value=Candidate(title='Aktuelle Aufgabe',kind='task',quote='Eigene Aufgabe',page=1,anchor='test')
    item=db.execute('INSERT INTO items(identity,payload,created_at,changed_at) VALUES(?,?,?,?)',('note:overview-projection',value.model_dump_json(),now(),now()))
    reads=[];original=db.items;received=[]
    def items(*args,**kwargs):
        reads.append(True)
        return original(*args,**kwargs)
    def dashboard(current_items=None):
        assert current_items is not None
        received.append(next(i for i in current_items if i['id']==item)['status'])
        return {'quests':[]}
    monkeypatch.setattr(db,'items',items);monkeypatch.setattr(app.state.quests,'dashboard',dashboard)
    client=TestClient(app)
    assert client.get('/overview').status_code==200
    assert len(reads)==1 and received==['open']
    db.execute("UPDATE items SET status='done' WHERE id=?",(item,))
    assert client.get('/overview').status_code==200
    assert len(reads)==2 and received==['open','done']


def test_course_overview_resolves_items_once(tmp_path,monkeypatch):
    app=create_app(Settings(data_dir=tmp_path));db=app.state.db
    calls=[];original=db.items
    def items(*args,**kwargs):
        calls.append(True)
        return original(*args,**kwargs)
    monkeypatch.setattr(db,'items',items)
    assert TestClient(app).get('/sources').status_code==200
    assert len(calls)==1
