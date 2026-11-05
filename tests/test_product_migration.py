import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from studycenter_public.legacy import create_legacy_router
from studycenter_public.migration import MigrationError, migrate_legacy
from studycenter_public.storage.db import Database, encode


def fixture(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    documents = data / 'documents'
    documents.mkdir()
    config = data / 'config'
    config.mkdir()
    (config / 'settings.json').write_text('{"theme":"dark"}', encoding='utf-8')
    paths = SimpleNamespace(data=data, database=data/'studycenter-public.sqlite3', documents=documents, config=config)
    db = Database(paths.database)
    blob = documents / 'synthetic.txt'
    blob.write_bytes(b'Synthetic quotation: deadline October 30.')
    digest = hashlib.sha256(blob.read_bytes()).hexdigest()
    with db.connection() as con:
        con.execute('INSERT INTO sources VALUES(1,?,?,?)', ('Synthetic source','web',encode({'url':'https://example.test/course','options':{}})))
        con.execute('INSERT INTO courses VALUES(2,?,?)', ('Synthetic course',encode({'source_id':1,'external_id':'course-2'})))
        con.execute('INSERT INTO documents VALUES(3,1,?,?,?,?,?,?,?,?,?)', ('https://example.test/doc','Synthetic document',digest,1,'2026-01-01',None,'text/plain',str(blob),encode({'processing_state':'success','analysis_state':'success','trusted_course_id':2})))
        con.execute('INSERT INTO pages VALUES(?,?,?,?)',(3,1,'Synthetic quotation: deadline October 30.','{}'))
        payload={'title':'Synthetic task','kind':'task','date':'2026-10-30','date_meaning':'deadline','obligation':'required','source_state':'active','description':'Source text'}
        con.execute('INSERT INTO items(id,identity,source_id,course_id,payload,overrides,status,created_at,updated_at) VALUES(4,?,?,?,?,?,?,?,?)',('item-4',1,2,encode(payload),encode({'title':'My correction'}),'done','2026-01-01','2026-01-02'))
        con.execute('INSERT INTO item_evidence VALUES(5,4,3,1,?,?,?,1)',('line-1','deadline October 30',encode(payload)))
        con.execute('INSERT INTO items(id,identity,payload,status,created_at,updated_at) VALUES(6,?,?,?,?,?)',('personal:abc',encode({'kind':'note','title':'My note','body':'Personal text'}),'open','2026-01-01','2026-01-02'))
        plan={'title':'Synthetic quest','mode':'custom','horizon':7,'daily_minutes':30,'start_date':'2026-10-01','planning':'paced','steps':[{'id':'step-1','title':'Learn','detail':'Practice','topic':'Synthetic','day_offset':0,'minutes':15,'done':True,'completed_at':'2026-10-01'}]}
        con.execute('INSERT INTO quest_plans VALUES(?,?,?,?,?,?,?,?)',('quest-1','request-1','fingerprint',encode(plan),'active',2,'2026-10-01','2026-10-02'))
        con.execute('INSERT INTO action_receipts VALUES(?,?,?,?,?,?)',('request','hash','{}','{}','{}','2026-10-01'))
    return paths


def test_migrate_preserves_ids_evidence_status_note_and_quest_and_is_idempotent(tmp_path):
    paths = fixture(tmp_path)
    before = paths.database.read_bytes()
    report = migrate_legacy(paths)
    assert report['state'] == 'migrated'
    assert migrate_legacy(paths) == report
    assert paths.database.read_bytes() == before
    core = sqlite3.connect(paths.data/'core'/'campusboard.sqlite3')
    assert core.execute('SELECT id,course_id,status FROM items WHERE id=4').fetchone() == (4,2,'done')
    assert json.loads(core.execute('SELECT overrides FROM items WHERE id=4').fetchone()[0])['title'] == 'My correction'
    assert core.execute('SELECT id,item_id,document_id,page,anchor,quote FROM item_evidence').fetchone() == (5,4,3,1,'line-1','deadline October 30')
    identity, note, status = core.execute('SELECT identity,payload,status FROM items WHERE id=6').fetchone()
    assert identity == 'note:beta4:abc' and json.loads(note)['description'] == 'Personal text' and status == 'open'
    quest = core.execute('SELECT state,version,steps FROM quests').fetchone()
    assert quest[:2] == ('active',2) and json.loads(quest[2])[0]['done'] is True
    original, = core.execute('SELECT original_path FROM documents').fetchone()
    assert open(original,'rb').read() == b'Synthetic quotation: deadline October 30.'
    core.close()
    from studycenter_public.core.db import Database as CoreDatabase
    projected = {item['id']: item for item in CoreDatabase(paths.data/'core'/'campusboard.sqlite3').items()}
    assert projected[4]['data']['title'] == 'My correction'
    assert projected[6]['data']['description'] == 'Personal text'
    app = FastAPI()
    app.include_router(create_legacy_router(paths))
    client = TestClient(app)
    assert 'deadline October 30' in client.get('/legacy?table=item_evidence').text
    assert 'request' in client.get('/legacy?table=action_receipts').text
    assert 'dark' in client.get('/legacy/settings').text
    (paths.documents/'synthetic.txt').unlink()
    assert client.get('/legacy/documents/3').content == b'Synthetic quotation: deadline October 30.'
    assert client.get('/legacy?table=sqlite_master').status_code == 400


def test_failed_migration_preserves_original_and_retry_succeeds(tmp_path):
    paths = fixture(tmp_path)
    db = Database(paths.database)
    with db.connection() as con:
        con.execute("INSERT INTO decisions VALUES(1,4,'date','\"2026-10-30\"',3,'hash','own decision','2026-01-01')")
    before = paths.database.read_bytes()
    with pytest.raises(MigrationError, match='Quellenentscheidungen'):
        migrate_legacy(paths)
    assert paths.database.read_bytes() == before
    assert not (paths.data/'core').exists()
    with db.connection() as con:
        con.execute('DELETE FROM decisions')
    assert migrate_legacy(paths)['state'] == 'migrated'


def test_missing_original_stops_without_publishing_and_unsafe_paths_rejected(tmp_path):
    paths = fixture(tmp_path)
    (paths.documents/'synthetic.txt').unlink()
    before = paths.database.read_bytes()
    with pytest.raises(MigrationError, match='fehlt'):
        migrate_legacy(paths)
    assert paths.database.read_bytes() == before
    assert not (paths.data/'core').exists()


def test_new_install_does_not_create_core(tmp_path):
    paths = SimpleNamespace(data=tmp_path, database=tmp_path/'missing.sqlite', documents=tmp_path/'documents', config=tmp_path/'config')
    assert migrate_legacy(paths) == {'state':'new'}
    assert not (tmp_path/'core').exists()



def test_source_bound_quest_blocks_instead_of_disabling_source_checks(tmp_path):
    paths = fixture(tmp_path)
    db = Database(paths.database)
    with db.connection() as con:
        row = con.execute('SELECT data FROM quest_plans').fetchone()
        data = json.loads(row['data'])
        data['source_snapshots'] = [{'item_id':4, 'documents':[{'id':3}]}]
        con.execute('UPDATE quest_plans SET data=?', (encode(data),))
    before = paths.database.read_bytes()
    with pytest.raises(MigrationError, match='Quellenbindung'):
        migrate_legacy(paths)
    assert paths.database.read_bytes() == before
    assert not (paths.data/'core').exists()


def test_outside_document_path_stops(tmp_path):
    paths = fixture(tmp_path)
    external = tmp_path/'outside.txt'
    external.write_text('Synthetic outside', encoding='utf-8')
    db = Database(paths.database)
    with db.connection() as con:
        con.execute('UPDATE documents SET original_path=?', (str(external),))
    before = paths.database.read_bytes()
    with pytest.raises(MigrationError):
        migrate_legacy(paths)
    assert paths.database.read_bytes() == before
    assert external.read_text(encoding='utf-8') == 'Synthetic outside'
    assert not (paths.data/'core').exists()


@pytest.mark.parametrize('item_id', [4, 6])
def test_planned_source_or_personal_state_is_explicit_personal_choice(tmp_path, item_id):
    paths = fixture(tmp_path)
    with Database(paths.database).connection() as con:
        con.execute("UPDATE items SET status='planned' WHERE id=?", (item_id,))
    before = paths.database.read_bytes()
    assert migrate_legacy(paths)['state'] == 'migrated'
    assert paths.database.read_bytes() == before
    from studycenter_public.core.db import Database as CoreDatabase
    from studycenter_public.core.presentation import personal_state
    item = next(item for item in CoreDatabase(paths.data/'core'/'campusboard.sqlite3').items() if item['id'] == item_id)
    assert item['status'] == 'open'
    assert item['reminder_profile']['personal'] is True
    assert personal_state(item)['personally_planned'] is True
    assert personal_state(item)['booking_proven'] is False


def test_planned_choice_survives_later_status_change_and_notice_explains_it(tmp_path):
    paths = fixture(tmp_path)
    with Database(paths.database).connection() as con:
        con.execute("UPDATE items SET status='planned' WHERE id=4")
    report = migrate_legacy(paths)
    assert 'Statuswechsel bestehen' in report['notice']
    from studycenter_public.core.db import Database as CoreDatabase
    from studycenter_public.core.presentation import personal_state
    core = CoreDatabase(paths.data/'core'/'campusboard.sqlite3')
    core.execute("UPDATE items SET status='done' WHERE id=4")
    assert personal_state(next(item for item in core.items() if item['id']==4))['personally_planned'] is True


def test_source_bound_quest_with_unchanged_snapshot_retains_core_change_checks(tmp_path):
    paths = fixture(tmp_path)
    db = Database(paths.database)
    with db.connection() as con:
        item = con.execute('SELECT payload FROM items WHERE id=4').fetchone()
        payload = json.loads(item['payload'])
        payload['date'] = '2099-10-30'
        con.execute("UPDATE items SET status='open',payload=? WHERE id=4", (encode(payload),))
    from studycenter_public.services.quests import QuestService as OldQuests
    snapshot, _ = OldQuests(db,None,None)._context(4, [], verify_original=False)
    with db.connection() as con:
        plan = json.loads(con.execute('SELECT data FROM quest_plans').fetchone()['data'])
        plan['item_ids'], plan['source_snapshots'] = [4], [snapshot]
        con.execute('UPDATE quest_plans SET data=?', (encode(plan),))
    assert migrate_legacy(paths)['state'] == 'migrated'
    from studycenter_public.core.db import Database as CoreDatabase
    from studycenter_public.core.quests import QuestService as CoreQuests
    core = CoreDatabase(paths.data/'core'/'campusboard.sqlite3')
    source = json.loads(core.one('SELECT sources FROM quests')['sources'])
    item = next(i for i in core.items() if i['id'] == 4)
    assert source[0]['id'] == 4 and source[0]['documents'][0]['id'] == 3
    assert not CoreQuests(core,None,None)._snapshot_changed([item],source)
    core.execute("UPDATE items SET overrides=? WHERE id=4", (encode({'title':'Changed later'}),))
    changed = next(i for i in core.items() if i['id'] == 4)
    assert CoreQuests(core,None,None)._snapshot_changed([changed],source)


def test_teachcenter_course_identity_matches_future_discovery(tmp_path):
    paths = fixture(tmp_path)
    db = Database(paths.database)
    with db.connection() as con:
        con.execute("UPDATE sources SET kind='teachcenter' WHERE id=1")
        con.execute('UPDATE courses SET metadata=? WHERE id=2', (encode({'source_id':1,'external_id':'42'}),))
    assert migrate_legacy(paths)['state'] == 'migrated'
    with sqlite3.connect(paths.data/'core'/'campusboard.sqlite3') as con:
        assert con.execute('SELECT id FROM courses WHERE external_id=?', ('tc:42',)).fetchone() == (2,)


def test_migrated_items_accept_correction_posts_and_reject_invalid_dates(tmp_path):
    paths = fixture(tmp_path)
    with Database(paths.database).connection() as con:
        meta = json.loads(con.execute('SELECT metadata FROM sources WHERE id=1').fetchone()['metadata'])
        meta['sync_status'] = {'state':'partial','gaps':['Synthetic OCR failure']}
        con.execute('UPDATE sources SET metadata=? WHERE id=1', (encode(meta),))
    assert migrate_legacy(paths)['state'] == 'migrated'
    paths.cache = paths.data/'cache'
    paths.profiles = paths.data/'profiles'
    paths.program = tmp_path/'program'
    paths.instance_id = '00000000-0000-4000-8000-000000000001'
    paths.host_id = 'synthetic-host'
    paths.logs = paths.data/'logs'
    for folder in (paths.cache,paths.profiles,paths.logs): folder.mkdir()
    from studycenter_public.product import create_app
    from studycenter_public.platform.secrets import SecretStore
    app = create_app(paths, SecretStore(paths.instance_id, session_only=True))
    client = TestClient(app)
    assert 'Synthetic OCR failure' in client.get('/sources').text
    headers = {'X-CSRF-Token':app.state.csrf, 'X-Requested-With':'fetch'}
    for item_id in (4,6):
        response = client.post(f'/items/{item_id}', headers=headers, data={'action':'correct','title':'Edited synthetic','description':'Edited text','date':'','time':'','end_date':'','end_time':''})
        assert response.status_code == 200, response.text
        invalid = client.post(f'/items/{item_id}', headers=headers, data={'action':'correct','title':'Edited synthetic','description':'Edited text','date':'not-a-date','time':'','end_date':'','end_time':''})
        assert invalid.status_code == 400
        assert json.loads(app.state.db.one('SELECT overrides FROM items WHERE id=?',(item_id,))['overrides'])['title'] == 'Edited synthetic'


def test_disabled_beta_reminders_do_not_become_enabled_in_core(tmp_path):
    paths = fixture(tmp_path)
    assert migrate_legacy(paths)['state'] == 'migrated'
    from studycenter_public.core.db import Database as CoreDatabase
    from studycenter_public.core.reminders import ReminderService
    core = CoreDatabase(paths.data/'core'/'campusboard.sqlite3')
    assert ReminderService(core, None).settings().enabled is False


def test_enabled_beta_reminders_stop_instead_of_changing_delivery_semantics(tmp_path):
    paths = fixture(tmp_path)
    with Database(paths.database).connection() as con:
        con.execute('INSERT INTO settings VALUES(?,?)', ('reminders', encode({'enabled':True})))
    before = paths.database.read_bytes()
    with pytest.raises(MigrationError, match='Erinnerungen'):
        migrate_legacy(paths)
    assert paths.database.read_bytes() == before and not (paths.data/'core').exists()


@pytest.mark.parametrize(('old_state','complete','gaps','expected'), [
    ('complete',True,[],'success'), ('complete',True,['OCR failed'],'partial'),
    ('complete',False,[],'partial'), ('cancelled',False,['Interrupted'],'partial'),
    ('running',False,[],'partial'), ('idle',False,[],'new'),
    ('failed',False,['Login failed'],'failed'), ('login_required',False,[],'login_required'),
])
def test_source_sync_report_and_state_remain_visible(tmp_path, old_state, complete, gaps, expected):
    paths = fixture(tmp_path)
    with Database(paths.database).connection() as con:
        meta = json.loads(con.execute('SELECT metadata FROM sources WHERE id=1').fetchone()['metadata'])
        meta['sync_status'] = {'state':old_state,'complete':complete,'gaps':gaps,'started_at':'2026-01-01','finished_at':'2026-01-02'}
        con.execute('UPDATE sources SET metadata=? WHERE id=1', (encode(meta),))
    assert migrate_legacy(paths)['state'] == 'migrated'
    with sqlite3.connect(paths.data/'core'/'campusboard.sqlite3') as con:
        state, message, last_attempt, last_success = con.execute('SELECT state,message,last_attempt,last_success FROM sources').fetchone()
        assert state == expected and all(gap in message for gap in gaps)
        assert last_attempt == '2026-01-01'
        assert (last_success is not None) == (expected == 'success')


def test_legacy_long_fields_are_fully_available_and_html_escaped(tmp_path):
    paths = fixture(tmp_path)
    with Database(paths.database).connection() as con:
        con.execute('INSERT INTO settings VALUES(?,?)', ('long', '<script>' + 'x'*50001 + 'END'))
    migrate_legacy(paths)
    app = FastAPI()
    app.include_router(create_legacy_router(paths))
    client = TestClient(app)
    listing = client.get('/legacy?table=settings').text
    assert 'Vollständiges Feld anzeigen' in listing and '<script>' not in listing
    result = client.get('/legacy/field', params={'table':'settings','row':1,'column':'value'})
    assert result.status_code == 200 and 'END' in result.text and '&lt;script&gt;' in result.text
    assert client.get('/legacy/field', params={'table':'settings','row':1,'column':'bad'}).status_code == 400


def test_source_ingest_after_migration_reuses_original_numeric_id(tmp_path):
    paths = fixture(tmp_path)
    migrate_legacy(paths)
    from studycenter_public.core.db import Database as CoreDatabase
    from studycenter_public.core.models import Candidate
    core = CoreDatabase(paths.data/'core'/'campusboard.sqlite3')
    item = core.one('SELECT payload FROM items WHERE id=4')
    candidate = Candidate.model_validate(json.loads(item['payload']))
    document = core.one('SELECT * FROM documents WHERE id=3')
    assert core.ingest(document, candidate) == 4
    assert len(core.rows('SELECT id FROM items WHERE id=4')) == 1
    assert core.one('SELECT count(*) n FROM items')['n'] == 2


def test_quote_not_in_retained_page_stops_migration(tmp_path):
    paths = fixture(tmp_path)
    with Database(paths.database).connection() as con:
        con.execute("UPDATE item_evidence SET quote='not in document'")
    before = paths.database.read_bytes()
    with pytest.raises(MigrationError, match='Belegzitat'):
        migrate_legacy(paths)
    assert paths.database.read_bytes() == before and not (paths.data/'core').exists()
