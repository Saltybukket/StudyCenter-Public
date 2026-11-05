"""Local completion must preserve personal status and source uncertainty."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from studycenter_public.config import VIENNA
from studycenter_public.services.actions import ActionService
from studycenter_public.services.class_completion import ClassCompletionService
from studycenter_public.storage.db import Database


@pytest.fixture
def setup(tmp_path):
    db=Database(tmp_path/'synthetic.sqlite3')
    source=db.upsert_source(name='Synthetic',kind='public')
    course=db.upsert_course(source['id'],'test','Synthetic',{'course_type':'VU','group':'Gruppe A'})
    doc=db.save_document({'source_id':source['id'],'url':'https://example.test/course',
        'trusted_course_id':course['id']},pages=[{'page':1,'text':'Synthetic course session.'}])
    return db,doc


def create(db,doc,**changes):
    value={'title':'Vorlesung','kind':'event','date':'2026-10-24','time':'09:00',
           'end_date':'2026-10-25','end_time':'10:00','date_meaning':'start',
           'source_state':'active','obligation':'required','quote':'Synthetic course session.',
           'page':1,'anchor':str(uuid4())}
    return db.ingest(doc,value|changes)


def test_after_vienna_day_end_manual_reopen_and_calendar_retention(setup):
    db,doc=setup
    item=create(db,doc)
    service=ClassCompletionService(db)
    assert service.tick(datetime(2026,10,25,22,59,tzinfo=timezone.utc)) == []
    assert service.tick(datetime(2026,10,25,23,0,tzinfo=timezone.utc)) == [item['id']]
    assert db.item(item['id'])['status']=='done'
    assert any(row['id']==item['id'] for row in db.items())
    ActionService(db).execute([{'type':'set_status','item_id':item['id'],'status':'open'}],str(uuid4()))
    assert service.tick(datetime(2026,10,27,tzinfo=VIENNA)) == []
    assert db.item(item['id'])['status']=='open'


def test_manual_status_before_automatic_completion_is_preserved_and_undo_restores_eligibility(setup):
    db,doc=setup
    item=create(db,doc)
    actions=ActionService(db)
    request_id=str(uuid4())
    actions.execute([{'type':'set_status','item_id':item['id'],'status':'planned'}],request_id)
    service=ClassCompletionService(db)
    instant=datetime(2026,10,27,tzinfo=VIENNA)
    assert service.tick(instant)==[] and db.item(item['id'])['status']=='planned'
    undone=actions.undo(request_id,str(uuid4()))
    assert undone['state']=='undone' and db.item(item['id'])['status']=='open'
    assert service.tick(instant)==[item['id']] and db.item(item['id'])['status']=='done'


def test_repeated_identical_manual_status_blocks_older_undo(setup):
    db,doc=setup;item=create(db,doc);actions=ActionService(db)
    first=str(uuid4());second=str(uuid4())
    actions.execute([{'type':'set_status','item_id':item['id'],'status':'open'}],first)
    actions.execute([{'type':'set_status','item_id':item['id'],'status':'open'}],second)
    assert actions.undo(first,str(uuid4()))['state']=='blocked'
    instant=datetime(2026,10,27,tzinfo=VIENNA);service=ClassCompletionService(db)
    assert service.tick(instant)==[] and db.item(item['id'])['status']=='open'
    assert actions.undo(second,str(uuid4()))['state']=='undone'
    assert actions.undo(first,str(uuid4()))['state']=='undone'
    assert service.tick(instant)==[item['id']] and db.item(item['id'])['status']=='done'


def test_legacy_recorded_personal_status_is_preserved_after_upgrade(setup):
    db,doc=setup
    item=create(db,doc);actions=ActionService(db)
    first=str(uuid4());second=str(uuid4())
    actions.execute([{'type':'set_status','item_id':item['id'],'status':'planned'}],first)
    actions.execute([{'type':'set_status','item_id':item['id'],'status':'open'}],second)
    path=f'$."{item["id"]}".completion'
    with db.connection() as con:
        con.execute('DELETE FROM class_completions WHERE item_id=?',(item['id'],))
        con.execute('UPDATE action_receipts SET before_state=json_remove(before_state,?),after_state=json_remove(after_state,?) WHERE request_id IN (?,?)',(path,path,first,second))
    assert ClassCompletionService(db).tick(datetime(2026,10,27,tzinfo=VIENNA))==[]
    assert db.item(item['id'])['status']=='open'


@pytest.mark.parametrize('action,expected',[
    ({'type':'set_status','status':'open'},'open'),
    ({'type':'update','fields':{'pinned':True}},'done'),
])
def test_legacy_noop_status_is_distinguished_from_unrelated_edit(setup,action,expected):
    db,doc=setup
    item=create(db,doc);request_id=str(uuid4())
    ActionService(db).execute([action | {'item_id':item['id']}],request_id)
    path=f'$."{item["id"]}".completion'
    with db.connection() as con:
        con.execute('DELETE FROM class_completions WHERE item_id=?',(item['id'],))
        con.execute('UPDATE action_receipts SET before_state=json_remove(before_state,?),after_state=json_remove(after_state,?) WHERE request_id=?',(path,path,request_id))
    ClassCompletionService(db).tick(datetime(2026,10,27,tzinfo=VIENNA))
    assert db.item(item['id'])['status']==expected


def test_source_losing_eligibility_reverts_only_automatic_completion(setup):
    db,doc=setup
    item=create(db,doc);service=ClassCompletionService(db);actions=ActionService(db)
    instant=datetime(2026,10,27,tzinfo=VIENNA)
    assert service.tick(instant)==[item['id']] and db.item(item['id'])['status']=='done'
    actions.execute([{'type':'update','item_id':item['id'],'fields':{'source_state':'cancelled'}}],str(uuid4()))
    assert service.tick(instant)==[item['id']] and db.item(item['id'])['status']=='open'
    assert service.tick(instant)==[]


def test_future_date_correction_reverses_only_automatic_status(setup):
    db,doc=setup
    item=create(db,doc)
    service=ClassCompletionService(db)
    service.tick(datetime(2026,10,27,tzinfo=VIENNA))
    actions=ActionService(db)
    actions.execute([{'type':'update','item_id':item['id'],'fields':{'date':'2026-11-01','end_date':'2026-11-01'}}],str(uuid4()))
    assert service.tick(datetime(2026,10,27,tzinfo=VIENNA)) == [item['id']]
    assert db.item(item['id'])['status']=='open'
    assert service.tick(datetime(2026,11,2,tzinfo=VIENNA)) == [item['id']]
    actions.execute([{'type':'set_status','item_id':item['id'],'status':'done'}],str(uuid4()))
    actions.execute([{'type':'update','item_id':item['id'],'fields':{'date':'2026-11-05','end_date':'2026-11-05'}}],str(uuid4()))
    assert service.tick(datetime(2026,11,2,tzinfo=VIENNA)) == []
    assert db.item(item['id'])['status']=='done'


@pytest.mark.parametrize('changes', [
    {'kind':'task'}, {'kind':'exam'}, {'title':'Vorbereitung Vorlesung'},
    {'title':'Prüfung Vorlesung'}, {'title':'Laborübung'}, {'obligation':'optional'},
    {'source_state':'unclear'}, {'group':'Fremdgruppe'}, {'date_meaning':'unclear'},
    {'group':'Gruppe A'},
    {'uncertainty':'Kein persönlicher Slot bestätigt'},
])
def test_uncertain_non_session_or_other_group_does_not_complete(setup,changes):
    db,doc=setup
    item=create(db,doc,**changes)
    assert ClassCompletionService(db).tick(datetime(2026,10,27,tzinfo=VIENNA)) == []
    assert db.item(item['id'])['status']=='open'


def test_existing_database_gets_additive_migration(setup):
    db,_=setup
    db.execute('DROP TABLE class_completions')
    reopened=Database(db.path)
    assert reopened.one("SELECT name FROM sqlite_master WHERE type='table' AND name='class_completions'")


def test_confirmed_course_group_does_not_imply_lab_booking(setup):
    db,doc=setup
    course=db.course(doc['trusted_course_id'])
    db.upsert_course(doc['source_id'],course['external_id'],course['title'],{'confirmed':True})
    ordinary=create(db,doc,group='Gruppe A')
    lab=create(db,doc,title='Laborübung',group='Gruppe A')
    assert ClassCompletionService(db).tick(datetime(2026,10,27,tzinfo=VIENNA)) == [ordinary['id']]
    assert db.item(lab['id'])['status']=='open'
    assert not db.item(lab['id'])['booked_event']


def test_completed_class_leaves_board_but_remains_in_calendar(tmp_path):
    from test_web import client
    c=client(tmp_path)
    db=c.app.state.db
    source=db.upsert_source(name='Synthetic',kind='public')
    course=db.upsert_course(source['id'],'test','Synthetic',{'course_type':'VU'})
    doc=db.save_document({'source_id':source['id'],'url':'https://example.test/course',
        'trusted_course_id':course['id']},pages=[{'page':1,'text':'Synthetic course session.'}])
    item=create(db,doc)
    assert 'Vorlesung' in c.get('/board').text
    c.app.state.class_completion.tick(datetime(2026,10,27,tzinfo=VIENNA))
    assert 'Vorlesung' not in c.get('/board').text
    assert 'Vorlesung' in c.get('/calendar?month=2026-10').text
    assert 'Vorlesung' in c.get('/archive').text
    assert db.item(item['id'])['status']=='done'


def test_completion_failure_is_visible(tmp_path):
    from test_web import client
    c=client(tmp_path)
    c.app.state.class_completion.error='synthetic failure'
    assert 'konnten nicht automatisch aktualisiert werden' in c.get('/board').text
    assert 'synthetic failure' not in c.get('/board').text


def test_restoring_older_backup_migrates_before_next_local_tick(tmp_path):
    from studycenter_public.platform.paths import AppPaths
    from studycenter_public.services.backup import BackupService
    paths=AppPaths.create(tmp_path/'synthetic-data')
    db=Database(paths.database)
    db.execute('DROP TABLE class_completions')
    backup=BackupService(db,paths).create()
    Database(paths.database)
    assert BackupService(db,paths).restore(backup,confirmed=True)['state']=='restored'
    assert ClassCompletionService(db).tick(datetime(2026,10,27,tzinfo=VIENNA)) == []


def test_repeated_ticks_only_project_changed_items(setup,monkeypatch):
    db,doc=setup
    item=create(db,doc)
    service=ClassCompletionService(db)
    original=db._item
    calls=[]
    def counted(*args,**kwargs):
        calls.append(args[0])
        return original(*args,**kwargs)
    monkeypatch.setattr(db,'_item',counted)
    instant=datetime(2026,10,24,tzinfo=VIENNA)
    service.tick(instant)
    assert calls==[item['id']]
    calls.clear()
    service.tick(instant)
    assert calls==[]
    ActionService(db).execute([{'type':'set_status','item_id':item['id'],'status':'planned'}],str(uuid4()))
    calls.clear()
    service.tick(instant)
    assert calls==[item['id']]
    calls.clear()
    service.tick(datetime(2026,10,25,tzinfo=VIENNA))
    assert calls==[item['id']]


def test_course_confirmation_change_rechecks_same_day(setup):
    db,doc=setup
    item=create(db,doc,group='Gruppe A')
    service=ClassCompletionService(db)
    instant=datetime(2026,10,27,tzinfo=VIENNA)
    assert service.tick(instant)==[]
    course=db.course(doc['trusted_course_id'])
    db.upsert_course(doc['source_id'],course['external_id'],course['title'],{'confirmed':True})
    assert service.tick(instant)==[item['id']]


def test_invalidation_during_tick_does_not_restore_stale_cache(setup,monkeypatch):
    from contextlib import contextmanager
    from threading import Event,Thread
    db,_=setup
    original=db.connection
    committed,resume=Event(),Event()
    @contextmanager
    def pause_after_commit():
        with original() as con:
            yield con
        committed.set()
        assert resume.wait(5)
    monkeypatch.setattr(db,'connection',pause_after_commit)
    service=ClassCompletionService(db)
    errors=[]
    def worker():
        try:service.tick(datetime(2026,10,27,tzinfo=VIENNA))
        except Exception as exc:errors.append(exc)
    thread=Thread(target=worker);thread.start()
    try:
        assert committed.wait(5)
        service.invalidate()
    finally:
        resume.set();thread.join(5)
    assert not thread.is_alive() and not errors
    assert service._day is None and service._versions=={}
