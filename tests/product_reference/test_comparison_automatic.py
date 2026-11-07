import json

import pytest
from fastapi.testclient import TestClient

from studycenter_public.core.app import create_app
from studycenter_public.core.comparison import comparison, save
from .reference_settings import Settings
from studycenter_public.core.db import Database, encode
from studycenter_public.core.models import Candidate


def add_task(db, opening, quote, *, closing='2030-11-10', opening_time=None,
             closing_time='23:59', fetched='2030-10-01T10:00:00+00:00'):
    course = db.one('SELECT id FROM courses')
    cid = course['id'] if course else db.execute("INSERT INTO courses(title,semester) VALUES('Testkurs','2030W')")
    number = db.one('SELECT COUNT(*) n FROM sources')['n'] + 1
    url = f'https://example.org/source-{number}'
    sid = db.execute("INSERT INTO sources(course_id,kind,title,url) VALUES(?,'teachcenter',?,?)",
                     (cid, f'Quelle {number}', url))
    doc_id = db.execute('''INSERT INTO documents(source_id,url,title,mime,hash,fetched_at,original_path,pages,analysis_state)
                           VALUES(?,?,?,?,?,?,?,?,?)''',
                        (sid,url,f'Quelle {number}','text/plain',f'hash-{number}',fetched,'unused',
                         encode([{'page':1,'text':quote}]),'success'))
    doc = db.one('SELECT * FROM documents WHERE id=?',(doc_id,))
    candidate = Candidate(title='Arbeitsblatt 2',kind='task',identity_scope='course',identity_key='AB2',
        semester='2030W',date=opening,time=opening_time,end_date=closing,end_time=closing_time,
        date_meaning='window',obligation='required',quote=quote,page=1,anchor='AB2')
    return db.ingest(doc,candidate), doc


def row(db, iid):
    return next(item for item in db.items() if item['id']==iid)


def release_and_opening(db):
    iid, release = add_task(db,'2030-10-26','Ausgabe AB2',closing_time=None,
                            fetched='2030-11-05T10:00:00+00:00')
    _, opening = add_task(db,'2030-10-28','Geöffnet:\nMittwoch, 28. Oktober 2030, 00:00',
                          opening_time='00:00',fetched='2030-10-01T10:00:00+00:00')
    return iid, release, opening


def test_release_and_literal_opening_are_different_roles_without_writes(tmp_path):
    db = Database(tmp_path/'db.sqlite3')
    iid, release, opening = release_and_opening(db)
    db.execute("UPDATE items SET status='doing',pinned=1 WHERE id=?",(iid,))
    before = {table:db.rows('SELECT * FROM '+table) for table in ('items','item_evidence','item_preferences')}
    item = row(db,iid)
    assert not item['conflict'] and not item['date_conflict']
    assert (item['data']['date'],item['data']['time']) == ('2030-10-28','00:00:00')
    assert (item['data']['end_date'],item['data']['end_time']) == ('2030-11-10','23:59:00')
    assert item['status']=='doing' and item['pinned']
    auto = item['evidence_resolution']['auto_resolutions'][0]
    assert auto['effective'] and auto['proofs'][0]['document_id']==opening['id']
    assert auto['other_roles'][0]['document_id']==release['id']
    view = comparison(db,iid,'opening')
    assert len(view['cards'])==1 and view['cards'][0]['document_id']==opening['id']
    assert comparison(db,iid)['fact']=='opening'
    assert before=={table:db.rows('SELECT * FROM '+table) for table in before}


@pytest.mark.parametrize('quote,opening,clock',[
    ('Geöffnet:\nFreitag, 30. Oktober 2030, 00:00','2030-10-30','00:00'),
    ('Kursseite mit anderem Termin','2030-10-30','00:00'),
    ('Geöffnet:\nMittwoch, 28. Oktober 2030, 10:00','2030-10-28','10:00'),
])
def test_real_or_unclassified_opening_disagreement_remains_visible(tmp_path,quote,opening,clock):
    db = Database(tmp_path/'db.sqlite3')
    iid, _, _ = release_and_opening(db)
    add_task(db,opening,quote,opening_time=clock,fetched='2030-11-08T10:00:00+00:00')
    item = row(db,iid)
    assert item['conflict'] and item['date_conflict']
    assert not item['evidence_resolution'].get('auto_resolutions')
    assert 'opening_date' in item['evidence_resolution']['unresolved_fields'] or 'opening_time' in item['evidence_resolution']['unresolved_fields']


def test_disputed_closing_is_never_resolved_by_different_opening_roles(tmp_path):
    db = Database(tmp_path/'db.sqlite3')
    iid, _, _ = release_and_opening(db)
    add_task(db,'2030-10-28','Geöffnet:\nMittwoch, 28. Oktober 2030, 00:00',
             opening_time='00:00',closing='2030-11-11')
    item = row(db,iid)
    assert item['conflict'] and item['date_conflict']
    assert 'closing_date' in item['evidence_resolution']['unresolved_fields']
    assert not item['evidence_resolution'].get('auto_resolutions')


@pytest.mark.parametrize('which',['release','opening'])
def test_unmatched_quote_cannot_authorize_automatic_resolution(tmp_path,which):
    db = Database(tmp_path/'db.sqlite3')
    iid, release, opening = release_and_opening(db)
    doc = release if which=='release' else opening
    db.execute('UPDATE documents SET pages=? WHERE id=?',
               (encode([{'page':1,'text':'Andere Fundstelle ohne das angegebene Zitat.'}]),doc['id']))
    item = row(db,iid)
    assert item['conflict'] and item['date_conflict']
    assert not item['evidence_resolution'].get('auto_resolutions')


