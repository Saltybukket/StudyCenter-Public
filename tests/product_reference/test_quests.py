import json
from datetime import date
from types import SimpleNamespace

import pytest

from studycenter_public.core.db import Database
from studycenter_public.core.quests import QuestError, QuestService


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr('studycenter_public.core.quests.today', lambda: date(2030, 3, 30))
    db = Database(tmp_path / 'test.sqlite')
    with db.connection() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS quests(id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,input_hash TEXT NOT NULL,title TEXT NOT NULL,mode TEXT NOT NULL,horizon INTEGER NOT NULL,daily_minutes INTEGER NOT NULL,start_date TEXT NOT NULL,end_date TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'draft',sources TEXT NOT NULL DEFAULT '[]',steps TEXT NOT NULL DEFAULT '[]',version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
    class Adapter:
        calls = 0
        item_id = None
        callback = None
        async def responses(self, instructions, payload):
            self.calls += 1
            if self.callback: self.callback()
            return json.dumps({'title':'Lernplan', 'steps':[{'title':'Material öffnen','detail':'Unterlagen liegen bereit','day_offset':0,'minutes':10,'item_id':self.item_id}]})
    return QuestService(db, SimpleNamespace(model_ready=True,model_provider='chatgpt'), Adapter())


def request(horizon=7, request_id='req'):
    return {'request_id':request_id,'mode':'custom','goal':'Üben','horizon':horizon,'daily_minutes':30}


@pytest.mark.asyncio
@pytest.mark.parametrize('horizon,end', [(1,'2030-03-30'),(7,'2030-04-05'),(14,'2030-04-12')])
async def test_dates_idempotence_and_persistence(service, horizon, end):
    first = (await service.preview(request(horizon)))['quest']
    assert first['start_date']=='2030-03-30' and first['end_date']==end
    assert (await service.preview(request(horizon)))['quest']['id']==first['id']
    assert service.adapter.calls==1
    with pytest.raises(QuestError) as error: await service.preview(request(horizon, 'req') | {'goal':'anders'})
    assert error.value.status==409
    assert service.dashboard()['quests'][0]['steps'][0]['scheduled_date']=='2030-03-30'


@pytest.mark.asyncio
async def test_checkboxes_version_xp_and_archive(service):
    q=(await service.preview(request()))['quest']
    q=service.activate(q['id'], {'version':q['version']})['quest']
    step=q['steps'][0]['id']
    completed=service.set_step(q['id'], {'version':q['version'],'step_id':step,'done':True})
    assert completed['xp_delta']==35 and service.dashboard()['xp']==35
    q=completed['quest']
    assert service.set_step(q['id'], {'version':q['version'],'step_id':step,'done':True})['xp_delta']==0
    with pytest.raises(QuestError) as error: service.set_step(q['id'], {'version':q['version']-1,'step_id':step,'done':False})
    assert error.value.status==409
    q=service.archive(q['id'], {'version':q['version'],'archived':True})['quest']
    assert service.dashboard()['xp']==35 and service.dashboard()['today_minutes']==0
    q=service.archive(q['id'], {'version':q['version'],'archived':False})['quest']
    assert service.set_step(q['id'], {'version':q['version'],'step_id':step,'done':False})['xp_delta']==-35


@pytest.mark.asyncio
async def test_model_cannot_reference_unselected_item(service):
    service.adapter.item_id=77
    with pytest.raises(QuestError) as error: await service.preview(request())
    assert error.value.status==502 and service.dashboard()['quests']==[]


def sample_item():
    return {'id':1,'status':'open','ignored':0,'identity':'task:1','pinned':0,'course_title':'Kurs','date_conflict':False,
            'data':{'title':'Abgabe','description':'Text','kind':'task','source_state':'active','date_meaning':'deadline','date':'2030-04-01'}}


@pytest.mark.asyncio
async def test_changed_source_during_generation_and_activation(service, monkeypatch):
    item=sample_item()
    monkeypatch.setattr(service, '_items', lambda con=None: [item])
    service.adapter.item_id=1
    service.adapter.callback=lambda: item.update(status='done')
    payload={'request_id':'source','mode':'item','item_id':1,'goal':'','horizon':7,'daily_minutes':30}
    with pytest.raises(QuestError) as error: await service.preview(payload)
    assert error.value.status==409 and service.dashboard()['quests']==[]
    item['status']='open'; service.adapter.callback=None
    q=(await service.preview(payload))['quest']
    item['status']='done'
    with pytest.raises(QuestError) as error: service.activate(q['id'], {'version':q['version']})
    assert error.value.status==409 and item['status']=='done'

@pytest.mark.asyncio
async def test_title_edit_discard_and_stale_draft(service, monkeypatch):
    q=(await service.preview(request()))['quest']
    with pytest.raises(QuestError) as error:
        service.activate(q['id'], {'version':q['version'], 'title':'   '})
    assert error.value.status==422
    monkeypatch.setattr('studycenter_public.core.quests.today', lambda: date(2030, 3, 31))
    with pytest.raises(QuestError) as error:
        service.activate(q['id'], {'version':q['version']})
    assert error.value.status==409
    with pytest.raises(QuestError): service.discard(q['id'], {'version':q['version']+1})
    assert service.discard(q['id'], {'version':q['version']})=={'discarded':True}
    monkeypatch.setattr('studycenter_public.core.quests.today', lambda: date(2030, 3, 30))
    q=(await service.preview(request(request_id='next')))['quest']
    q=service.activate(q['id'], {'version':q['version'], 'title':'  Mein Titel  '})['quest']
    assert q['title']=='Mein Titel' and service.dashboard()['quests'][0]['title']=='Mein Titel'
    with pytest.raises(QuestError) as error: service.discard(q['id'], {'version':q['version']})
    assert error.value.status==409


def test_quiz_window_open_and_closed(service, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from studycenter_public.core.quests import deadline, eligible
    monkeypatch.setattr('studycenter_public.core.quests.local_now', lambda: datetime(2030,3,30,12,0,tzinfo=ZoneInfo('Europe/Vienna')))
    item=sample_item()
    item['data'].update(kind='exam', title='Online Quiz', date_meaning='window', date='2030-03-30', time='11:00', end_date='2030-03-30', end_time='13:00', obligation='required')
    assert eligible(item,date(2030,3,30)) and deadline(item)=='2030-03-30'
    item['data']['time']='13:00'
    assert not eligible(item,date(2030,3,30))
    item['data']['time']='11:00'; item['data']['end_time']='12:00'
    assert not eligible(item,date(2030,3,30))
    item['data']['end_time']=None
    assert eligible(item,date(2030,3,30))
    item['data']['date']='2030-03-31'; item['data']['end_date']='2030-04-01'
    assert not eligible(item,date(2030,3,30))
    item['data'].update(date='2030-03-30', end_date='2030-03-30', kind='task', end_time='11:59')
    assert not eligible(item,date(2030,3,30))
    item['data'].update(kind='exam', title='Prüfungstermine', description='', end_time='13:00')
    assert not eligible(item,date(2030,3,30))


@pytest.mark.asyncio
async def test_active_source_change_warns_but_does_not_block_progress(service, monkeypatch):
    item=sample_item()
    monkeypatch.setattr(service, '_items', lambda con=None: [item])
    service.adapter.item_id=1
    q=(await service.preview({'request_id':'source2','mode':'item','item_id':1,'goal':'','horizon':7,'daily_minutes':30}))['quest']
    q=service.activate(q['id'], {'version':q['version']})['quest']
    item['status']='done'
    assert any('geändert' in s for s in service.dashboard()['quests'][0]['warnings'])
    result=service.set_step(q['id'], {'version':q['version'],'step_id':q['steps'][0]['id'],'done':True})
    assert result['xp_delta']==35 and item['status']=='done'

@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['custom', 'item'])
async def test_learning_prompt_distinguishes_topics_and_source_claims(service, monkeypatch, mode):
    seen=[]
    item=sample_item(); item['data']['title']='OPV lernen'
    monkeypatch.setattr(service, '_items', lambda con=None:[item])
    async def respond(instructions, payload):
        data=json.loads(payload); seen.append((instructions,data))
        return json.dumps({'title':'OPV','steps':[{'title':'Differenzeingang verstehen','topic':'Grundlagen',
            'detail':'Vergleiche beide Eingänge; skizziere die Differenz und erkläre sie in einem Satz.',
            'day_offset':0,'minutes':10,'item_id':1 if mode=='item' else None}]})
    service.adapter.responses=respond
    payload=({'request_id':'itemgoal','mode':'item','item_id':1,'goal':'','horizon':7,'daily_minutes':30}
             if mode=='item' else request() | {'goal':'OPV lernen'})
    quest=(await service.preview(payload))['quest']
    assert len(seen)==1
    instructions, context=seen[0]
    assert 'etabliertes allgemeines Fachwissen' in instructions
    assert 'persönlich gewählten Eintrag' in instructions
    assert 'nicht als belegten Kurs- oder Prüfungsstoff' in instructions
    assert 'konkreten Abgabe' in instructions and 'fehlende Aufgabenangaben' in instructions
    assert 'keine Internetrecherche' in instructions and 'kleinem Zeitbudget' in instructions
    assert context['goal']=='OPV lernen' if mode=='custom' else context['sources'][0]['title']=='OPV lernen'
    step=quest['steps'][0]
    assert step['topic']=='Grundlagen'
    # Editing other fields must preserve topic; changing it is explicitly supported.
    edit={'id':step['id'],'title':'Eingänge vergleichen'}
    quest=service.activate(quest['id'], {'version':quest['version'],'steps':[edit]})['quest']
    assert quest['steps'][0]['topic']=='Grundlagen'
    quest=service.set_step(quest['id'], {'version':quest['version'],'step_id':step['id'],'done':True})['quest']
    assert quest['steps'][0]['topic']=='Grundlagen' and service.dashboard()['quests'][0]['steps'][0]['topic']=='Grundlagen'


@pytest.mark.asyncio
async def test_legacy_steps_and_topic_edit_validation(service):
    quest=(await service.preview(request()))['quest']
    with service.db.connection() as con:
        row=con.execute('SELECT steps FROM quests WHERE id=?',(quest['id'],)).fetchone()
        steps=json.loads(row['steps']); steps[0].pop('topic',None)
        con.execute('UPDATE quests SET steps=? WHERE id=?',(json.dumps(steps),quest['id']))
    quest=service.dashboard()['quests'][0]
    assert quest['steps'][0]['topic']==''
    edit={'id':quest['steps'][0]['id'],'topic':'x'*81}
    with pytest.raises(QuestError) as error: service.activate(quest['id'], {'version':quest['version'],'steps':[edit]})
    assert error.value.status==422
    edit['topic']=' Grundlagen '
    quest=service.activate(quest['id'], {'version':quest['version'],'steps':[edit]})['quest']
    assert quest['steps'][0]['topic']=='Grundlagen'

@pytest.mark.asyncio
async def test_custom_goal_course_context_is_bounded_and_not_evidence(service, monkeypatch):
    with service.db.connection() as con:
        con.execute("INSERT INTO courses(title,semester,group_name,active) VALUES('Elektronik','2030S','secret group',1)")
        con.execute("INSERT INTO courses(title,semester,group_name,active) VALUES('Altkurs','2020W','secret old',0)")
        for n in range(25):
            con.execute('INSERT INTO courses(title,semester,active) VALUES(?,?,1)', ('Kurs '+str(n),'2029W'))
    seen=[]
    async def respond(instructions,payload):
        seen.append((instructions,json.loads(payload)))
        return json.dumps({'title':'Operationsverstärker','steps':[{'title':'Eingangsdifferenz','topic':'Grundlagen',
            'detail':'Vergleiche die Eingänge und erkläre die Differenz in einem Satz.', 'day_offset':0,'minutes':10,'item_id':None}]})
    service.adapter.responses=respond
    await service.preview(request() | {'goal':'OPV lernen'})
    instruction,data=seen[0]
    assert 'OPV Operationsverstärker' in instruction and 'nicht als Beleg' in instruction
    assert 'tagelange' in instruction and 'unverbindlichen' in instruction
    assert len(data['course_context'])==20 and data['course_context'][0]=={'title':'Elektronik','semester':'2030S'}
    assert all(set(c)=={'title','semester'} and c['title']!='Altkurs' for c in data['course_context'])
    assert data['sources']==[] and 'secret' not in json.dumps(data)
    item=sample_item(); monkeypatch.setattr(service,'_items',lambda con=None:[item])
    async def item_response(instructions,payload):
        seen.append((instructions,json.loads(payload)))
        return json.dumps({'title':'Abgabe','steps':[{'title':'Start','detail':'Bearbeite die erste Aufgabe.','day_offset':0,'minutes':10,'item_id':1}]})
    service.adapter.responses=item_response
    await service.preview({'request_id':'bound','mode':'item','item_id':1,'goal':'','horizon':7,'daily_minutes':30})
    assert seen[-1][1]['course_context']==[] and len(seen[-1][1]['sources'])==1

@pytest.mark.asyncio
async def test_overloaded_plan_one_repair_after_deadline_clamping(service, monkeypatch):
    item=sample_item(); item['data']['date']='2030-03-30'
    monkeypatch.setattr(service,'_items',lambda con=None:[item])
    calls=[]
    async def respond(instructions,payload):
        context=json.loads(payload); calls.append(context)
        steps=[{'title':'Start','detail':'Grundidee erklären','day_offset':0,'minutes':10,'item_id':1},
               {'title':'Übung','detail':'Ein Beispiel lösen','day_offset':2,'minutes':25,'item_id':1}]
        if len(calls)==2: steps=steps[:1]
        return json.dumps({'title':'Übung','steps':steps})
    service.adapter.responses=respond
    q=(await service.preview({'request_id':'repair','mode':'item','item_id':1,'goal':'','horizon':7,'daily_minutes':30}))['quest']
    assert len(calls)==2 and len(q['steps'])==1
    correction=calls[1]['correction']
    assert correction['overloaded_days']==[{'day_offset':0,'date':'2030-03-30','minutes':35,'limit':30}]
    assert correction['previous_plan']['steps'][1]['day_offset']==0
    assert 'nicht willkürlich' in correction['instruction']


@pytest.mark.asyncio
async def test_failed_budget_repair_never_persists(service):
    calls=[]
    async def respond(instructions,payload):
        calls.append(json.loads(payload))
        return json.dumps({'title':'OPV','steps':[{'title':'Start','detail':'Eingänge vergleichen','day_offset':0,'minutes':10},
            {'title':'Übung','detail':'Beispiel berechnen','day_offset':0,'minutes':25}]})
    service.adapter.responses=respond
    with pytest.raises(QuestError) as error: await service.preview(request(1) | {'goal':'OPV lernen'})
    assert error.value.status==502 and 'Tagesbudget' in str(error.value)
    assert len(calls)==2 and service.dashboard()['quests']==[]


@pytest.mark.asyncio
async def test_source_change_during_budget_repair_never_persists(service, monkeypatch):
    item=sample_item(); monkeypatch.setattr(service,'_items',lambda con=None:[item])
    calls=[]
    async def respond(instructions,payload):
        calls.append(json.loads(payload))
        steps=[{'title':'Start','detail':'Einstieg','day_offset':0,'minutes':10,'item_id':1}]
        if len(calls)==1: steps.append({'title':'Mehr','detail':'Weiter','day_offset':0,'minutes':25,'item_id':1})
        else: item['status']='done'
        return json.dumps({'title':'Plan','steps':steps})
    service.adapter.responses=respond
    with pytest.raises(QuestError) as error:
        await service.preview({'request_id':'repair-source','mode':'item','item_id':1,'goal':'','horizon':7,'daily_minutes':30})
    assert error.value.status==409 and len(calls)==2 and service.dashboard()['quests']==[]

@pytest.mark.asyncio
async def test_checklist_reads_exact_local_pdf_all_pages_and_preserves_status(service, monkeypatch, tmp_path):
    import hashlib
    import pymupdf
    from studycenter_public.core.db import now
    item=sample_item(); item['course_id']=1
    monkeypatch.setattr(service,'_items',lambda con=None:[item])
    html=b'<html><a href="https://tc.test/pluginfile.php/AB2.pdf">AB2.pdf</a></html>'
    htmlpath=tmp_path/'page.html'; htmlpath.write_bytes(html)
    pdfpath=tmp_path/'AB2.pdf'
    with pymupdf.open() as pdf:
        for text in ('Kalibrierung Aufgabe 1','Temperaturdrift Aufgabe 2','Messunsicherheit Aufgabe 3'):
            page=pdf.new_page(); page.insert_text((40,40),text)
        pdf.save(str(pdfpath))
    with service.db.connection() as con:
        con.execute("INSERT INTO courses(id,title) VALUES(1,'Messtechnik')")
        con.execute("INSERT INTO sources(id,course_id,kind,title,url) VALUES(1,1,'teachcenter','Kurs','https://tc.test/course')")
        con.execute("INSERT INTO items(id,course_id,identity,payload,created_at,changed_at) VALUES(1,1,'task:1','{}',?,?)",(now(),now()))
        for did,url,title,mime,path,raw in [(1,'https://tc.test/mod/resource/view.php?id=1','AB2 Ressource','text/html',htmlpath,html),(2,'https://tc.test/pluginfile.php/AB2.pdf','AB2.pdf','application/pdf',pdfpath,pdfpath.read_bytes())]:
            con.execute('INSERT INTO documents(id,source_id,url,title,mime,hash,fetched_at,original_path) VALUES(?,?,?,?,?,?,?,?)', (did,1,url,title,mime,hashlib.sha256(raw).hexdigest(),now(),str(path)))
        con.execute("INSERT INTO item_evidence(item_id,document_id,page,anchor,quote,payload) VALUES(1,1,1,'a','Arbeitsblatt 2','{}')")
    calls=[]
    async def respond(instructions,payload):
        context=json.loads(payload); calls.append((instructions,context))
        return json.dumps({'title':'AB2','steps':[{'title':str(n),'detail':'Aufgabe bearbeiten','day_offset':n,'minutes':30,'item_id':1} for n in range(3)]})
    service.adapter.responses=respond
    q=(await service.preview({'request_id':'check','mode':'item','item_id':1,'planning':'checklist','horizon':7,'daily_minutes':30}))['quest']
    pdf=next(d for d in calls[0][1]['documents'] if d['document_id']==2)
    assert [p['page'] for p in pdf['pages']]==[1,2,3] and 'Messunsicherheit' in pdf['pages'][2]['text']
    assert 'keine künstliche Tagesverteilung' in calls[0][0] and 'harte Obergrenze' not in calls[0][0]
    assert q['planning']=='checklist' and all(s['scheduled_date'] is None and s['day_offset']==0 for s in q['steps'])
    assert 'Messunsicherheit' not in json.dumps(q)
    assert len(calls)==1
    monkeypatch.setattr(service, '_document_context', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('must not re-extract')))
    q=service.activate(q['id'], {'version':q['version']})['quest']
    assert service.dashboard()['today_minutes']==0 and service.db.one('SELECT status FROM items WHERE id=1')['status']=='open'
    q=service.set_step(q['id'], {'version':q['version'],'step_id':q['steps'][2]['id'],'done':True})['quest']
    assert q['steps'][2]['done'] and not q['steps'][0]['done']
    assert service.db.one('SELECT processing_state FROM documents WHERE id=2')['processing_state']=='pending'


