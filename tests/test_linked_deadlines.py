"""Synthetic personal starts follow only explicitly bound, matching source deadlines."""
from uuid import uuid4

import pytest

from studycenter_public.config import VIENNA
from studycenter_public.domain.presentation import overview_date
from studycenter_public.services.actions import ActionService
from studycenter_public.services.calendar_export import export_calendar
from studycenter_public.services.reminders import ReminderService
from studycenter_public.storage.db import Database


def setup_source(tmp_path):
    db=Database(tmp_path/'synthetic.sqlite3')
    source=db.upsert_source(name='Synthetic',kind='web')
    course=db.upsert_course(source['id'],'a','Synthetic course',{'semester':'2026W'})
    doc=db.save_document({'source_id':source['id'],'url':'https://example.test/task','hash':'a',
        'trusted_course_id':course['id'],'published_at':'2026-10-01'},pages=[{'page':1,'text':'Assignment 1 submission'}])
    value={'title':'Assignment 1','kind':'task','identity_scope':'course','identity_key':'a1',
           'quote':'Assignment 1 submission','page':1,'anchor':'a1','date_meaning':'window',
           'date':'2026-11-01','time':'08:00','end_date':'2026-11-12','end_time':'09:00',
           'source_state':'active','semester':'2026W'}
    return db,source,course,db.ingest(doc,value),value


def personal(db,course,original,title='Assignment 1 bearbeiten'):
    receipt=ActionService(db).execute([{'type':'create_task','title':title,'date':'2026-11-05',
        'date_meaning':'start','course_id':course['id'],'linked_source_id':original['id']}],str(uuid4()))
    return receipt['item_ids'][0]


def test_linked_source_closing_updates_without_overwriting_personal_start(tmp_path):
    db,source,course,original,value=setup_source(tmp_path)
    item_id=personal(db,course,original)
    item=db.item(item_id)
    assert item['date']=='2026-11-05' and overview_date(item)=='2026-11-12'
    assert ReminderService(db)._instant(item,VIENNA).isoformat().startswith('2026-11-12T09:00')
    assert 'DTSTART;TZID=Europe/Vienna:20261112T090000' in export_calendar([item],'test')
    new=db.save_document({'source_id':source['id'],'url':'https://example.test/task','hash':'b',
        'trusted_course_id':course['id'],'published_at':'2026-10-02'},pages=[{'page':1,'text':'Assignment 1 submission'}])
    db.ingest(new,value | {'end_date':'2026-11-13','end_time':'10:00'})
    current=db.item(item_id)
    assert current['date']=='2026-11-05' and current['linked_deadline']['date']=='2026-11-13'
    assert db.items()[-1]['linked_deadline']['time']=='10:00'
    assert current['status']=='open'


def test_wrong_identifier_does_not_promote_personal_start_to_deadline(tmp_path):
    db,_,course,original,_=setup_source(tmp_path)
    item=db.item(personal(db,course,original,title='Assignment 2 bearbeiten'))
    assert item['linked_deadline_unavailable'] and overview_date(item) is None
    assert ReminderService(db)._instant(item,VIENNA) is None
    assert 'BEGIN:VEVENT' not in export_calendar([item],'test')


def test_changed_current_source_identifier_cannot_use_old_fallback_title(tmp_path):
    db,_,course,original,_=setup_source(tmp_path)
    item_id=personal(db,course,original)
    with db.connection() as con:
        con.execute("UPDATE item_evidence SET payload=json_set(payload,'$.title','Assignment 2') WHERE item_id=?",(original['id'],))
    assert db.item(item_id)['linked_deadline_unavailable']


def test_link_requires_matching_course_and_current_source_evidence(tmp_path):
    db,source,course,original,_=setup_source(tmp_path)
    other=db.upsert_course(source['id'],'b','Other synthetic course')
    with pytest.raises(ValueError,match='selben Kurs'):
        personal(db,other,original)
    assert len(db.items())==1
    with pytest.raises(ValueError,match='selben Kurs'):
        ActionService(db).execute([{'type':'create_task','title':'Synthetic','linked_source_id':original['id']}],str(uuid4()))


def test_linked_source_change_after_preview_blocks_creation(tmp_path):
    db,source,course,original,value=setup_source(tmp_path)
    svc=ActionService(db)
    action={'type':'create_task','title':'Assignment 1 bearbeiten','date':'2026-11-05',
            'date_meaning':'start','course_id':course['id'],'linked_source_id':original['id']}
    preview=svc.preview([action])
    assert preview['versions'][str(original['id'])]==original['version']
    revised=db.save_document({'source_id':source['id'],'url':'https://example.test/task','hash':'b',
        'trusted_course_id':course['id'],'published_at':'2026-10-02'},pages=[{'page':1,'text':'Assignment 1 submission'}])
    db.ingest(revised,value | {'end_date':'2026-11-13'})
    with pytest.raises(ValueError,match='version changed'):
        svc.execute(preview['actions'],str(uuid4()),preview['versions'])
    assert len(db.items())==1


def test_old_semester_and_unknown_closing_time_are_not_invented(tmp_path):
    db,_,course,original,_=setup_source(tmp_path)
    item_id=personal(db,course,original)
    with db.connection() as con:
        con.execute("UPDATE item_evidence SET payload=json_set(payload,'$.semester','2025W') WHERE item_id=?",(original['id'],))
    assert db.item(item_id)['linked_deadline_unavailable']
    with db.connection() as con:
        con.execute("UPDATE item_evidence SET payload=json_set(payload,'$.semester','2026W','$.end_time',NULL) WHERE item_id=?",(original['id'],))
    item=db.item(item_id)
    assert item['linked_deadline']['date']=='2026-11-12' and item['linked_deadline']['time'] is None
    assert ReminderService(db)._instant(item,VIENNA) is None
    assert 'DTSTART;VALUE=DATE:20261112' in export_calendar([item],'test')


def test_linked_personal_task_supplies_source_evidence_to_quest(tmp_path,monkeypatch):
    import asyncio
    from studycenter_public.platform.secrets import SecretStore
    from studycenter_public.providers.contracts import ModelResult,ProviderProfile
    from studycenter_public.providers.settings import ProviderSettings
    from studycenter_public.services.quests import QuestService
    from test_quests import plan,request
    db,_,course,original,_=setup_source(tmp_path)
    item_id=personal(db,course,original)
    secrets=SecretStore(str(uuid4()),session_only=True)
    profiles=ProviderSettings(tmp_path/'providers.json',secrets)
    profiles.save_profile(ProviderProfile('openai','synthetic'))
    seen=[]
    class Provider:
        async def generate(self,req):seen.append(req.source_data);return ModelResult(plan(10),True,'synthetic','synthetic')
    monkeypatch.setattr('studycenter_public.services.quests.create_provider',lambda *a,**kw:Provider())
    svc=QuestService(db,profiles,secrets)
    draft=asyncio.run(svc.preview(request('checklist') | {'item_id':item_id}))['quest']
    assert draft['source_snapshot']['deadline']=='2026-11-12'
    assert draft['source_snapshot']['linked_source_id']==original['id']
    assert seen[0]['source_context']['item']['evidence'][0]['quote']=='Assignment 1 submission'
    assert db.item(item_id)['date']=='2026-11-05' and db.item(item_id)['status']=='open'
