"""Offline synthetic personal-goal plans and receipt semantics."""
import asyncio
from uuid import uuid4

import pytest

from studycenter_public.platform.secrets import SecretStore
from studycenter_public.providers.contracts import ModelResult,ProviderProfile
from studycenter_public.providers.settings import ProviderSettings
from studycenter_public.services.quests import QuestService
from studycenter_public.storage.db import Database


def service(tmp_path,monkeypatch,outputs):
    db=Database(tmp_path/'synthetic.sqlite3')
    secrets=SecretStore(str(uuid4()),session_only=True)
    profiles=ProviderSettings(tmp_path/'providers.json',secrets)
    profiles.save_profile(ProviderProfile('openai','synthetic'))
    seen=[]
    class Provider:
        async def generate(self,request):
            seen.append(request.source_data)
            return ModelResult(outputs.pop(0),True,'synthetic','synthetic')
    monkeypatch.setattr('studycenter_public.services.quests.create_provider',lambda *a,**kw:Provider())
    return db,QuestService(db,profiles,secrets),seen


def plan(*minutes):
    return {'title':'Synthetic learning','steps':[{'title':f'Step {index}','detail':'Explain and check.',
        'topic':'Synthetic concept','day_offset':0,'minutes':value} for index,value in enumerate(minutes)]}


def request(planning='paced'):
    return {'request_id':str(uuid4()),'goal':'Learn synthetic concepts','planning':planning,'horizon':7,'daily_minutes':30}


def test_budget_repair_persistence_idempotence_and_undo(tmp_path,monkeypatch):
    db,svc,seen=service(tmp_path,monkeypatch,[plan(25,25),plan(15,15)])
    inp=request()
    draft=asyncio.run(svc.preview(inp))['quest']
    assert len(seen)==2 and seen[1]['correction']['previous_plan']['steps'][0]['minutes']==25
    assert asyncio.run(svc.preview(inp))['quest']==draft and len(seen)==2
    assert db.items()==[] and draft['state']=='draft'
    action={'request_id':str(uuid4()),'action':'activate','version':draft['version'],'title':'Edited goal'}
    activated=svc.execute(draft['id'],action)
    assert svc.execute(draft['id'],action)==activated
    assert activated['quest']['title']=='Edited goal'
    step=activated['quest']['steps'][0]
    mutation={'request_id':str(uuid4()),'action':'step','version':activated['quest']['version'],'step_id':step['id'],'done':True}
    changed=svc.execute(draft['id'],mutation)
    assert changed['quest']['completed']==1 and changed['quest']['xp']==10
    undo_id=str(uuid4());undo=svc.undo(mutation['request_id'],undo_id)
    assert undo['state']=='undone' and undo['quest']['completed']==0
    assert svc.undo(mutation['request_id'],undo_id)==undo
    with pytest.raises(ValueError,match='geändert'):
        svc.execute(draft['id'],mutation | {'request_id':str(uuid4())})


def test_undo_conflict_does_not_rewrite_later_progress(tmp_path,monkeypatch):
    _,svc,_=service(tmp_path,monkeypatch,[plan(10,10)])
    draft=asyncio.run(svc.preview(request()))['quest']
    activate={'request_id':str(uuid4()),'action':'activate','version':1}
    active=svc.execute(draft['id'],activate)['quest']
    svc.execute(draft['id'],{'request_id':str(uuid4()),'action':'step','version':active['version'],
        'step_id':active['steps'][0]['id'],'done':True})
    result=svc.undo(activate['request_id'],str(uuid4()))
    assert result['state']=='blocked' and svc.list()[0]['completed']==1


def test_checklist_has_no_daily_budget_and_can_tick_immediately(tmp_path,monkeypatch):
    _,svc,_=service(tmp_path,monkeypatch,[plan(30,30)])
    draft=asyncio.run(svc.preview(request('checklist')))['quest']
    active=svc.execute(draft['id'],{'request_id':str(uuid4()),'action':'activate','version':1})['quest']
    changed=svc.execute(draft['id'],{'request_id':str(uuid4()),'action':'step','version':active['version'],
        'step_id':active['steps'][1]['id'],'done':True})['quest']
    assert changed['completed']==1 and changed['planning']=='checklist'


