import pytest
from datetime import date
from uuid import uuid4
from studycenter_public.core.assistant import Action, Command
from studycenter_public.core.db import now
from studycenter_public.core.models import Candidate
from .test_assistant_everyday import env, submit, apply


def add(db,title,course,day,kind='event',meaning='start',end=None):
    item=Candidate(title=title,kind=kind,date=day,end_date=end,date_meaning=meaning,quote='Quelle',page=1,anchor='source')
    return db.execute('INSERT INTO items(identity,course_id,payload,created_at,changed_at) VALUES(?,?,?,?,?)',('test:'+str(uuid4()),course,item.model_dump_json(),now(),now()))

@pytest.mark.asyncio
async def test_all_lectures_preview_confirm_undo_and_stale(env):
    service,db,adapter,vo,_=env
    db.execute('UPDATE courses SET title=? WHERE id=?',('Messtechnik (VO)',vo))
    ue=db.execute("INSERT INTO courses(title,semester) VALUES('Messtechnik Übung (UE)','2026W')")
    good=[add(db,'Einheit '+str(n),vo,date(2026,11,2)) for n in range(8)]
    excluded=[add(db,'Vorlesung',vo,date(2026,11,3)),add(db,'Vorrechnen',vo,date(2026,11,1)),add(db,'Einheit',ue,date(2026,11,1)),add(db,'Prüfung',vo,date(2026,11,1)),add(db,'Tutorium',vo,date(2026,11,1))]
    req=Command(request_id=uuid4(),text='Markiere alle Vorlesungen bis zum 2.11.2026, alles erledigt.',preview=True)
    draft=await service.command(req)
    assert {r['id'] for r in draft['preview']['items']}==set(good)
    assert all(x['status']=='open' for x in db.rows('SELECT status FROM items'))
    accepted,result=await apply(service,req)
    assert result['changed'] and len(result['undo_bulk'])==8
    assert all(db.one('SELECT status FROM items WHERE id=?',(i,))['status']=='done' for i in good)
    assert all(db.one('SELECT status FROM items WHERE id=?',(i,))['status']=='open' for i in excluded)
    undone=await service.undo(accepted.request_id,True)
    assert undone['undone'] and all(db.one('SELECT status FROM items WHERE id=?',(i,))['status']=='open' for i in good)
    req=Command(request_id=uuid4(),text='Markiere alle Vorlesungen bis zum 2.11.2026, alles erledigt.',preview=True)
    assert (await service.command(req)).get('preview')
    db.execute('UPDATE items SET pinned=1 WHERE id=?',(good[0],))
    _,result=await apply(service,req)
    assert result.get('failed') and all(db.one('SELECT status FROM items WHERE id=?',(i,))['status']=='open' for i in good)

@pytest.mark.asyncio
async def test_structured_dates_and_bulk_pin(env):
    service,db,adapter,course,_=env
    selected=add(db,'Abgabe',course,date(2026,10,1),'task','window',date(2026,11,2))
    other=add(db,'Andere Abgabe',course,date(2026,10,1),'task','window',date(2026,11,3))
    _,result=await submit(service,adapter,'Suche Aufgaben bis 2.11.',{'action':'search_items','kind':'task','date_to':'2026-11-02'})
    assert [x['id'] for x in result['items']]==[selected]
    req,result=await submit(service,adapter,'Hefte alle offenen Aufgaben bis 2.11. an',{'action':'bulk_set_pin','kind':'task','filter_status':'open','date_to':'2026-11-02','pinned':True})
    assert [x['id'] for x in result['preview']['items']]==[selected]
    _,done=await apply(service,req)
    assert done['changed'] and db.one('SELECT pinned FROM items WHERE id=?',(selected,))['pinned']==1
    assert db.one('SELECT pinned FROM items WHERE id=?',(other,))['pinned']==0

@pytest.mark.asyncio
async def test_new_bulk_request_supersedes_old_question(env):
    service,db,adapter,course,_=env
    db.execute('UPDATE courses SET title=? WHERE id=?',('Vorlesung (VO)',course))
    item=add(db,'Einheit',course,date(2026,11,2))
    old,clarify=await submit(service,adapter,'Markiere einen Eintrag',{'action':'set_status','status':'done'})
    assert clarify.get('clarify')
    adapter.responses.reset_mock()
    command=Command(request_id=uuid4(),previous_id=old.request_id,text='Markiere alle Vorlesungen bis zum 2.11.2026, alles erledigt.',preview=True)
    result=await service.command(command)
    assert [r['id'] for r in result['preview']['items']]==[item]
    adapter.responses.assert_not_awaited()
    _,done=await apply(service,command)
    assert done['changed'] and db.one('SELECT status FROM items WHERE id=?',(item,))['status']=='done'

