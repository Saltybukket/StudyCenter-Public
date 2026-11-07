from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from studycenter_public.core.app import create_app
from studycenter_public.core.config import VIENNA
from studycenter_public.core.db import Database, encode
from studycenter_public.core.documents import DocumentStore
from studycenter_public.core.models import Candidate
from .reference_settings import Settings

@pytest.fixture
def environment(tmp_path):
    settings = Settings(data_dir=tmp_path)
    db = Database(tmp_path / 'campusboard.sqlite3')
    cid = db.execute("INSERT INTO courses(title,semester) VALUES('Prüfkurs','2026W')")
    sid = db.execute("INSERT INTO sources(course_id,kind,title,url,config) VALUES(?,'public','Testquelle','https://example.org/course/',?)", (cid, encode({'scope':'/course/','pause':0.3})))
    return settings, db, cid, sid

def candidate(**changes):
    values = dict(title='A2 abgeben', kind='task', identity_key='A2', identity_scope='course', date='2026-11-12', date_meaning='deadline', obligation='required', quote='A2 abgeben bis 12. November', page=1, anchor='A2')
    return Candidate(**(values | changes))

def test_overview_and_board_filters_preserve_personal_data(environment):
    settings, db, cid, sid = environment
    from datetime import timedelta
    today = datetime.now(VIENNA).date()
    store = DocumentStore(db, settings)
    doc_id = store.save(sid, 'https://example.org/course/overview', 'Organisation', 'text/plain', b'Organisation')
    doc = db.one('SELECT * FROM documents WHERE id=?', (doc_id,))
    def add(key, **changes):
        return db.ingest(doc, candidate(identity_key=key, anchor=key, title=key,
                         **({'date': today.isoformat()} | changes)))
    window = add('Projektfenster', date=(today-timedelta(days=3)).isoformat(), time='07:00:00',
                 end_date=(today+timedelta(days=1)).isoformat())
    add('Spaetere Abgabe', end_date=(today+timedelta(days=40)).isoformat())
    add('Nur Empfehlung', date_meaning='recommended', obligation='recommended')
    add('Abgesagte Abgabe', source_state='cancelled')
    completed = add('Erledigte Abgabe')
    ignored = add('Ignorierte Abgabe')
    add('Aktuelle Anmeldung', kind='registration', date_meaning='window',
        date=(today-timedelta(days=2)).isoformat(), end_date=(today+timedelta(days=2)).isoformat())
    expired = add('Geschlossene Anmeldung', kind='registration', date_meaning='window',
                  date=(today-timedelta(days=3)).isoformat(), end_date=(today-timedelta(days=1)).isoformat())
    overdue = add('Ueberfaellige Abgabe', date=(today-timedelta(days=1)).isoformat(),date_meaning='deadline')
    for n in range(10):
        add(f'Vorlesung {n}', kind='event', date_meaning='start')
    add('Kommende Pruefung', kind='exam', date_meaning='start', date=(today+timedelta(days=1)).isoformat())
    historical = add('Alte Unterlage', semester='2026S')
    db.execute("UPDATE items SET status='done' WHERE id=?", (completed,))
    db.execute('UPDATE items SET ignored=1 WHERE id=?', (ignored,))
    db.execute("UPDATE items SET status='doing',pinned=1,overrides=? WHERE id=?",
               (encode({'description': 'Persoenliche Lernplanung'}), window))
    before = db.rows('SELECT * FROM items ORDER BY id')
    with TestClient(create_app(settings)) as client:
        overview = client.get('/overview')
        assert overview.status_code == 200
        assert 'Projektfenster' in overview.text and 'Aktuelle Anmeldung' in overview.text
        from bs4 import BeautifulSoup
        dom = BeautifulSoup(overview.text,'html.parser')
        assert not dom.select_one(f'#actions [data-item-id="{expired}"]')
        assert not dom.select_one(f'#deadlines [data-item-id="{expired}"]')
        assert dom.select_one(f'#overdue [data-item-id="{expired}"]')
        assert dom.select_one(f'#overdue [data-item-id="{overdue}"]')
        action = dom.select_one(f'#actions [data-item-id="{window}"]')
        assert '07:00' not in action.get_text() and 'Ohne Uhrzeit' in action.get_text()
        for title in ('Nur Empfehlung', 'Abgesagte Abgabe', 'Erledigte Abgabe', 'Ignorierte Abgabe', 'Alte Unterlage'):
            assert title not in overview.text
        assert f'/items/{window}' in overview.text and 'Quellenstand prüfen' in overview.text
        for kind, expected, excluded in [('exam', 'Kommende Pruefung', ['Projektfenster', 'Aktuelle Anmeldung', 'Vorlesung 0']),
                                         ('registration', 'Aktuelle Anmeldung', ['Projektfenster', 'Kommende Pruefung', 'Vorlesung 0']),
                                         ('task', 'Projektfenster', ['Aktuelle Anmeldung', 'Kommende Pruefung', 'Vorlesung 0'])]:
            page = client.get('/overview', params={'kind': kind}).text
            assert expected in page
            assert all(title not in page for title in excluded)
            assert f'href="/overview?kind={kind}" aria-current="true"' in page
        assert 'Aktuelle Anmeldung' in client.get('/overview?kind=invalid').text
        filtered = client.get('/board', params={'course': cid, 'q': 'PERSOENLICHE', 'status': 'doing'})
        assert filtered.status_code == 200 and 'Projektfenster' in filtered.text
        assert 'Erledigte Abgabe' not in filtered.text and 'Spaetere Abgabe' not in filtered.text
        assert not BeautifulSoup(client.get('/board?q=nothing-matches').text,'html.parser').select_one('[data-item-id]')
        assert 'Alte Unterlage' in client.get('/board?include_inactive=true').text
        assert 'Ignorierte Abgabe' in client.get('/board?show_ignored=true').text
        assert '/overview' in client.get('/calendar').text
    assert db.rows('SELECT * FROM items ORDER BY id') == before