def test_repeated_overbudget_never_persists_and_unknown_fields_fail(tmp_path,monkeypatch):
    db,svc,_=service(tmp_path,monkeypatch,[plan(30,30),plan(30,30)])
    with pytest.raises(ValueError,match='Tagesbudget'):
        asyncio.run(svc.preview(request()))
    assert db.rows('SELECT * FROM quest_plans')==[]
    with pytest.raises(ValueError):asyncio.run(svc.preview(request() | {'document_id':3}))


def test_http_quest_preview_actions_undo_and_backup(tmp_path,monkeypatch):
    from test_web import client
    from studycenter_public.services.backup import BackupService
    c=client(tmp_path);app=c.app
    app.state.profiles.save_profile(ProviderProfile('openai','synthetic'))
    class Provider:
        async def generate(self,req):return ModelResult(plan(15,15),True,'synthetic','synthetic')
    monkeypatch.setattr('studycenter_public.services.quests.create_provider',lambda *a,**kw:Provider())
    page=c.get('/quests')
    assert page.status_code==200 and 'name="document_ids"' in page.text
    inp=request('checklist')
    assert c.post('/api/quests/preview',json=inp).status_code==403
    token=c.get('/api/session').json()['csrf']
    headers={'Origin':app.state.endpoint,'X-CSRF-Token':token}
    assert c.post('/api/quests/preview',json=inp,headers=headers).status_code==400
    profile=app.state.profiles.active_profile()
    app.state.settings.set('cloud_consent',f'{profile.provider}|{profile.base_url}|{profile.model}|{profile.profile_id}')
    draft=c.post('/api/quests/preview',json=inp,headers=headers).json()['quest']
    assert 'Synthetic learning' in c.get('/quests').text
    action={'request_id':str(uuid4()),'action':'activate','version':draft['version']}
    active=c.post('/api/quests/'+draft['id']+'/actions',json=action,headers=headers)
    assert active.status_code==200 and active.json()['quest']['state']=='active'
    backup=BackupService(app.state.db,app.state.paths).create()
    assert BackupService(app.state.db,app.state.paths).preview(backup)['counts']['quest_plans']==1
    undo=c.post('/api/quests/undo',json={'request_id':action['request_id'],'undo_id':str(uuid4())},headers=headers)
    assert undo.status_code==200 and undo.json()['state']=='undone'
    assert app.state.quests.list()[0]['state']=='draft'


def test_selected_pending_pdf_supplies_all_pages_and_changed_reading_blocks_activation(tmp_path,monkeypatch):
    import pymupdf
    from test_web import client
    from studycenter_public.services.actions import ActionService
    c=client(tmp_path);app=c.app
    app.state.profiles.save_profile(ProviderProfile('openai','synthetic'))
    pdf=pymupdf.open()
    for text in ('Synthetic worksheet page one','Synthetic worksheet page two'):
        page=pdf.new_page();page.insert_text((72,72),text)
    raw=pdf.tobytes();pdf.close()
    doc=app.state.documents.import_bytes(raw,title='Synthetic worksheet.pdf',mime='application/pdf')
    ActionService(app.state.db).execute([{'type':'create_task','title':'Synthetic worksheet'}],str(uuid4()))
    item=app.state.db.items()[0];seen=[]
    class Provider:
        async def generate(self,req):
            seen.append(req.source_data)
            return ModelResult(plan(15,15),True,'synthetic','synthetic')
    monkeypatch.setattr('studycenter_public.services.quests.create_provider',lambda *a,**kw:Provider())
    data=request('checklist') | {'goal':'','item_id':item['id'],'document_ids':[doc['id']]}
    draft=asyncio.run(app.state.quests.preview(data))['quest']
    pages=seen[0]['source_context']['documents'][0]['pages']
    assert [page['page'] for page in pages]==[1,2]
    assert 'page one' in pages[0]['text'] and 'page two' in pages[1]['text']
    assert all(step['item_id']==item['id'] for step in draft['steps'])
    assert app.state.db.item(item['id'])['status']=='open'
    app.state.db.execute('UPDATE pages SET text=? WHERE document_id=? AND page=?',('Synthetic changed reading',doc['id'],2))
    assert app.state.quests.list()[0]['warnings']
    with pytest.raises(ValueError,match='geändert'):
        app.state.quests.execute(draft['id'],{'request_id':str(uuid4()),'action':'activate','version':1})
    assert app.state.quests.list()[0]['state']=='draft'