@pytest.mark.asyncio
async def test_checklist_rejects_changed_document_before_activation(service, monkeypatch, tmp_path):
    import hashlib
    from studycenter_public.core.db import now
    item=sample_item(); monkeypatch.setattr(service,'_items',lambda con=None:[item])
    doc=tmp_path/'doc.txt';doc.write_text('Original',encoding='utf8')
    with service.db.connection() as con:
        con.execute("INSERT INTO sources(id,kind,title,url) VALUES(1,'manual','S','https://example.test')")
        con.execute("INSERT INTO items(id,identity,payload,created_at,changed_at) VALUES(1,'task:1','{}',?,?)",(now(),now()))
        con.execute('INSERT INTO documents(id,source_id,url,title,mime,hash,fetched_at,original_path,pages,processing_state) VALUES(1,1,?,?,?,?,?,?,?,?)', ('https://example.test/doc','Aufgabe','text/plain',hashlib.sha256(b'Original').hexdigest(),now(),str(doc),json.dumps([{'page':1,'text':'Original','gaps':[]}]),'success'))
        con.execute("INSERT INTO item_evidence(item_id,document_id,page,anchor,quote,payload) VALUES(1,1,1,'a','Original','{}')")
    service.adapter.item_id=1
    q=(await service.preview({'request_id':'fresh-doc','mode':'item','item_id':1,'planning':'checklist','horizon':7,'daily_minutes':30}))['quest']
    service.db.execute("UPDATE documents SET pages=? WHERE id=1",(json.dumps([{'page':1,'text':'Geändert','gaps':[]}]),))
    with pytest.raises(QuestError) as error: service.activate(q['id'], {'version':q['version']})
    assert error.value.status==409