def test_dashboard_personal_evidence_deadline_conflicts_and_manual_sync(environment):
    from datetime import timedelta
    from studycenter_public.core.db import now
    from bs4 import BeautifulSoup
    settings,db,cid,sid=environment
    today=datetime.now(VIENNA).date()
    store=DocumentStore(db,settings)
    doc_id=store.save(sid,'https://example.org/actions','Organisation','text/plain',b'Organisation')
    doc=db.one('SELECT * FROM documents WHERE id=?',(doc_id,))
    proposed=candidate(kind='event',identity_key='Offer',title='Angebotene Labslots',date=today.isoformat(),date_meaning='start',obligation='required',uncertainty='Mehrere alternative Termine; keine persönliche Slot-Zuteilung angegeben.')
    offered_id=db.ingest(doc,proposed)
    start=(today-timedelta(days=3)).isoformat(); end=(today+timedelta(days=1)).isoformat()
    task_id=db.ingest(doc,candidate(identity_key='A1',date=start,end_date=end,anchor='window'))
    db.ingest(doc,candidate(identity_key='A1',date=end,anchor='closing',quote='Abgabe am Schlussdatum'))
    assert not next(i for i in db.items() if i['id']==task_id)['date_conflict']
    own_sid=db.execute("INSERT INTO sources(course_id,kind,title,url) VALUES(?,'tugrazonline','Eigener Kalender','https://online.tugraz.at/calendar')",(cid,))
    own_doc_id=store.save(own_sid,'https://online.tugraz.at/calendar','Eigener Kalender','text/calendar',b'BEGIN:VCALENDAR\nEND:VCALENDAR')
    own_doc=db.one('SELECT * FROM documents WHERE id=?',(own_doc_id,))
    own_id=db.ingest(own_doc,candidate(kind='event',identity_key='tugrazonline:own',title='Eigener LV-Termin',date=today.isoformat(),date_meaning='start',obligation='unclear'))
    assert next(i for i in db.items() if i['id']==own_id)['personal_event']
    planned=db.ingest(doc,candidate(identity_key='personal-plan',title='Heute Unterlagen vorbereiten',date=today.isoformat(),date_meaning='start'))
    db.execute("UPDATE items SET identity='note:planned-fixture' WHERE id=?",(planned,))
    db.execute("UPDATE items SET status='doing',pinned=1,seen=1,overrides=? WHERE id=?",(encode({'description':'Meine Planung'}),task_id))
    run=db.reserve_run('manual')
    db.execute("UPDATE sync_runs SET state='success',finished_at=? WHERE id=?",(now(),run))
    db.ingest(doc,candidate(identity_key='Changed',title='Geänderte Abgabe',date=end,anchor='change'))
    db.ingest(doc,candidate(identity_key='Changed',title='Geänderte Abgabe',date=(today+timedelta(days=2)).isoformat(),anchor='change',is_update=True))
    before=db.rows('SELECT * FROM items ORDER BY id')
    with TestClient(create_app(settings)) as client:
        page=client.get('/overview').text
        dom=BeautifulSoup(page,'html.parser')
        assert not dom.select_one(f'.dashboard-step[data-item-id="{offered_id}"]')
        assert dom.select_one(f'#actions [data-item-id="{own_id}"]')
        planned_action=dom.select_one(f'#actions [data-item-id="{planned}"]')
        assert planned_action and 'Geplante Aufgabe' in planned_action.get_text() and 'Ohne Uhrzeit' in planned_action.get_text()
        assert not dom.select_one(f'#deadlines [data-item-id="{planned}"]')
        from studycenter_public.core.app import action_state
        planned_state=action_state(next(i for i in db.items() if i['id']==planned),datetime.now(VIENNA))
        assert not planned_state['action_deadline'] and planned_state['action_time'] is None
        assert 'Persönlicher Kalendertermin' in page
        assert dom.select_one('a[href="/entries?view=updates"]')
        assert not dom.select_one('#updates')  # Detailed change history lives under Informationen.
        # The current timeline intentionally contains personal/actionable dates,
        # not unassigned offers. The original offer remains available in storage.
        assert 'Angebotene Labslots' not in client.get('/').text
        assert any(i['id']==offered_id for i in db.items())
        assert dom.select_one('a[href="/sources"]')
        assert client.post('/settings',data={'auto_sync':'on','daily_time':'10:00','csrf':client.app.state.csrf}).status_code==405
        assert not hasattr(client.app.state.sync,'scheduler')
    assert before==db.rows('SELECT * FROM items ORDER BY id')