def test_source_change_during_model_request_never_persists(tmp_path,monkeypatch):
    from studycenter_public.services.actions import ActionService
    db,svc,_=service(tmp_path,monkeypatch,[])
    item=db.add_item({'kind':'task','title':'Synthetic task'})
    class Provider:
        async def generate(self,req):
            ActionService(db).execute([{'type':'set_status','item_id':item['id'],'status':'done'}],str(uuid4()))
            return ModelResult(plan(10),True,'synthetic','synthetic')
    monkeypatch.setattr('studycenter_public.services.quests.create_provider',lambda *a,**kw:Provider())
    with pytest.raises(ValueError,match='nicht mehr offen'):
        asyncio.run(svc.preview(request() | {'item_id':item['id']}))
    assert db.rows('SELECT * FROM quest_plans')==[]


def test_excluded_document_and_changed_original_fail_closed(tmp_path,monkeypatch):
    from test_web import client
    c=client(tmp_path);app=c.app
    app.state.profiles.save_profile(ProviderProfile('openai','synthetic'))
    source=app.state.db.upsert_source(name='Synthetic',kind='web',metadata={'options':{'excluded_urls':['https://example.test/excluded']}})
    doc=app.state.documents.import_bytes(b'Synthetic source text',title='Synthetic.txt',mime='text/plain',
        source_id=source['id'],source_url='https://example.test/excluded')
    calls=[]
    class Provider:
        async def generate(self,req):
            calls.append(req)
            return ModelResult(plan(10),True,'synthetic','synthetic')
    monkeypatch.setattr('studycenter_public.services.quests.create_provider',lambda *a,**kw:Provider())
    with pytest.raises(ValueError,match='ausgeschlossen'):
        asyncio.run(app.state.quests.preview(request() | {'document_ids':[doc['id']]}))
    assert not calls
    source=app.state.db.source(source['id'])
    app.state.db.upsert_source(source['id'],metadata={'options':{'excluded_urls':[]}})
    draft=asyncio.run(app.state.quests.preview(request() | {'document_ids':[doc['id']]}))['quest']
    from pathlib import Path
    Path(doc['original_path']).write_bytes(b'altered synthetic file')
    with pytest.raises(ValueError,match='Originalhash'):
        app.state.quests.execute(draft['id'],{'request_id':str(uuid4()),'action':'activate','version':1})
    assert app.state.quests.list()[0]['state']=='draft'


def test_combined_paced_load_warns_but_checklist_has_no_day_load(tmp_path,monkeypatch):
    _,svc,seen=service(tmp_path,monkeypatch,[plan(20),plan(20),plan(30,30)])
    one=asyncio.run(svc.preview(request()))['quest']
    svc.execute(one['id'],{'request_id':str(uuid4()),'action':'activate','version':1})
    two=asyncio.run(svc.preview(request()))['quest']
    assert list(seen[-1]['existing_load'].values())==[20]
    svc.execute(two['id'],{'request_id':str(uuid4()),'action':'activate','version':1})
    checklist=asyncio.run(svc.preview(request('checklist')))['quest']
    assert seen[-1]['existing_load']=={}
    all_quests={quest['id']:quest for quest in svc.list()}
    assert all_quests[one['id']]['warnings'] and all_quests[two['id']]['warnings']
    assert not all_quests[checklist['id']]['warnings']


