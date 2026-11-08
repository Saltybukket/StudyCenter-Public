import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from studycenter_public.core.class_completion import ClassCompletionService, session_end
from studycenter_public.core.db import Database
from studycenter_public.core.app import calendar_events

VIENNA = ZoneInfo('Europe/Vienna')


def make_item(db, title='Vorlesung', day='2026-10-20', **changes):
    payload = {'kind': 'event', 'title': title, 'date': day, 'time': '09:00:00', 'end_date': day,
               'end_time': '10:00:00', 'date_meaning': 'start', 'source_state': 'active',
               'obligation': 'required', 'group': None, 'uncertainty': None, 'description': ''}
    payload.update(changes)
    n = db.one('SELECT count(*) n FROM items')['n'] + 1
    return db.execute('INSERT INTO items(course_id,identity,payload,created_at,changed_at) VALUES(1,?,?,?,?)',
                      (f'test:{n}', json.dumps(payload), '2026-01-01', '2026-01-01'))


@pytest.fixture
def db(tmp_path):
    db = Database(tmp_path / 'data.sqlite3')
    db.execute("INSERT INTO courses(id,title) VALUES(1,'CON')")
    return db


def test_midnight_reopen_and_calendar(db):
    item = make_item(db)
    service = ClassCompletionService(db)
    assert service.tick(datetime(2026, 10, 20, 23, 59, tzinfo=VIENNA)) == []
    assert service.tick(datetime(2026, 10, 21, 0, 0, tzinfo=VIENNA)) == [item]
    row = db.items()[0]
    assert row['status'] == 'done'
    assert calendar_events([row])[0]['extendedProps']['status'] == 'done'
    db.execute("UPDATE items SET status='open' WHERE id=?", (item,))
    assert service.tick(datetime(2026, 10, 22, tzinfo=VIENNA)) == []
    assert db.items()[0]['status'] == 'open'
    assert db.one('SELECT state FROM class_completions WHERE item_id=?', (item,))['state'] == 'reopened'


def test_dst_and_multiday(db):
    item = make_item(db, day='2026-10-24', end_date='2026-10-25')
    service = ClassCompletionService(db)
    assert service.tick(datetime(2026, 10, 25, 22, 59, tzinfo=timezone.utc)) == []
    assert service.tick(datetime(2026, 10, 25, 23, 0, tzinfo=timezone.utc)) == [item]


def test_corrected_future_date_reverts_only_automatic_status(db):
    item = make_item(db)
    service = ClassCompletionService(db)
    service.tick(datetime(2026, 10, 21, tzinfo=VIENNA))
    db.execute('UPDATE items SET overrides=? WHERE id=?', (json.dumps({'date': '2026-11-01', 'end_date': '2026-11-01'}), item))
    assert service.tick(datetime(2026, 10, 22, tzinfo=VIENNA)) == [item]
    assert db.items()[0]['status'] == 'open'
    assert service.tick(datetime(2026, 11, 2, tzinfo=VIENNA)) == [item]


@pytest.mark.parametrize('changes', [
    {'kind': 'task'}, {'kind': 'exam'}, {'kind': 'registration'}, {'kind': 'notice'},
    {'title': 'Vorbereitung Vorlesung'}, {'title': 'Prüfung Vorlesung'},
    {'title': 'Quiz zur Übung'}, {'title': 'Arzttermin'}, {'source_state': 'cancelled'},
    {'date_meaning': 'unclear'}, {'date': None}, {'group': 'Fremdgruppe'},
    {'obligation': 'optional'}, {'title': 'Lab 1'},
])
def test_exclusions(db, changes):
    item = make_item(db, **changes)
    assert ClassCompletionService(db).tick(datetime(2026, 10, 22, tzinfo=VIENNA)) == []
    assert db.one('SELECT status FROM items WHERE id=?', (item,))['status'] == 'open'


def test_generic_vu_requires_personal_and_course_type(db):
    item = make_item(db, title='CON · Lehrveranstaltung')
    row = db.items()[0]
    assert session_end(row, {'VU'}) is None
    row['personal_event'] = True
    assert session_end(row, {'VU'}).isoformat() == '2026-10-20'
    assert session_end(row, {'LU'}) is None
    assert session_end(row, set()) is None
    assert session_end(row, {'VU', 'UE'}) is None