@pytest.mark.asyncio
async def test_undated_and_overdue_filters(env):
    service,db,adapter,course,_=env
    old=add(db,'Alt',course,date(2026,10,1),'task','deadline')
    undated=add(db,'Ohne Datum',course,None,'task','none')
    add(db,'Geplant',course,date(2026,10,1),'task','start')
    _,overdue=await submit(service,adapter,'Zeige überfällige Aufgaben',{'action':'search_items','kind':'task','date_filter':'overdue'})
    assert [x['id'] for x in overdue['items']]==[old]
    _,missing=await submit(service,adapter,'Zeige Aufgaben ohne Datum',{'action':'search_items','kind':'task','date_filter':'undated'})
    assert [x['id'] for x in missing['items']]==[undated]

@pytest.mark.asyncio
async def test_membership_and_undo_guards_are_atomic(env):
    service,db,adapter,course,_=env
    db.execute('UPDATE courses SET title=? WHERE id=?',('Vorlesung (VO)',course))
    first=add(db,'Einheit A',course,date(2026,11,1))
    second=add(db,'Einheit B',course,date(2026,11,1))
    offered=add(db,'Einheit C',course,date(2026,11,1))
    payload=db.one('SELECT payload FROM items WHERE id=?',(offered,))['payload']
    import json
    data=json.loads(payload);data['obligation']='optional'
    db.execute('UPDATE items SET payload=? WHERE id=?',(json.dumps(data),offered))
    command=Command(request_id=uuid4(),text='Markiere alle Vorlesungen bis zum 2.11.2026, alles erledigt.',preview=True)
    draft=await service.command(command)
    assert {x['id'] for x in draft['preview']['items']}=={first,second}
    added=add(db,'Einheit D',course,date(2026,11,1))
    _,stale=await apply(service,command)
    assert stale.get('failed') and all(db.one('SELECT status FROM items WHERE id=?',(i,))['status']=='open' for i in (first,second,added,offered))
    command=Command(request_id=uuid4(),text='Markiere alle Vorlesungen bis zum 2.11.2026, alles erledigt.',preview=True)
    await service.command(command)
    applied,result=await apply(service,command)
    assert result['changed']
    db.execute("UPDATE items SET status='doing' WHERE id=?",(first,))
    with pytest.raises(ValueError):await service.undo(applied.request_id,True)
    assert db.one('SELECT status FROM items WHERE id=?',(second,))['status']=='done'
    assert db.one('SELECT status FROM items WHERE id=?',(offered,))['status']=='open'

@pytest.mark.asyncio
async def test_bulk_preserves_linked_state_and_skips_noops(env):
    service,db,adapter,course,_=env
    db.execute('UPDATE courses SET title=? WHERE id=?',('Vorlesung (VO)',course))
    item=add(db,'Einheit A',course,date(2026,11,1))
    already=add(db,'Einheit B',course,date(2026,11,1))
    db.execute("UPDATE items SET status='done' WHERE id=?",(already,))
    db.execute('INSERT INTO personal_reminders(item_id,due_at,request_id) VALUES(?,?,?)',(item,'2030-01-01T10:00:00+00:00','kept'))
    db.execute('INSERT INTO item_preferences(item_id,value) VALUES(?,?)',(item,'{"mode":"off"}'))
    source=db.execute("INSERT INTO sources(course_id,kind,title,url) VALUES(?,?,?,?)",(course,'manual','Quelle','https://example.test'))
    doc=db.execute("INSERT INTO documents(source_id,url,title,mime,hash,fetched_at,original_path) VALUES(?,?,?,?,?,?,?)",(source,'https://example.test/doc','Quelle','text/plain','abc',now(),'source.txt'))
    payload=db.one('SELECT payload FROM items WHERE id=?',(item,))['payload']
    db.execute('INSERT INTO item_evidence(item_id,document_id,page,anchor,quote,payload,current) VALUES(?,?,?,?,?,?,0)',(item,doc,1,'a','Original',payload))
    tables=('personal_reminders','item_preferences','item_evidence')
    before={t:db.rows('SELECT * FROM '+t) for t in tables}
    base=db.one('SELECT payload,overrides,conflict,pinned FROM items WHERE id=?',(item,))
    command=Command(request_id=uuid4(),text='Markiere alle Vorlesungen bis zum 2.11.2026, alles erledigt.',preview=True)
    proposal=await service.command(command)
    assert [x['id'] for x in proposal['preview']['items']]==[item]
    adapter.responses.assert_not_awaited()
    applied,result=await apply(service,command)
    assert result['items'][0]['status']=='done'
    assert {t:db.rows('SELECT * FROM '+t) for t in tables}==before
    assert db.one('SELECT payload,overrides,conflict,pinned FROM items WHERE id=?',(item,))==base
    await service.undo(applied.request_id,True)
    assert {t:db.rows('SELECT * FROM '+t) for t in tables}==before