def test_linked_saved_pdf_is_course_bound_and_never_fetched(tmp_path,monkeypatch):
    import pymupdf
    from test_web import client
    c=client(tmp_path);app=c.app;db=app.state.db
    app.state.profiles.save_profile(ProviderProfile('openai','synthetic'))
    source=db.upsert_source(name='Synthetic',kind='web')
    course=db.upsert_course(source['id'],'a','Synthetic A')
    other=db.upsert_course(source['id'],'b','Synthetic B')
    html=b'<html><body><a href="/worksheet.pdf">Worksheet.pdf</a></body></html>'
    parent=app.state.documents.import_bytes(html,title='Synthetic assignment',mime='text/html',
        source_url='https://example.test/task',source_id=source['id'],trusted_course_id=course['id'])
    item=db.ingest(parent,{'title':'Synthetic task','kind':'task','quote':'Worksheet.pdf','page':1,'anchor':'task'})
    pdf=pymupdf.open();page=pdf.new_page();page.insert_text((72,72),'Synthetic worksheet content')
    raw=pdf.tobytes();pdf.close()
    wrong=app.state.documents.import_bytes(raw,title='Wrong course.pdf',mime='application/pdf',
        source_url='https://example.test/worksheet.pdf',source_id=source['id'],trusted_course_id=other['id'])
    captured=[]
    class Provider:
        async def generate(self,req):captured.append(req.source_data);return ModelResult(plan(10),True,'synthetic','synthetic')
    monkeypatch.setattr('studycenter_public.services.quests.create_provider',lambda *a,**kw:Provider())
    first=asyncio.run(app.state.quests.preview(request('checklist') | {'item_id':item['id']}))['quest']
    assert captured[-1]['source_context']['missing_local_pdfs']==1
    assert [d['id'] for d in captured[-1]['source_context']['documents']]==[parent['id']]
    assert wrong['id'] not in [d['id'] for d in captured[-1]['source_context']['documents']]
    correct=app.state.documents.import_bytes(raw,title='Correct course.pdf',mime='application/pdf',
        source_url='https://example.test/worksheet.pdf',source_id=source['id'],trusted_course_id=course['id'])
    with pytest.raises(ValueError,match='geändert'):
        app.state.quests.execute(first['id'],{'request_id':str(uuid4()),'action':'activate','version':1})
    asyncio.run(app.state.quests.preview(request('checklist') | {'item_id':item['id']}))
    assert {d['id'] for d in captured[-1]['source_context']['documents']}=={parent['id'],correct['id']}
    assert captured[-1]['source_context']['missing_local_pdfs']==0


def test_deadline_clamping_triggers_budget_repair_and_edit_guard(tmp_path,monkeypatch):
    from datetime import datetime
    from studycenter_public.config import VIENNA
    today=datetime.now(VIENNA).date().isoformat()
    first=plan(20,20);first['steps'][1]['day_offset']=1
    fixed=plan(15,15)
    db,svc,seen=service(tmp_path,monkeypatch,[first,fixed])
    item=db.add_item({'kind':'task','title':'Synthetic task','date':today,'date_meaning':'deadline'})
    draft=asyncio.run(svc.preview(request() | {'item_id':item['id']}))['quest']
    assert len(seen)==2 and draft['source_snapshot']['deadline']==today
    assert seen[1]['correction']['previous_plan']['steps'][1]['day_offset']==0
    assert all(step['day_offset']==0 for step in draft['steps'])
    edits=[{key:step[key] for key in ('id','title','detail','topic','minutes','day_offset','item_id')} for step in draft['steps']]
    edits[1]['day_offset']=1
    with pytest.raises(ValueError,match='nach der gespeicherten Frist'):
        svc.execute(draft['id'],{'request_id':str(uuid4()),'action':'activate','version':1,'steps':edits})
    assert svc.list()[0]['state']=='draft'


