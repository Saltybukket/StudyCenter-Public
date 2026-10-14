from datetime import datetime, timezone
from uuid import uuid4
import pytest

from studycenter_public.storage.db import Database
from studycenter_public.services.actions import ActionService
from studycenter_public.services.reminders import ReminderService


@pytest.mark.asyncio
async def test_assistant_reminder_proposal_preview_apply_once_and_undo(tmp_path, monkeypatch):
    from studycenter_public.services.assistant import AssistantService
    from studycenter_public.providers.contracts import ModelResult
    import studycenter_public.services.assistant as module
    db = Database(tmp_path / 'db.sqlite3')
    item = db.add_item({'kind':'task','title':'Synthetic deadline'})
    action = {'type':'set_reminder','item_id':item['id'],'enabled':True,'offsets':[60,0]}
    class Profiles:
        def active_profile(self):
            return object()
    class Provider:
        async def generate(self, request):
            return ModelResult({'answer':'Reminder preview','actions':[action],'question':None,'choices':[],'citations':[]},True,'synthetic','synthetic')
    monkeypatch.setattr(module,'create_provider',lambda *args,**kwargs:Provider())
    assistant = AssistantService(db,ActionService(db),Profiles(),object())
    reminders = ReminderService(db)
    proposal = await assistant.respond('Remind me 60 minutes before this deadline')
    assert proposal['preview']['actions'] == [action]
    assert reminders.settings()['exceptions'] == {}
    receipt = assistant.apply(proposal['proposal_id'])
    assert assistant.apply(proposal['proposal_id']) == receipt
    assert reminders.settings()['exceptions'][str(item['id'])] == {'enabled':True,'offsets':[60,0]}
    undo_id = str(uuid4())
    undo = assistant.actions.undo(proposal['proposal_id'],undo_id)
    assert undo['state'] == 'undone'
    assert assistant.actions.undo(proposal['proposal_id'],undo_id) == undo
    assert reminders.settings()['exceptions'] == {}


def test_reminder_batch_conflict_rolls_back_both_items(tmp_path):
    db = Database(tmp_path / 'db.sqlite3')
    actions = ActionService(db)
    reminders = ReminderService(db)
    first = db.add_item({'kind':'task','title':'First'})
    second = db.add_item({'kind':'task','title':'Second'})
    batch = [{'type':'set_reminder','item_id':item['id'],'enabled':True,'offsets':[10]} for item in (first,second)]
    preview = actions.preview(batch)
    reminders.configure({'exceptions':{str(second['id']):{'enabled':True,'offsets':[20]}}})
    with pytest.raises(ValueError, match='version'):
        actions.execute(batch,str(uuid4()),preview['versions'])
    assert reminders.settings()['exceptions'] == {str(second['id']):{'enabled':True,'offsets':[20]}}
    assert db.item(first['id'])['version'] == first['version']


def test_later_personal_reminder_blocks_created_item_undo(tmp_path):
    db = Database(tmp_path / 'db.sqlite3')
    actions = ActionService(db)
    reminders = ReminderService(db)
    request_id = str(uuid4())
    receipt = actions.execute([{'type':'create_task','title':'Retained created task'}],request_id)
    item_id = receipt['item_ids'][0]
    reminders.configure({'exceptions':{str(item_id):{'enabled':True,'offsets':[30]}}})
    undo = actions.undo(request_id,str(uuid4()))
    assert undo['state'] == 'blocked'
    assert db.item(item_id)['title'] == 'Retained created task'
    assert reminders.settings()['exceptions'][str(item_id)]['offsets'] == [30]


def test_reminder_undo_preserves_later_title_and_other_item_exception(tmp_path):
    db = Database(tmp_path / 'db.sqlite3')
    actions = ActionService(db)
    reminders = ReminderService(db)
    first = db.add_item({'kind':'task','title':'First'})
    second = db.add_item({'kind':'task','title':'Second'})
    request_id = str(uuid4())
    actions.execute([{'type':'set_reminder','item_id':first['id'],'enabled':True,'offsets':[10]}],request_id)
    actions.execute([{'type':'update','item_id':first['id'],'fields':{'title':'Later retained title'}},
                     {'type':'set_reminder','item_id':second['id'],'enabled':False,'offsets':[]}],str(uuid4()))
    assert actions.undo(request_id,str(uuid4()))['state'] == 'undone'
    assert db.item(first['id'])['title'] == 'Later retained title'
    assert reminders.settings()['exceptions'] == {str(second['id']):{'enabled':False,'offsets':[]}}


