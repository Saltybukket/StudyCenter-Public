from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from studycenter_public.core.app import create_app
from .reference_settings import Settings
from studycenter_public.core.db import encode
from studycenter_public.core.models import Candidate


def add_document(db, source_id, title, mime='text/html', analysis='success', processing='success', error=''):
    number = db.one('SELECT count(*) n FROM documents')['n'] + 1
    doc_id = db.execute('''INSERT INTO documents(source_id,url,title,mime,hash,fetched_at,original_path,pages,analysis_state,processing_state,error)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (source_id, f'https://example.org/{number}', title, mime, str(number),
        '2030-01-01T10:00:00+00:00', 'unused', encode([{'page':1,'anchor':'p1','text':'Abgabe bis 10.10.2030','gaps':[]}]), analysis, processing, error))
    return db.one('SELECT * FROM documents WHERE id=?', (doc_id,))


def test_course_next_task_uses_current_evidence_and_preserves_data(tmp_path):
    app = create_app(Settings(data_dir=tmp_path)); db = app.state.db
    course = db.execute("INSERT INTO courses(title,semester) VALUES('Mathematik','2030W')")
    source = db.execute("INSERT INTO sources(course_id,kind,title,url) VALUES(?,'public','Kursseite','https://example.org/course')", (course,))
    pdf = add_document(db, source, 'Übungsblatt.pdf', 'application/pdf')
    unrelated = add_document(db, source, 'Unrelated.pdf', 'application/pdf')
    item = db.ingest(pdf, Candidate(title='Übung abgeben', kind='task', identity_key='a', identity_scope='course',
        date='2030-10-10', date_meaning='deadline', obligation='required', quote='Abgabe bis 10.10.2030', page=1, anchor='p1'))
    before = {table: db.rows('SELECT * FROM '+table+' ORDER BY id') for table in ('items','item_evidence','documents','sources')}
    dom = BeautifulSoup(TestClient(app).get('/sources').text, 'html.parser')
    card = dom.select_one(f'#course-coverage-{course} .course-next')
    assert 'Übung abgeben' in card.get_text(' ', strip=True)
    assert card.select_one(f'a[href="/items/{item}"]')
    assert card.select_one(f'[data-voice-item="{item}"]')
    assert card.select_one(f'a[href="/quests?item={item}"]')
    assert card.select_one(f'a[href^="/documents/{pdf["id"]}?page=1"]')
    assert 'Unrelated.pdf' not in card.get_text(' ', strip=True)
    assert before == {table: db.rows('SELECT * FROM '+table+' ORDER BY id') for table in before}


def test_media_failures_are_not_disguised_as_deferred(tmp_path):
    app = create_app(Settings(data_dir=tmp_path)); db = app.state.db
    course = db.execute("INSERT INTO courses(title) VALUES('Kurs')")
    source = db.execute("INSERT INTO sources(course_id,kind,title,url,state) VALUES(?,'public','Video','https://tube.tugraz.at/example','partial')", (course,))
    deferred = add_document(db, source, 'Wartendes Medium', analysis='pending')
    failed = add_document(db, source, 'Fehlgeschlagenes Medium', analysis='failed', error='Modellfehler')
    stats = db.sources()[0]
    assert stats['analysis_pending'] == 2 and stats['deferred_analysis'] == 1
    client = TestClient(app)
    page = BeautifulSoup(client.get('/sources').text, 'html.parser')
    section = page.select_one(f'#source-{source}')
    assert section.has_attr('open')
    assert 'Auswertung offen / Fehler 1' in section.get_text(' ', strip=True)
    assert 'Bewusst zurückgestellt 1' in section.get_text(' ', strip=True)
    pending = client.get('/documents?state=pending').text
    deferred_page = client.get('/documents?state=deferred').text
    assert 'Fehlgeschlagenes Medium' in pending and 'Modellfehler' in pending
    assert 'Wartendes Medium' not in pending
    assert 'Wartendes Medium' in deferred_page and 'Fehlgeschlagenes Medium' not in deferred_page


def test_course_prefers_upcoming_and_retains_overdue_separately(tmp_path):
    from datetime import datetime, timedelta
    from studycenter_public.core.config import VIENNA
    app = create_app(Settings(data_dir=tmp_path)); db = app.state.db
    course = db.execute("INSERT INTO courses(title) VALUES('Kurs')")
    source = db.execute("INSERT INTO sources(course_id,kind,title,url) VALUES(?,'public','Seite','https://example.org/course')", (course,))
    pdf = add_document(db, source, 'Blatt%201.pdf', 'application/pdf')
    today = datetime.now(VIENNA).date()
    ids = []
    for title, offset in [('Alt', -20), ('Bevorstehend', 3)]:
        day = (today + timedelta(days=offset)).isoformat()
        ids.append(db.ingest(pdf, Candidate(title=title, kind='task', identity_key=title, identity_scope='course',
            date=day, date_meaning='deadline', obligation='required', quote='Abgabe bis 10.10.2030', page=1, anchor=title)))
    card = BeautifulSoup(TestClient(app).get('/sources').text, 'html.parser').select_one(f'#course-coverage-{course} .course-next')
    assert card.select_one('h3').get_text(strip=True) == 'Bevorstehend'
    assert card.select_one(f'.course-overdue a[href="/items/{ids[0]}"]')
    assert 'Blatt 1.pdf' in card.get_text(' ', strip=True)
    assert db.one('SELECT title FROM documents WHERE id=?', (pdf['id'],))['title'] == 'Blatt%201.pdf'


def test_superseded_document_is_not_presented_as_current_task_proof(tmp_path):
    app = create_app(Settings(data_dir=tmp_path)); db = app.state.db
    course = db.execute("INSERT INTO courses(title) VALUES('Kurs')")
    source = db.execute("INSERT INTO sources(course_id,kind,title,url) VALUES(?,'public','Seite','https://example.org/course')", (course,))
    old = add_document(db, source, 'Altes Blatt', 'application/pdf')
    db.ingest(old, Candidate(title='Alte Aufgabe', kind='task', identity_key='old', identity_scope='course',
        date='2099-10-10', date_meaning='deadline', obligation='required', quote='Abgabe bis 10.10.2030', page=1, anchor='p1'))
    new = add_document(db, source, 'Neues Blatt', 'application/pdf')
    db.execute('UPDATE documents SET url=?,fetched_at=? WHERE id=?', (old['url'], '2031-01-01T10:00:00+00:00', new['id']))
    card = BeautifulSoup(TestClient(app).get('/sources').text, 'html.parser').select_one(f'#course-coverage-{course} .course-next')
    assert 'Alte Aufgabe' not in card.get_text(' ', strip=True)
    assert 'aktuellem Quellenbeleg' in card.get_text(' ', strip=True)


def test_nearest_dated_course_is_featured_ahead_of_undated_and_attention(tmp_path):
    from datetime import datetime, timedelta
    from studycenter_public.core.config import VIENNA
    app = create_app(Settings(data_dir=tmp_path)); db = app.state.db
    course_ids = []
    for title, offset in [('A undatiert', None), ('B spaeter', 8), ('C zuerst', 2), ('D Fehler', None)]:
        course = db.execute('INSERT INTO courses(title) VALUES(?)', (title,))
        course_ids.append(course)
        state = 'failed' if title == 'D Fehler' else 'success'
        source = db.execute("INSERT INTO sources(course_id,kind,title,url,state,last_success) VALUES(?,'public',?,?,?,?)",
                            (course, title, f'https://example.org/course/{course}', state, '2030-01-01T10:00:00+00:00'))
        if title == 'D Fehler':
            continue
        doc = add_document(db, source, title)
        day = (datetime.now(VIENNA).date() + timedelta(days=offset)).isoformat() if offset is not None else None
        db.ingest(doc, Candidate(title=title, kind='task', identity_key=title, identity_scope='course',
            date=day, date_meaning='deadline' if day else 'none', obligation='optional',
            quote='Abgabe bis 10.10.2030', page=1, anchor='p1'))
    page = BeautifulSoup(TestClient(app).get('/sources').text, 'html.parser')
    featured = page.select('.course-coverage-grid > .course-coverage-card')
    assert len(featured) == 1 and featured[0]['id'] == f'course-coverage-{course_ids[2]}'
    remaining = page.select('#other-courses > details')
    assert [x.select_one('summary').get_text(strip=True) for x in remaining] == ['B spaeter', 'D Fehler', 'A undatiert']
    assert all(not x.has_attr('open') for x in remaining)