@pytest.mark.parametrize('changes',[
    {'kind':'event'}, {'kind':'registration'}, {'kind':'task','source_state':'cancelled'},
    {'kind':'exam','obligation':'optional'},
])
def test_ineligible_item_is_not_sent_to_model(tmp_path,monkeypatch,changes):
    db,svc,seen=service(tmp_path,monkeypatch,[plan(10)])
    if changes.get('kind')=='exam':
        doc=db.save_document({'title':'Synthetic'},pages=[{'page':1,'text':'Synthetic offered exam'}])
        item=db.ingest(doc,{'title':'Synthetic','quote':'Synthetic offered exam','page':1,'anchor':'exam','source_state':'active'} | changes)
    else:
        item=db.add_item({'kind':'task','title':'Synthetic'} | changes)
    with pytest.raises(ValueError):asyncio.run(svc.preview(request() | {'item_id':item['id']}))
    assert seen==[] and svc.list()==[]


def test_quiz_window_eligibility_uses_known_boundaries_only():
    from datetime import datetime
    from studycenter_public.config import VIENNA
    from studycenter_public.services.quests import quest_item_eligible
    item={'kind':'exam','title':'Quiz 1','status':'open','source_state':'active',
        'date_meaning':'window','date':'2026-10-20','time':'10:00',
        'end_date':'2026-10-20','end_time':'12:00','personal_event':True}
    assert not quest_item_eligible(item,datetime(2026,10,20,9,59,tzinfo=VIENNA))
    assert quest_item_eligible(item,datetime(2026,10,20,10,0,tzinfo=VIENNA))
    assert not quest_item_eligible(item,datetime(2026,10,20,12,0,tzinfo=VIENNA))
    assert not quest_item_eligible(item | {'end_date':None},datetime(2026,10,20,11,tzinfo=VIENNA))
    assert quest_item_eligible(item | {'time':None,'end_time':None},datetime(2026,10,20,11,tzinfo=VIENNA))


def test_backup_restores_quest_versions_and_undo_receipts(tmp_path,monkeypatch):
    from test_web import client
    from studycenter_public.services.backup import BackupService
    c=client(tmp_path);app=c.app
    app.state.profiles.save_profile(ProviderProfile('openai','synthetic'))
    class Provider:
        async def generate(self,req):return ModelResult(plan(10),True,'synthetic','synthetic')
    monkeypatch.setattr('studycenter_public.services.quests.create_provider',lambda *a,**kw:Provider())
    draft=asyncio.run(app.state.quests.preview(request()))['quest']
    activation={'request_id':str(uuid4()),'action':'activate','version':1}
    active=app.state.quests.execute(draft['id'],activation)['quest']
    backup=BackupService(app.state.db,app.state.paths).create()
    app.state.quests.execute(draft['id'],{'request_id':str(uuid4()),'action':'step','version':active['version'],
        'step_id':active['steps'][0]['id'],'done':True})
    assert app.state.quests.list()[0]['completed']==1
    BackupService(app.state.db,app.state.paths).restore(backup,confirmed=True)
    assert app.state.quests.list()[0]['completed']==0
    result=app.state.quests.undo(activation['request_id'],str(uuid4()))
    assert result['state']=='undone' and result['quest']['state']=='draft'


def test_restore_old_backup_adds_quest_tables_without_restart(tmp_path):
    from test_web import client
    from studycenter_public.services.backup import BackupService
    c=client(tmp_path);app=c.app
    app.state.db.execute('DROP TABLE quest_receipts')
    app.state.db.execute('DROP TABLE quest_plans')
    backup=BackupService(app.state.db,app.state.paths).create()
    BackupService(app.state.db,app.state.paths).restore(backup,confirmed=True)
    assert app.state.quests.list()==[]
    assert app.state.db.one("SELECT name FROM sqlite_master WHERE name='quest_receipts'")