def test_changed_reminder_blocks_entire_undo_batch(tmp_path):
    db = Database(tmp_path / 'db.sqlite3')
    actions = ActionService(db)
    reminders = ReminderService(db)
    first = db.add_item({'kind':'task','title':'First'})
    second = db.add_item({'kind':'task','title':'Second'})
    request_id = str(uuid4())
    actions.execute([{'type':'set_status','item_id':first['id'],'status':'done'},
                     {'type':'set_reminder','item_id':second['id'],'enabled':True,'offsets':[10]}],request_id)
    reminders.configure({'exceptions':{str(second['id']):{'enabled':True,'offsets':[20]}}})
    undo_id = str(uuid4())
    undo = actions.undo(request_id,undo_id)
    assert undo['state'] == 'blocked'
    assert actions.undo(request_id,undo_id) == undo
    assert db.item(first['id'])['status'] == 'done'
    assert reminders.settings()['exceptions'][str(second['id'])]['offsets'] == [20]
    assert all(not result['restored'] for result in undo['results'])


@pytest.mark.parametrize('offsets',[[True],[-1],[43201],[1,2,3,4,5,6],[1.5]])
def test_personal_reminder_action_rejects_invalid_offset_bound(tmp_path,offsets):
    db = Database(tmp_path / 'db.sqlite3')
    item = db.add_item({'kind':'task','title':'Task'})
    with pytest.raises(ValueError):
        ActionService(db).preview([{'type':'set_reminder','item_id':item['id'],'enabled':True,'offsets':offsets}])


def test_opening_only_bound_registration_reminds_and_closing_only_does_not(tmp_path):
    db = Database(tmp_path / 'db.sqlite3')
    reminders = ReminderService(db)
    reminders.configure({'enabled':True})
    source = db.upsert_source(name='Synthetic registrations',kind='web')
    text = 'Registration opens 2026-10-14 at 10:00. Registration closes 2026-10-14 at 10:00.'
    doc = db.save_document({'source_id':source['id'],'url':'https://example.org/registration'},pages=[{'page':1,'text':text}])
    common = {'kind':'registration','date':'2026-10-14','time':'10:00','date_meaning':'window','page':1}
    opening = db.ingest(doc, common | {'title':'Opening only','anchor':'opening','quote':'Registration opens 2026-10-14 at 10:00.'})
    db.ingest(doc, common | {'title':'Closing only','anchor':'closing','quote':'Registration closes 2026-10-14 at 10:00.'})
    db.add_item({'kind':'registration','title':'Unproven window','date':'2026-10-14','time':'10:00','date_meaning':'window'})
    db.add_item({'kind':'registration','title':'No guessed clock','date':'2026-10-14','date_meaning':'start'})
    jobs = reminders.due(datetime(2026,10,14,7,50,tzinfo=timezone.utc))
    assert [job['item_id'] for job in jobs] == [opening['id']]
    assert jobs[0]['due_at'] == '2026-10-14T09:50:00+02:00'


def test_mixed_opening_closing_proof_does_not_guess_single_date_role(tmp_path):
    db = Database(tmp_path / 'db.sqlite3')
    reminders = ReminderService(db)
    reminders.configure({'enabled':True})
    quote = 'Registration opens October 14 and registration closes October 20.'
    doc = db.save_document({'url':'https://example.org/registration'},pages=[{'page':1,'text':quote}])
    db.ingest(doc,{'kind':'registration','title':'Ambiguous window','date':'2026-10-14','time':'10:00','date_meaning':'window','page':1,'anchor':'window','quote':quote})
    assert reminders.due(datetime(2026,10,14,7,50,tzinfo=timezone.utc)) == []
