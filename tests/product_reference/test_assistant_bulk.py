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
