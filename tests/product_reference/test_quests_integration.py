import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from fastapi.testclient import TestClient

from studycenter_public.core.app import create_app
from studycenter_public.core.backup import create_backup, inspect_archive, apply_pending
from .reference_settings import Settings
from studycenter_public.core.db import now
from studycenter_public.core.models import Candidate


def test_http_quest_flow_versions_and_backup_preserve_original_task(tmp_path, monkeypatch):
    settings=Settings(data_dir=tmp_path)
    monkeypatch.setattr(type(settings),'model_ready',property(lambda self:True))
    app=create_app(settings);db=app.state.db
    import studycenter_public.core.quests as module
    monkeypatch.setattr(module,'today',lambda:date(2030,3,30))
    value=Candidate(title='Arbeitsblatt 2 bearbeiten',kind='task',date='2030-03-30',date_meaning='deadline',quote='Eigene Aufgabe',page=1,anchor='own')
    item=db.execute('INSERT INTO items(identity,payload,created_at,changed_at) VALUES(?,?,?,?)',('note:quest-test',value.model_dump_json(),now(),now()))
    original=db.rows('SELECT * FROM items')
    calls=[]
    async def model(instructions,payload):
        calls.append(payload)
        # A model may omit the source identifier; a single-task quest must still
        # be bound to its real deadline, not escape it via null.
        return json.dumps({'title':'Arbeitsblatt in Etappen','steps':[
            {'title':'Aufgaben lesen','detail':'Notiere die drei nächsten Arbeitsschritte.','day_offset':6,'minutes':10,'item_id':None},
            {'title':'Erste Teilaufgabe lösen','detail':'Halte deinen Lösungsweg fest.','day_offset':6,'minutes':25,'item_id':None}]})
    app.state.quests.adapter.chat_completions=model
    app.state.quests.adapter.responses=model
    client=TestClient(app);headers={'X-CSRF-Token':app.state.csrf}
    payload={'request_id':'integration','mode':'item','item_id':item,'horizon':7,'daily_minutes':60}
    assert client.post('/api/quests/preview',json=payload).status_code==403
    q=client.post('/api/quests/preview',json=payload,headers=headers).json()['quest']
    assert client.post('/api/quests/preview',json=payload,headers=headers).json()['quest']['id']==q['id'] and len(calls)==1
    assert all(s['item_id']==item and s['scheduled_date']=='2030-03-30' for s in q['steps'])
    activated=client.post(f"/api/quests/{q['id']}/activate",json={'version':q['version'],'title':'Mein Arbeitsblatt'},headers=headers)
    assert activated.status_code==200
    q=activated.json()['quest'];assert q['title']=='Mein Arbeitsblatt'
    def check(step):
        return client.post(f"/api/quests/{q['id']}/step",json={'version':q['version'],'step_id':step['id'],'done':True},headers=headers)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(check,q['steps']))
    assert sorted(r.status_code for r in responses)==[200,409]
    current=client.get('/api/quests').json();assert current['xp']==10
    assert db.rows('SELECT * FROM items')==original
    before=db.rows('SELECT * FROM quests')
    backup=create_backup(db,tmp_path)
    manifest=inspect_archive(backup,tmp_path/'checked')
    assert manifest['counts']['quests']==1
    db.execute("UPDATE quests SET title='Temporary change'")
    shutil.copy2(backup,tmp_path/'restore-pending.zip');apply_pending(db,tmp_path)
    assert db.rows('SELECT * FROM quests')==before and db.rows('SELECT * FROM items')==original
    # Older backups without the optional table still acquire it on initialization.
    db.execute('DROP TABLE quests');old=create_backup(db,tmp_path)
    assert 'quests' not in inspect_archive(old,tmp_path/'old-checked')['counts']
    db.initialize();assert db.rows('SELECT * FROM quests')==[]
    assert client.get('/quests').status_code==200
    assert client.post('/api/quests/missing/step',json=[],headers=headers).status_code==422


def test_linked_preparation_uses_actual_closing_and_availability():
    from studycenter_public.core.quests import deadline,eligible
    item={'data':{'kind':'task','source_state':'active','date_meaning':'start','date':'2030-03-30'},
          'status':'open','task_display':{'deadline_date':'2030-04-01','available_now':True,'available_from':'2030-03-29'}}
    assert deadline(item)=='2030-04-01' and eligible(item,date(2030,3,30))
    item['task_display']['available_now']=False
    assert not eligible(item,date(2030,3,30))


def test_old_backup_with_quests_without_planning_restores(tmp_path):
    from studycenter_public.core.db import Database
    db=Database(tmp_path/'studycenter_public.core.sqlite3')
    db.execute("INSERT INTO quests(id,request_id,input_hash,title,mode,horizon,daily_minutes,start_date,end_date,created_at,updated_at) VALUES('q','r','hash','Alt','custom',7,30,'2030-01-01','2030-01-07',?,?)",(now(),now()))
    db.execute('ALTER TABLE quests DROP COLUMN planning')
    old=create_backup(db,tmp_path)
    assert inspect_archive(old,tmp_path/'checked-legacy')['counts']['quests']==1
    shutil.copy2(old,tmp_path/'restore-pending.zip')
    apply_pending(db,tmp_path)
    db.initialize()
    assert db.one("SELECT planning,title FROM quests WHERE id='q'")=={'planning':'paced','title':'Alt'}


def test_material_reader_link_survives_document_reprocessing(tmp_path):
    from studycenter_public.core.documents import DocumentStore,reading_id
    settings=Settings(data_dir=tmp_path);app=create_app(settings);db=app.state.db;store=DocumentStore(db,settings)
    sid=db.execute("INSERT INTO sources(kind,title,url) VALUES('manual','Worksheet','https://example.test')")
    did=store.save(sid,'https://example.test/script.txt','Script','text/plain',b'Current source reading')
    previous={'page':1,'anchor':'text','text':'Earlier source reading','gaps':['OCR uncertainty']}
    db.execute("UPDATE documents SET pages=?,processing_state='partial' WHERE id=?",(json.dumps([previous]),did))
    updated=store.process(did)
    assert json.loads(updated['pages'])[0]['reading_history'][0]['text']==previous['text']
    with TestClient(app) as client:
        response=client.get(f'/documents/{did}?page=1&reading={reading_id(previous)}')
        assert response.status_code==200 and 'Earlier source reading' in response.text


def test_document_processing_keeps_concurrent_reread(tmp_path,monkeypatch):
    from studycenter_public.core.documents import DocumentStore
    settings=Settings(data_dir=tmp_path);app=create_app(settings);db=app.state.db;store=DocumentStore(db,settings)
    sid=db.execute("INSERT INTO sources(kind,title,url) VALUES('manual','Fixture','https://example.test')")
    did=store.save(sid,'https://example.test/page','Page','text/html',b'<p>Old input</p>')
    newer=json.dumps([{'page':1,'text':'Concurrent verified reading','gaps':[]}])
    def changed_html(raw,url):
        db.execute("UPDATE documents SET pages=?,progress='verified' WHERE id=?",(newer,did))
        return 'Title',None,[{'page':1,'text':'Outdated processing','gaps':[]}]
    monkeypatch.setattr('studycenter_public.core.documents.html_content',changed_html)
    result=store.process(did)
    assert result['pages']==newer and result['progress']=='verified'