@pytest.mark.asyncio
async def test_generic_bulk_does_not_inherit_previous_course(env):
    import json
    service,db,adapter,first,second=env
    db.execute('UPDATE courses SET title=? WHERE id=?',('Kurs A (VO)',first))
    db.execute('UPDATE courses SET title=? WHERE id=?',('Kurs B (VO)',second))
    a=add(db,'Einheit A',first,date(2026,11,1));b=add(db,'Einheit B',second,date(2026,11,1))
    previous,result=await submit(service,adapter,'Eine Frage',{'action':'clarify','question':'Welcher Kurs?'})
    result['qa_context']={'course_id':first}
    db.execute('UPDATE assistant_requests SET result=? WHERE id=?',(json.dumps(result),str(previous.request_id)))
    adapter.responses.reset_mock()
    command=Command(request_id=uuid4(),previous_id=previous.request_id,text='Markiere alle Vorlesungen bis zum 2.11.2026, alles erledigt.',preview=True)
    draft=await service.command(command)
    assert {r['id'] for r in draft['preview']['items']}=={a,b}
    adapter.responses.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_weekly_coursework_pin_preserves_lectures_and_undo(env):
    from studycenter_public.core.db import encode
    service,db,adapter,course,_=env
    db.execute("INSERT INTO app_preferences(name,value) VALUES('assistant_mode',?)",(encode({'mode':'direct'}),))
    task=add(db,'Hausübung abgeben',course,date(2026,11,10),'task')
    exercise=add(db,'Übung 2',course,date(2026,11,11))
    excluded=[add(db,'Vorlesung',course,date(2026,11,10)),add(db,'Lehrveranstaltung',course,date(2026,11,10)),add(db,'Später',course,date(2026,11,19),'task')]
    req,result=await submit(service,adapter,'Hefte alle Übungen und Abgaben vom 9. bis 15. November an',{'action':'bulk_set_pin','item_category':'coursework','date_from':'2026-11-09','date_to':'2026-11-15','pinned':True,'filter_status':'open'})
    assert result.get('changed') and result.get('direct') and not result.get('preview'),result
    assert {i['id'] for i in result['items']}=={task,exercise}
    assert all(not db.one('SELECT pinned FROM items WHERE id=?',(i,))['pinned'] for i in excluded)
    assert await service.command(req)==result
    assert (await service.undo(req.request_id,True))['undone']
    assert not any(r['pinned'] for r in db.rows('SELECT pinned FROM items'))


@pytest.mark.asyncio
@pytest.mark.parametrize('action,end', [('bulk_set_pin','2026-12-15'),('bulk_set_status','2026-11-15')])
async def test_direct_mode_keeps_broad_and_completion_changes_in_preview(env,action,end):
    from studycenter_public.core.db import encode
    service,db,adapter,course,_=env
    db.execute("INSERT INTO app_preferences(name,value) VALUES('assistant_mode',?)",(encode({'mode':'direct'}),))
    identifier=add(db,'Hausübung',course,date(2026,11,10),'task')
    _,result=await submit(service,adapter,'Ändere alle Aufgaben im Zeitraum',{'action':action,'kind':'task','date_from':'2026-11-09','date_to':end,'pinned':True,'status':'done'})
    assert result.get('preview') and not result['changed']
    assert db.one('SELECT pinned,status FROM items WHERE id=?',(identifier,))=={'pinned':0,'status':'open'}