def test_real_calendar_evidence_generic_vu_and_source_resync(db):
    source = db.execute("INSERT INTO sources(course_id,kind,title,url,config) VALUES(1,'tugrazonline','Kalender','https://example.org/cal',?)", (json.dumps({'course_type': 'VU'}),))
    doc = db.execute("INSERT INTO documents(source_id,url,title,mime,hash,fetched_at,original_path) VALUES(?, 'https://example.org/cal', 'Kalender', 'text/calendar', 'hash', '2026-10-20', '')", (source,))
    item = make_item(db, title='CON · Lehrveranstaltung', group='Standardgruppe', identity_key='tugrazonline:uid')
    payload = db.one('SELECT payload FROM items WHERE id=?', (item,))['payload']
    db.execute("INSERT INTO item_evidence(item_id,document_id,page,anchor,quote,payload) VALUES(?,?,1,'uid','Termin',?)", (item, doc, payload))
    assert db.items()[0]['personal_event']
    service = ClassCompletionService(db)
    assert service.tick(datetime(2026, 10, 21, tzinfo=VIENNA)) == [item]
    db.execute('UPDATE items SET payload=? WHERE id=?', (payload, item))
    assert db.items()[0]['status'] == 'done'
    assert service.revision == 1


@pytest.mark.asyncio
async def test_service_gate_and_stop(db):
    import asyncio
    service = ClassCompletionService(db)
    make_item(db)
    task = asyncio.create_task(service.run(lambda: False))
    await asyncio.sleep(0)
    assert service.running
    assert db.items()[0]['status'] == 'open'
    service.stop.set()
    await task
    assert not service.running


def test_completed_classes_leave_board_but_remain_calendar(tmp_path):
    from fastapi.testclient import TestClient
    from studycenter_public.core.app import create_app
    from .reference_settings import Settings
    app=create_app(Settings(data_dir=tmp_path))
    db=app.state.db
    db.execute("INSERT INTO courses(id,title) VALUES(1,'CON')")
    old=make_item(db,title='Vergangene Vorlesung',day='2026-10-20')
    task=make_item(db,title='Offene Vorbereitung',day='2026-10-20',kind='task',date_meaning='deadline')
    today=make_item(db,title='Heutige Übung',day='2026-10-21')
    client=TestClient(app)
    assert 'Vergangene Vorlesung' in client.get('/board?lectures=true').text
    assert app.state.class_completion.tick(datetime(2026,10,21,23,59,tzinfo=VIENNA))==[old]
    board=client.get('/board?lectures=true').text
    assert 'Vergangene Vorlesung' not in board
    assert 'Offene Vorbereitung' in board and 'Heutige Übung' in board
    event=next(e for e in client.get('/api/calendar').json() if e['id']==str(old))
    assert event['extendedProps']['status']=='done' and event['start'].startswith('2026-10-20')
    assert 'Vergangene Vorlesung' in client.get('/archive').text
    assert db.one('SELECT status FROM items WHERE id=?',(task,))['status']=='open'
    assert db.one('SELECT status FROM items WHERE id=?',(today,))['status']=='open'


def test_backup_restores_completion_ledger_and_reopen_trigger(db, tmp_path):
    import shutil
    import zipfile
    from studycenter_public.core.backup import apply_pending, create_backup, inspect_archive

    (tmp_path / 'documents').mkdir()
    item = make_item(db)
    ClassCompletionService(db).tick(datetime(2026, 10, 21, tzinfo=VIENNA))
    archive = create_backup(db, tmp_path)
    assert inspect_archive(archive, tmp_path / 'checked')['counts']['class_completions'] == 1
    # Archives already created by the previous version contain the ledger but
    # omit its manifest count; their original data must remain recoverable.
    old_archive = tmp_path / 'old.zip'
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(old_archive, 'w') as target:
        for name in source.namelist():
            data = source.read(name)
            if name == 'manifest.json':
                manifest = json.loads(data)
                del manifest['counts']['class_completions']
                data = json.dumps(manifest).encode()
            target.writestr(name, data)
    inspect_archive(old_archive, tmp_path / 'old-checked')
    db.execute("UPDATE items SET status='open' WHERE id=?", (item,))
    shutil.copyfile(archive, tmp_path / 'restore-pending.zip')
    apply_pending(db, tmp_path)
    assert db.one('SELECT state FROM class_completions WHERE item_id=?', (item,))['state'] == 'automatic'
    db.execute("UPDATE items SET status='open' WHERE id=?", (item,))
    assert db.one('SELECT state FROM class_completions WHERE item_id=?', (item,))['state'] == 'reopened'


def test_backup_rejects_altered_completion_trigger_and_accepts_legacy(db, tmp_path):
    from studycenter_public.core.backup import create_backup, inspect_archive

    db.execute('DROP TRIGGER class_completion_reopened')
    db.execute('CREATE TRIGGER class_completion_reopened AFTER UPDATE OF status ON items BEGIN DELETE FROM items; END')
    archive = create_backup(db, tmp_path)
    with pytest.raises(ValueError, match='Datenbankstruktur'):
        inspect_archive(archive, tmp_path / 'altered')
    db.execute('DROP TRIGGER class_completion_reopened')
    db.execute('DROP TABLE class_completions')
    legacy = create_backup(db, tmp_path)
    assert 'class_completions' not in inspect_archive(legacy, tmp_path / 'legacy')['counts']