@pytest.mark.parametrize('quote',[
    'Geöffnet: 28.10.2030, 00:00 Geöffnet: 30.10.2030, 00:00',
    'Geöffnet:\n28.10.2030, 00:00\nGeöffnet:\n30.10.2030, 00:00',
    'Geöffnet:\nSchließt: 28.10.2030, 00:00',
    'Geöffnet: 28.10.2030 und 30.10.2030',
])
def test_ambiguous_or_wrongly_labelled_opening_text_stays_open(tmp_path,quote):
    db = Database(tmp_path/'db.sqlite3')
    iid, _ = add_task(db,'2030-10-26','Ausgabe AB2',closing_time=None)
    add_task(db,'2030-10-28',quote,opening_time='00:00')
    item = row(db,iid)
    assert item['conflict'] and item['date_conflict']
    assert not item['evidence_resolution'].get('auto_resolutions')


def test_previous_semester_opening_is_not_current_just_because_fetched_later(tmp_path):
    db = Database(tmp_path/'db.sqlite3')
    iid, _ = add_task(db,'2030-10-26','Ausgabe AB2',closing_time=None)
    add_task(db,'2029-10-28','Geöffnet:\n28. Oktober 2029, 00:00',
             opening_time='00:00',fetched='2030-11-09T10:00:00+00:00')
    item = row(db,iid)
    assert item['conflict'] and item['date_conflict']
    assert not item['evidence_resolution'].get('auto_resolutions')


def test_personal_override_and_decision_keep_precedence_and_staleness(tmp_path):
    db = Database(tmp_path/'db.sqlite3')
    iid, _, _ = release_and_opening(db)
    view = comparison(db,iid,'opening')
    assert save(db,iid,'opening',view['token'],manual={'date':'2030-10-29','time':'12:00'})
    item = row(db,iid)
    assert (item['data']['date'],item['data']['time'])==('2030-10-29','12:00:00')
    assert not item['evidence_resolution']['auto_resolutions'][0]['effective']
    db.execute('UPDATE items SET overrides=?,status=? WHERE id=?',
               (encode({'date':'2030-10-30','time':'13:00:00'}),'done',iid))
    item = row(db,iid)
    assert (item['data']['date'],item['data']['time'],item['status'])==('2030-10-30','13:00:00','done')
    assert not item['evidence_resolution']['auto_resolutions'][0]['effective']
    add_task(db,'2030-10-31','Geöffnet:\nSamstag, 31. Oktober 2030, 00:00',opening_time='00:00')
    item = row(db,iid)
    assert item['personal_decisions'][0]['stale']
    assert (item['data']['date'],item['data']['time'],item['status'])==('2030-10-30','13:00:00','done')


def test_any_personal_time_correction_keeps_the_whole_existing_time_role(tmp_path,monkeypatch):
    import studycenter_public.core.comparison as module
    db = Database(tmp_path/'db.sqlite3')
    iid, _, _ = release_and_opening(db)
    db.execute('UPDATE items SET overrides=? WHERE id=?',
               (encode({'date':'2030-11-10','time':'12:00:00','end_date':None,'end_time':None}),iid))
    with monkeypatch.context() as context:
        context.setattr(module,'task_opening_roles',lambda *args:None)
        before = row(db,iid)
    after = row(db,iid)
    fields = ('date','time','end_date','end_time','date_meaning')
    assert {k:after['data'][k] for k in fields}=={k:before['data'][k] for k in fields}
    assert not after['evidence_resolution']['auto_resolutions'][0]['effective']


def test_distinct_values_not_repeated_sources_are_choices_and_one_click_is_guarded(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    db = app.state.db
    iid, _, _ = release_and_opening(db)
    add_task(db,'2030-10-30','Geöffnet:\nFreitag, 30. Oktober 2030, 00:00',opening_time='00:00')
    add_task(db,'2030-10-30','Geöffnet:\nFreitag, 30. Oktober 2030, 00:00',opening_time='00:00')
    view = comparison(db,iid,'opening')
    assert len(view['cards'])==3 and len(view['groups'])==2 and len(view['other_roles'])==1
    assert sum(len(g['support']) for g in view['groups'])==1
    client = TestClient(app)
    page = client.get(f'/items/{iid}/compare?fact=opening')
    assert page.status_code==200 and 'weitere Fundstellen mit derselben Angabe' in page.text
    assert 'Diese Angabe verwenden' in page.text and 'value="apply"' in page.text
    chosen = view['groups'][1]['card']
    data = {'fact':'opening','token':view['token'],'choice':chosen['key'],'action':'apply'}
    assert client.post(f'/items/{iid}/compare',data=data).status_code==403
    response = client.post(f'/items/{iid}/compare',data=data,
                           headers={'X-CSRF-Token':app.state.csrf},follow_redirects=False)
    assert response.status_code==303
    assert row(db,iid)['personal_decisions'][0]['value']==chosen['value']
    denied = client.post(f'/items/{iid}/compare',data=data,
                         headers={'X-CSRF-Token':app.state.csrf},follow_redirects=False)
    assert 'Bitte neu vergleichen' in client.get(denied.headers['location']).text