@pytest.mark.parametrize('day,phrase,start,end',[
    (date(2026,11,8),'diese Woche','2026-11-02','2026-11-08'),
    (date(2026,11,8),'nächste Woche','2026-11-09','2026-11-15'),
    (date(2026,11,9),'diese Woche','2026-11-09','2026-11-15'),
])
def test_bulk_week_uses_local_calendar_not_model_guess(env,day,phrase,start,end):
    service,*_=env
    plan=Action(action='bulk_set_pin',date_from=date(2030,1,1),date_to=date(2030,1,7),pinned=True)
    fixed=service.normalize_bulk_scope(plan,'Hefte alle Übungen und Abgaben '+phrase+' an. Keine Vorlesungen.',day)
    assert str(fixed.date_from)==start and str(fixed.date_to)==end
    assert fixed.item_category=='coursework'


def test_bulk_week_does_not_guess_ambiguous_inclusion_or_replace_explicit_dates(env):
    service,*_=env
    plan=Action(action='bulk_set_pin',pinned=True)
    fixed=service.normalize_bulk_scope(plan,'Alle Übungen und Abgaben diese Woche. Vorlesungen zählen mich dazu.',date(2026,11,8))
    assert fixed.item_category is None and fixed.date_from==date(2026,11,2)
    assert service.normalize_bulk_scope(plan,'Alle Aufgaben diese oder nächste Woche',date(2026,11,8)).date_from is None
    assert service.normalize_bulk_scope(plan,'Alle Aufgaben diese Woche bis Freitag',date(2026,11,8)).date_from is None


@pytest.mark.asyncio
async def test_missing_type_message_does_not_claim_week_is_missing(env):
    service,db,adapter,course,_=env
    _,result=await submit(service,adapter,'Hefte alle Übungen und Abgaben diese Woche an. Vorlesungen zählen mich dazu.',{'action':'bulk_set_pin','date_from':'2030-01-01','date_to':'2030-01-07','pinned':True})
    assert result.get('clarify') and 'dazugehören' in result['message']
    from datetime import datetime, timedelta
    from studycenter_public.core.config import VIENNA
    today=datetime.now(VIENNA).date()
    assert result['pending_action']['date_from']==str(today-timedelta(days=today.weekday()))
    assert not result['changed']


@pytest.mark.asyncio
async def test_bulk_repairs_own_missing_category_before_asking_user(env):
    from unittest.mock import AsyncMock
    from studycenter_public.core.db import encode
    from datetime import datetime,timedelta
    from studycenter_public.core.config import VIENNA
    service,db,adapter,course,_=env
    today=datetime.now(VIENNA).date()
    task=add(db,'Hausübung',course,today,'task')
    db.execute("INSERT INTO app_preferences(name,value) VALUES('assistant_mode',?)",(encode({'mode':'direct'}),))
    adapter.repair_response=AsyncMock(return_value=encode({'action':'bulk_set_pin','item_category':'coursework','date_from':str(today),'date_to':str(today),'pinned':True}))
    _,result=await submit(service,adapter,'Hefte alle Häuserungen, Übungen und Abgaben diese Woche an. Vorlesungen zählen mich dazu.',{'action':'bulk_set_pin','pinned':True})
    assert result.get('changed') and not result.get('clarify'),result
    assert db.one('SELECT pinned FROM items WHERE id=?',(task,))['pinned']==1
    assert adapter.repair_response.await_count==1
    assert 'validation_feedback' in adapter.repair_response.call_args.args[1]


@pytest.mark.asyncio
async def test_bulk_repair_cannot_change_action_or_execute_twice(env):
    from unittest.mock import AsyncMock
    from studycenter_public.core.db import encode
    service,db,adapter,course,_=env
    adapter.repair_response=AsyncMock(return_value=encode({'action':'create_task','title':'Wrong action'}))
    _,result=await submit(service,adapter,'Hefte alle Einträge an',{'action':'bulk_set_pin','pinned':True})
    assert result.get('clarify') and not result.get('changed')
    assert not db.rows('SELECT id FROM items')


@pytest.mark.asyncio
async def test_repair_routes_to_catalog_terra_without_global_model_change(env):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from studycenter_public.core.llm import ModelAdapter
    service,db,_,_,_=env
    auth=SimpleNamespace(models=[{'slug':'gpt-6.1-terra'},{'slug':'gpt-6-luna'}])
    adapter=ModelAdapter(db,service.settings,auth)
    adapter._responses=AsyncMock(return_value='{}')
    await adapter.repair_response('instructions','payload')
    assert adapter._responses.call_args.kwargs['model_override']=='gpt-6.1-terra'
    assert service.settings.oauth_model=='gpt-6-luna'
