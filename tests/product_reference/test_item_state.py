import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from studycenter_public.core.action_state import view_action_state
from studycenter_public.core.app import create_app
from studycenter_public.core.assistant import Action, Assistant, Command
from studycenter_public.core.backup import create_backup, inspect_archive
from studycenter_public.core.class_completion import ClassCompletionService
from .reference_settings import Settings, VIENNA
from studycenter_public.core.db import Database, encode, now
from studycenter_public.core.item_state import set_personal_field
from studycenter_public.core.models import Candidate
from studycenter_public.core.search import SearchIndex
from .test_assistant_everyday import apply, env, own, submit


@pytest.fixture
def board(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    db = app.state.db
    value = Candidate(title='Aufgabe', kind='task', date='2030-10-20', date_meaning='deadline',
                      obligation='required', quote='Eigene Planung', page=1, anchor='test')
    item_id = db.execute('INSERT INTO items(identity,payload,created_at,changed_at) VALUES(?,?,?,?)',
                         ('note:test', value.model_dump_json(), now(), now()))
    return app, db, item_id


def test_form_versions_retries_stale_writes_and_independent_fields(board):
    app, db, item_id = board
    client = TestClient(app)
    headers = {'X-CSRF-Token': app.state.csrf, 'X-Requested-With': 'fetch'}
    path = f'/items/{item_id}'
    pin = {'action': 'pin', 'expected_revision': '0', 'target': '1'}
    assert client.post(path, data=pin, headers=headers).status_code == 200
    assert client.post(path, data=pin, headers=headers).json()['revision'] == 1
    assert db.one('SELECT pinned,pin_revision FROM items WHERE id=?', (item_id,)) == {'pinned': 1, 'pin_revision': 1}

    # An unrelated source/reading update must not stale a personal status form.
    db.execute('UPDATE items SET conflict=1,seen=1 WHERE id=?', (item_id,))
    doing = {'action': 'status', 'status': 'doing', 'expected_revision': '0'}
    assert client.post(path, data=doing, headers=headers).status_code == 200
    assert client.post(path, data=doing, headers=headers).status_code == 200
    assert client.post(path, data=doing | {'status': 'done'}, headers=headers).status_code == 409
    # ABA still cannot make the old revision valid again.
    assert client.post(path, data={'action': 'status', 'status': 'open', 'expected_revision': '1'}, headers=headers).status_code == 200
    assert client.post(path, data=doing, headers=headers).status_code == 409
    before = db.one('SELECT * FROM items WHERE id=?', (item_id,))
    for bad in ('', '-1', '1.0', '9223372036854775808'):
        response = client.post(path, data={'action': 'status', 'status': 'done', 'expected_revision': bad}, headers=headers)
        assert response.status_code == 400
    assert client.post(path, data={'action': 'pin', 'expected_revision': '1'}, headers=headers).status_code == 400
    assert db.one('SELECT * FROM items WHERE id=?', (item_id,)) == before
    # The pre-existing tokenless contract remains usable.
    assert client.post(path, data={'action': 'status', 'status': 'done'}, headers=headers).status_code == 200


def test_two_competing_forms_cannot_overwrite_one_another(board):
    app, db, item_id = board
    gate = Barrier(2)
    def submit_status(status):
        client = TestClient(app)
        gate.wait(timeout=10)
        return client.post(f'/items/{item_id}', data={'action':'status', 'status':status, 'expected_revision':'0'},
                           headers={'X-CSRF-Token':app.state.csrf, 'X-Requested-With':'fetch'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit_status, status) for status in ('doing', 'done')]
        assert sorted(f.result(timeout=20) for f in futures) == [200, 409]
    assert db.one('SELECT status_revision FROM items WHERE id=?', (item_id,))['status_revision'] == 1


def test_correction_is_validated_and_guarded_in_same_transaction(board):
    app, db, item_id = board
    client = TestClient(app)
    headers = {'X-CSRF-Token': app.state.csrf, 'X-Requested-With': 'fetch'}
    path = f'/items/{item_id}'
    data = {'action':'correct', 'expected_revision':'0', 'title':'Meine Aufgabe', 'description':'Eigener Text',
            'date':'2030-10-21', 'time':'', 'end_date':'', 'end_time':''}
    assert client.post(path, data=data, headers=headers).status_code == 200
    assert client.post(path, data=data, headers=headers).status_code == 200
    before = db.one('SELECT * FROM items WHERE id=?', (item_id,))
    assert client.post(path, data=data | {'title':'Veralteter Text'}, headers=headers).status_code == 409
    assert client.post(path, data=data | {'expected_revision':'1', 'date':'ungültig'}, headers=headers).status_code == 400
    assert db.one('SELECT * FROM items WHERE id=?', (item_id,)) == before


@pytest.mark.asyncio
async def test_assistant_preview_and_undo_detect_change_then_revert(env):
    service, db, adapter, course, _ = env
    item_id = own(db, 'Aufgabe ABA', course)
    preview, result = await submit(service, adapter, 'Markiere diese Aufgabe erledigt.',
                                   {'action':'set_status', 'target_id':item_id, 'status':'done'}, context=item_id)
    assert result.get('preview')
    db.execute("UPDATE items SET status='doing' WHERE id=?", (item_id,))
    db.execute("UPDATE items SET status='open' WHERE id=?", (item_id,))
    _, stale = await apply(service, preview)
    assert stale.get('failed') and db.one('SELECT status FROM items WHERE id=?', (item_id,))['status'] == 'open'

    preview, result = await submit(service, adapter, 'Markiere diese Aufgabe erledigt.',
                                   {'action':'set_status', 'target_id':item_id, 'status':'done'}, context=item_id)
    accepted, result = await apply(service, preview)
    assert result['changed']
    db.execute("UPDATE items SET status='open' WHERE id=?", (item_id,))
    db.execute("UPDATE items SET status='done' WHERE id=?", (item_id,))
    with pytest.raises(ValueError, match='inzwischen'):
        await service.undo(accepted.request_id, True)
    assert db.one('SELECT status FROM items WHERE id=?', (item_id,))['status'] == 'done'


@pytest.mark.asyncio
async def test_duplicate_assistant_request_is_reserved_across_instances(env):
    service, db, adapter, course, _ = env
    item_id = own(db, 'Einmalige Aufgabe', course)
    other = Assistant(db, service.settings, adapter, SearchIndex(db), view_action_state)
    command = Command(request_id=uuid4(), text=f'Markiere Eintrag {item_id} erledigt.', preview=False)
    action = Action(action='set_status', target_id=item_id, status='done')
    first, second = await asyncio.gather(service.command(command, action), other.command(command, action))
    assert first.get('changed') or second.get('changed')
    assert db.one('SELECT status,status_revision FROM items WHERE id=?', (item_id,)) == {'status':'done', 'status_revision':1}
    assert db.one('SELECT count(*) n FROM assistant_requests WHERE id=?', (str(command.request_id),))['n'] == 1
    assert (await service.command(command, action)).get('changed')
    gate = Barrier(2)
    def undo(instance):
        gate.wait(timeout=10)
        return instance._undo(command.request_id, True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(undo, instance) for instance in (service, other)]
        results = [future.result(timeout=20) for future in futures]
    assert results[0] == results[1] and results[0]['undone']
    assert db.one('SELECT status FROM items WHERE id=?', (item_id,))['status'] == 'open'


def test_explicit_completion_survives_a_later_source_date_change(tmp_path):
    db = Database(tmp_path/'class.sqlite3')
    db.execute("INSERT INTO courses(id,title) VALUES(1,'CON')")
    value = Candidate(title='Vorlesung', kind='event', date='2026-10-20', time='09:00',
                      end_date='2026-10-20', end_time='10:00', date_meaning='start', obligation='required',
                      quote='Vorlesung', page=1, anchor='session')
    item_id = db.execute('INSERT INTO items(course_id,identity,payload,created_at,changed_at) VALUES(1,?,?,?,?)',
                         ('test:class', value.model_dump_json(), now(), now()))
    service = ClassCompletionService(db)
    assert service.tick(datetime(2026,10,21,tzinfo=VIENNA)) == [item_id]
    with db.connection() as con:
        con.execute('BEGIN IMMEDIATE')
        # A form rendered before automatic completion still expresses an
        # explicit personal confirmation, even though its target now matches.
        set_personal_field(con, item_id, 'status', 'done', expected_revision=0)
    db.execute('UPDATE items SET overrides=? WHERE id=?', (encode({'date':'2026-11-01','end_date':'2026-11-01'}), item_id))
    assert service.tick(datetime(2026,10,22,tzinfo=VIENNA)) == []
    assert db.one('SELECT status FROM items WHERE id=?', (item_id,))['status'] == 'done'
    assert db.one('SELECT state FROM class_completions WHERE item_id=?', (item_id,))['state'] == 'confirmed'


def test_backups_accept_old_items_and_reject_changed_revision_trigger(board, tmp_path):
    app, db, item_id = board
    db.execute('DROP TRIGGER item_revisions')
    db.execute('CREATE TRIGGER item_revisions AFTER UPDATE ON items BEGIN DELETE FROM items; END')
    archive = create_backup(db, tmp_path)
    with pytest.raises(ValueError, match='Datenbankstruktur'):
        inspect_archive(archive, tmp_path/'bad')
    db.execute('DROP TRIGGER item_revisions')
    for column in ('status_revision','pin_revision','visibility_revision','content_revision'):
        db.execute(f'ALTER TABLE items DROP COLUMN {column}')
    legacy = create_backup(db, tmp_path)
    inspect_archive(legacy, tmp_path/'legacy')
    restored = Database(tmp_path/'legacy'/'campusboard.sqlite3')
    assert restored.one('SELECT status,status_revision FROM items WHERE id=?', (item_id,)) == {'status':'open','status_revision':0}


@pytest.mark.asyncio
async def test_linked_deadline_has_same_date_in_ui_and_assistant(board):
    app, db, _ = board
    course = db.execute("INSERT INTO courses(title) VALUES('Messtechnik')")
    today = datetime.now(VIENNA).date()
    planned = today + timedelta(days=1)
    closing = today + timedelta(days=5)
    source = Candidate(title='Arbeitsblatt 1 bearbeiten', kind='task', date=closing,
                       date_meaning='deadline', obligation='optional', quote='Beleg', page=1, anchor='source')
    source_id = db.execute('INSERT INTO items(course_id,identity,payload,created_at,changed_at) VALUES(?,?,?,?,?)',
                           (course, 'source:1', source.model_dump_json(), now(), now()))
    note = Candidate(title='AB1 vorbereiten', kind='task', date=planned, date_meaning='start', obligation='required',
                     description=f'Originaleintrag http://127.0.0.1:8767/items/{source_id}', quote='Eigene Planung',
                     page=1, anchor='note')
    note_id = db.execute('INSERT INTO items(course_id,identity,payload,created_at,changed_at) VALUES(?,?,?,?,?)',
                         (course, 'note:linked', note.model_dump_json(), now(), now()))
    rows = {r['id']:r for r in db.items()}
    state = view_action_state(rows[note_id], datetime.now(VIENNA))
    assert state['action_deadline'] and state['action_date'] == closing.isoformat()
    async def query(day):
        command = Command(request_id=uuid4(), text='Zeige Abgaben')
        plan = Action(action='list_upcoming', kind='task', date_from=day, date_to=day, deadline_only=True)
        return await app.state.assistant.command(command, plan)
    result = await query(closing)
    assert [(r['id'],r['date']) for r in result['items']] == [(note_id, closing.isoformat())]
    assert (await query(planned))['items'] == []
    source_payload = source.model_dump(mode='json') | {'source_state':'cancelled'}
    db.execute('UPDATE items SET payload=? WHERE id=?', (encode(source_payload), source_id))
    assert (await query(closing))['items'] == []


def test_quiz_opening_closing_and_conflict_have_consistent_action_state():
    from studycenter_public.core.action_state import view_pin_state
    from studycenter_public.core.task_presentation import attach_task_displays
    current = datetime(2030,10,16,12,tzinfo=VIENNA)
    data = Candidate(title='Quiz 1', kind='exam', date='2030-10-15', time='08:00',
                     end_date='2030-10-17', end_time='09:00', date_meaning='window', obligation='required',
                     quote='Quizfenster', page=1, anchor='quiz').model_dump(mode='json')
    item = {'id':1, 'identity':'source:quiz', 'course_id':None, 'data':data, 'status':'open',
            'ignored':0, 'pinned':0, 'conflict':False, 'date_conflict':False}
    attach_task_displays([item], now=current)
    state = view_action_state(item, current)
    assert state['action_deadline'] and state['action_date'] == '2030-10-17' and state['action_time'] == '09:00:00'
    assert state['urgent'] and view_pin_state(item,current)['auto_pin']
    # The opening boundary is availability, not a second submission deadline.
    data.update(date='2030-10-16', time='13:00:00')
    attach_task_displays([item], now=current)
    state = view_action_state(item,current)
    assert state['available_later'] and not state['actionable'] and not state['urgent'] and state['action_date'] == '2030-10-17'
    item.update(conflict=True,date_conflict=True)
    attach_task_displays([item], now=current)
    state = view_action_state(item,current)
    assert not state['action_deadline'] and not state['urgent']


def test_conflicted_appointment_stays_visible_but_is_not_urgent():
    from studycenter_public.core.action_state import action_state, pin_state
    current = datetime(2030,10,16,12,tzinfo=VIENNA)
    data = Candidate(title='Pflichttermin', kind='event', date='2030-10-16', time='15:00',
                     date_meaning='start', obligation='required', quote='Termin', page=1, anchor='event').model_dump(mode='json')
    item = {'id':1, 'identity':'source:event', 'course_id':None, 'data':data, 'status':'open',
            'ignored':0, 'pinned':0, 'conflict':True, 'date_conflict':True}
    state = action_state(item,current)
    assert state['appointment'] and state['action_date'] == '2030-10-16' and not state['urgent']
    assert not pin_state(item,current)['auto_pin']
    item.update(conflict=False,date_conflict=False)
    assert action_state(item,current)['urgent']


@pytest.mark.parametrize('bulk', [False, True], ids=['single', 'bulk'])
@pytest.mark.parametrize('coverage', ['missing', 'partial'])
@pytest.mark.parametrize('changed_back', [False, True], ids=['revision-zero', 'aba'])
def test_legacy_field_undo_is_not_replayed_after_migration(env, bulk, coverage, changed_back):
    service, db, _, course, _ = env
    # Create a real pre-revision schema and receipts, then let the product migrate it.
    db.execute('DROP TRIGGER item_revisions')
    for column in ('status_revision','pin_revision','visibility_revision','content_revision'):
        db.execute(f'ALTER TABLE items DROP COLUMN {column}')
    first = own(db, 'Altbeleg eins', course)
    second = own(db, 'Altbeleg zwei', course)
    db.execute("UPDATE items SET status='done',pinned=1 WHERE id IN (?,?)", (first, second))
    identities = {r['id']:r['identity'] for r in db.rows('SELECT id,identity FROM items')}
    identifier = str(uuid4())
    if bulk:
        changes = [{'id':i,'identity':identities[i],'field':'status','before':'open','after':'done'}
                   for i in (first,second)]
        if coverage == 'partial':
            changes[0]['revision'] = 0
        result = {'changed':True,'undoable':True,'undo_bulk':changes}
    else:
        undo = {'identity':identities[first], 'fields':{'status':{'before':'open','after':'done'},
                                                       'pinned':{'before':0,'after':1}}}
        if coverage == 'partial':
            undo['revisions'] = {'status_revision':0}
        result = {'changed':True,'undoable':True,'item_id':first,'undo_change':undo}
    db.execute("INSERT INTO assistant_requests(id,input_hash,input,state,result,created_at) VALUES(?,?,'{}','completed',?,?)",
               (identifier,'legacy',encode(result),now()))
    db.initialize()
    if changed_back:
        db.execute("UPDATE items SET status='open' WHERE id IN (?,?)", (first,second))
        db.execute("UPDATE items SET status='done' WHERE id IN (?,?)", (first,second))
    before = db.rows('SELECT * FROM items ORDER BY id')
    receipt = db.one('SELECT * FROM assistant_requests WHERE id=?',(identifier,))
    with pytest.raises(ValueError, match='ältere|Ältere'):
        service._undo(identifier,True)
    assert db.rows('SELECT * FROM items ORDER BY id') == before
    assert db.one('SELECT * FROM assistant_requests WHERE id=?',(identifier,)) == receipt
    # Replaying a previously completed undo remains idempotent.
    undone = result | {'undone':True,'undoable':False}
    db.execute('UPDATE assistant_requests SET result=? WHERE id=?',(encode(undone),identifier))
    assert service._undo(identifier,True) == undone
    assert db.rows('SELECT * FROM items ORDER BY id') == before


def test_legacy_reminder_only_undo_without_field_revisions_still_works(env):
    service, db, _, course, _ = env
    item_id = own(db, 'Erinnerung', course)
    identifier = str(uuid4())
    db.execute('INSERT INTO personal_reminders(item_id,due_at,request_id) VALUES(?,?,?)',
               (item_id,'2030-10-20T10:00:00+00:00',identifier))
    reminder = db.one('SELECT * FROM personal_reminders WHERE item_id=?',(item_id,))
    identity = db.one('SELECT identity FROM items WHERE id=?',(item_id,))['identity']
    result = {'changed':True,'undoable':True,'item_id':item_id,
              'undo_change':{'identity':identity,'fields':{},'reminder':{'before':None,'after':reminder}}}
    db.execute("INSERT INTO assistant_requests(id,input_hash,input,state,result,created_at) VALUES(?,?,'{}','completed',?,?)",
               (identifier,'legacy-reminder',encode(result),now()))
    assert service._undo(identifier,True)['undone']
    assert db.one('SELECT * FROM personal_reminders WHERE item_id=?',(item_id,)) is None