@pytest.mark.parametrize('horizon',[1,7,14])
def test_paced_budget_repair_covers_each_supported_horizon(tmp_path,monkeypatch,horizon):
    _,svc,seen=service(tmp_path,monkeypatch,[plan(25,25),plan(15,15)])
    draft=asyncio.run(svc.preview(request() | {'horizon':horizon}))['quest']
    assert len(seen)==2 and sum(s['minutes'] for s in draft['steps'])==30
    assert all(0<=s['day_offset']<horizon for s in draft['steps'])


def test_revoked_consent_during_source_read_prevents_provider_call(tmp_path,monkeypatch):
    from test_web import client
    c=client(tmp_path);app=c.app
    app.state.profiles.save_profile(ProviderProfile('openai','synthetic'))
    profile=app.state.profiles.active_profile()
    app.state.settings.set('cloud_consent',f'{profile.provider}|{profile.base_url}|{profile.model}|{profile.profile_id}')
    original=app.state.quests._context
    def revoke(*args,**kwargs):
        result=original(*args,**kwargs)
        app.state.settings.set('cloud_consent',None)
        return result
    monkeypatch.setattr(app.state.quests,'_context',revoke)
    calls=[]
    class Provider:
        async def generate(self,req):calls.append(req);return ModelResult(plan(10),True,'synthetic','synthetic')
    monkeypatch.setattr('studycenter_public.services.quests.create_provider',lambda *a,**kw:Provider())
    token=c.get('/api/session').json()['csrf']
    result=c.post('/api/quests/preview',json=request(),headers={'Origin':app.state.endpoint,'X-CSRF-Token':token})
    assert result.status_code==400 and not calls and app.state.quests.list()==[]


def test_auto_mode_selects_bounded_open_items_and_preserves_step_sources(tmp_path,monkeypatch):
    from datetime import datetime,timedelta
    from studycenter_public.config import VIENNA
    from studycenter_public.services.actions import ActionService
    db,svc,_=service(tmp_path,monkeypatch,[])
    today=datetime.now(VIENNA).date()
    actions=[{'type':'create_task','title':f'Synthetic {i}','date':str(today+timedelta(days=i)),
              'date_meaning':'deadline','pinned':i==5} for i in range(6)]
    ActionService(db).execute(actions,str(uuid4()))
    captured=[]
    class Provider:
        async def generate(self,req):
            captured.append(req.source_data)
            ids=[item['id'] for item in req.source_data['source_context']['items']]
            return ModelResult({'title':'Synthetic plan','steps':[{'title':f'Step {i}','detail':'Synthetic detail',
                'topic':'Synthetic','day_offset':0,'minutes':5,'item_id':i} for i in ids]},True,'synthetic','synthetic')
    monkeypatch.setattr('studycenter_public.services.quests.create_provider',lambda *a,**kw:Provider())
    draft=asyncio.run(svc.preview(request('checklist') | {'goal':'','mode':'auto'}))['quest']
    assert len(draft['item_ids'])==5
    pinned=next(item for item in db.items() if item.get('pinned'))
    assert draft['item_ids'][0]==pinned['id']
    assert {step['item_id'] for step in draft['steps']}==set(draft['item_ids'])
    assert len(captured[0]['source_context']['items'])==5
    active=svc.execute(draft['id'],{'request_id':str(uuid4()),'action':'activate','version':1})['quest']
    assert active['state']=='active' and all(item['status']=='open' for item in db.items())


def test_auto_mode_never_accepts_unselected_model_item(tmp_path,monkeypatch):
    db,svc,_=service(tmp_path,monkeypatch,[])
    db.add_item({'kind':'task','title':'Synthetic'})
    bad=plan(10);bad['steps'][0]['item_id']=999999
    class Provider:
        async def generate(self,req):return ModelResult(bad,True,'synthetic','synthetic')
    monkeypatch.setattr('studycenter_public.services.quests.create_provider',lambda *a,**kw:Provider())
    with pytest.raises(ValueError):asyncio.run(svc.preview(request() | {'mode':'auto'}))
    assert svc.list()==[]
