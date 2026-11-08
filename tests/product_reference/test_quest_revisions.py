import copy
import json
from datetime import date
from types import SimpleNamespace

import pytest

from studycenter_public.core.db import Database, encode, now
from studycenter_public.core.quests import QuestError, QuestService


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr('studycenter_public.core.quests.today', lambda: date(2030, 4, 3))
    db = Database(tmp_path/'revisions.sqlite')
    class Adapter:
        callback = None
        result = {'summary':'Keine fachliche Änderung nötig.', 'changes':[]}
        async def responses(self, instructions, payload):
            self.context = json.loads(payload)
            if self.callback: self.callback()
            return json.dumps(self.result)
    service = QuestService(db,SimpleNamespace(model_ready=True,model_provider='chatgpt'),Adapter())
    steps = [dict(id=f's{i}',title=f'Thema {i}',detail=f'Übung {i} lösen',topic='Grundlagen',minutes=20,
        item_id=None,day_offset=i,done=i==0,completed_at=now() if i==0 else None,
        edited_fields=[] if i!=2 else ['detail']) for i in range(4)]
    with db.connection() as con:
        con.execute('''INSERT INTO quests(id,request_id,input_hash,title,mode,horizon,daily_minutes,start_date,end_date,state,steps,created_at,updated_at)
            VALUES('q','req','hash','OPV','custom',7,30,'2030-03-29','2030-04-04','active',?,?,?)''', (encode(steps),now(),now()))
    return service


def quest(service):
    return service.dashboard()['quests'][0]


def apply(service, proposal, ids=None):
    revision = proposal['revision']
    return service.revisions.apply('q',dict(version=revision['version'],revision_id=revision['id'],
        selected_ids=ids if ids is not None else [c['id'] for c in revision['changes']]))['quest']


def test_replan_preserves_progress_words_ids_and_undo(service):
    before = quest(service)
    proposal = service.revisions.replan('q',{'version':1})
    assert {k:v for k,v in quest(service).items() if k!='pending_revision'}=={k:v for k,v in before.items() if k!='pending_revision'}
    assert len(proposal['revision']['changes'])==3
    after = apply(service,proposal)
    assert [s['scheduled_date'] for s in after['steps']]==['2030-03-29','2030-04-03','2030-04-04','2030-04-05']
    for old,new in zip(before['steps'],after['steps']):
        for field in ('id','title','detail','topic','done','completed_at','minutes','edited_fields'):
            assert new[field]==old[field]
    assert service.dashboard()['xp']==10
    restored = service.revisions.undo('q',{'version':after['version'],'revision_id':proposal['revision']['id']})['quest']
    assert restored['steps']==before['steps']
    assert restored['start_date']==before['start_date']
    replay=service.revisions.undo('q',{'version':after['version'],'revision_id':proposal['revision']['id']})['quest']
    assert replay==restored
    service.set_step('q',{'version':restored['version'],'step_id':'s1','done':True})
    with pytest.raises(QuestError):
        service.revisions.undo('q',{'version':after['version'],'revision_id':proposal['revision']['id']})
    assert quest(service)['steps'][1]['done']


def test_checkbox_race_blocks_proposal_and_undo(service):
    proposal = service.revisions.replan('q',{'version':1})
    after = service.set_step('q',{'version':1,'step_id':'s1','done':True})['quest']
    with pytest.raises(QuestError,match='zwischenzeitlich'): apply(service,proposal)
    proposal = service.revisions.replan('q',{'version':after['version']})
    after = apply(service,proposal)
    service.set_step('q',{'version':after['version'],'step_id':'s2','done':True})
    with pytest.raises(QuestError,match='Fortschritt'):
        service.revisions.undo('q',{'version':quest(service)['version'],'revision_id':proposal['revision']['id']})
    assert quest(service)['steps'][2]['done']


def test_midnight_and_invalid_selection_fail_closed(service,monkeypatch):
    proposal = service.revisions.replan('q',{'version':1})
    with pytest.raises(QuestError,match='Unbekannte'): apply(service,proposal,['bad'])
    monkeypatch.setattr('studycenter_public.core.quests.today', lambda:date(2030,4,4))
    with pytest.raises(QuestError,match='Tag hat'): apply(service,proposal)
    assert quest(service)['version']==1


def test_other_quest_load_and_deadline_are_honest(service,monkeypatch):
    q = quest(service)
    original = service.dashboard
    other = copy.deepcopy(q)
    other['id']='other'
    other['steps']=[dict(q['steps'][1],scheduled_date='2030-04-03',minutes=30)]
    monkeypatch.setattr(service,'dashboard',lambda:{'quests':[q,other]})
    changes,warnings = service.revisions.schedule(q,[])
    assert changes[0]['after']=='2030-04-04'
    for s in q['steps']: s['item_id']=1
    changes,warnings = service.revisions.schedule(q,[{'id':1,'deadline':'2030-04-03'}])
    assert all(c['after']=='2030-04-03' for c in changes)
    assert warnings and q['steps'][1]['minutes']==20


def test_generous_budget_keeps_learning_cadence_instead_of_cramming(service):
    service.db.execute("UPDATE quests SET daily_minutes=120 WHERE id='q'")
    proposal=service.revisions.replan('q',{'version':1})
    assert [c['after'] for c in proposal['revision']['changes']]==['2030-04-03','2030-04-04','2030-04-05']


def test_morning_deadline_keeps_preparation_before_the_final_morning(service):
    q=quest(service)
    q['steps'][-1]['item_id']=1
    source={'id':1,'deadline':'2030-04-05','date':'2030-04-05','time':'08:00:00'}
    changes,warnings=service.revisions.schedule(q,[source])
    assert max(c['after'] for c in changes)=='2030-04-04'
    assert any('Vormittag' in w for w in warnings)
    assert source['deadline']=='2030-04-05'
    source['time']=None
    changes,warnings=service.revisions.schedule(q,[source])
    assert max(c['after'] for c in changes)=='2030-04-05'


def test_manual_day_change_is_preserved_and_reserved(service):
    edited=service.revisions.edit('q',{'version':1,'step_id':'s1','values':{'planned_date':'2030-04-08'}})['quest']
    proposal=service.revisions.replan('q',{'version':edited['version']})
    changes=proposal['revision']['changes']
    assert not any(c['step_id']=='s1' for c in changes)
    after=apply(service,proposal)
    assert after['steps'][1]['scheduled_date']=='2030-04-08'
    q=copy.deepcopy(after)
    q['steps'][1]['scheduled_date']='2030-04-03'
    q['steps'][1]['minutes']=30
    changes,warnings=service.revisions.schedule(q,[])
    assert all(c['after']!='2030-04-03' for c in changes)


def change(step_id='s1', **extra):
    return dict(step_id=step_id,reason='Das Skript erklärt die Gegenkopplung genauer.',title='Gegenkopplung berechnen',
        detail='Spannungsverstärkung berechnen und Vorzeichen prüfen.',topic='OPV',minutes=25,item_id=None,materials=[],**extra)


@pytest.mark.asyncio
async def test_selective_source_update_protects_own_words_and_done(service):
    service.adapter.result={'summary':'Zwei passende Vertiefungen.', 'changes':[change('s1'),change('s2'),change(None)]}
    before = quest(service)
    proposal = await service.revisions.suggest('q',{'version':1})
    changes = proposal['revision']['changes']
    assert changes[1]['after']['detail']==before['steps'][2]['detail']
    after = apply(service,proposal,[changes[1]['id'],changes[2]['id']])
    assert after['steps'][0]['title']==before['steps'][0]['title']
    assert after['steps'][1]['title']==before['steps'][1]['title']
    assert after['steps'][2]['title']=='Gegenkopplung berechnen'
    assert after['steps'][2]['detail']==before['steps'][2]['detail']
    assert len(after['steps'])==5 and after['steps'][-1]['done'] is False
    assert service.dashboard()['xp']==10


@pytest.mark.asyncio
@pytest.mark.parametrize('step_id',['s0','missing'])
async def test_model_cannot_modify_completed_or_missing_step(service,step_id):
    service.adapter.result={'summary':'Änderung', 'changes':[change(step_id)]}
    with pytest.raises(QuestError,match='erledigten Schritt'):
        await service.revisions.suggest('q',{'version':1})
    assert quest(service)['version']==1


@pytest.mark.asyncio
async def test_legacy_words_remain_protected(service):
    with service.db.connection() as con:
        steps=json.loads(con.execute("SELECT steps FROM quests WHERE id='q'").fetchone()[0])
        for s in steps: s.pop('edited_fields')
        con.execute("UPDATE quests SET steps=? WHERE id='q'",(encode(steps),))
    service.adapter.result={'summary':'Alten Text beibehalten.', 'changes':[change()]}
    proposal=await service.revisions.suggest('q',{'version':1})
    assert proposal['revision']['changes']==[]


@pytest.mark.asyncio
async def test_change_during_model_call_does_not_overwrite_progress(service):
    service.adapter.callback=lambda:service.set_step('q',{'version':1,'step_id':'s1','done':True})
    with pytest.raises(QuestError,match='zwischenzeitlich'):
        await service.revisions.suggest('q',{'version':1})
    assert quest(service)['steps'][1]['done']


@pytest.mark.asyncio
async def test_hallucinated_material_rejected(service):
    item=change()
    item['materials']=[{'document_id':900,'page':1}]
    service.adapter.result={'summary':'Quelle', 'changes':[item]}
    with pytest.raises(QuestError,match='nicht gelesene'):
        await service.revisions.suggest('q',{'version':1})


def test_checklist_does_not_acquire_dates(service):
    with service.db.connection() as con: con.execute("UPDATE quests SET planning='checklist' WHERE id='q'")
    with pytest.raises(QuestError,match='jederzeit'):
        service.revisions.replan('q',{'version':1})
    assert all(s['scheduled_date'] is None for s in quest(service)['steps'])


@pytest.mark.asyncio
async def test_review_keeps_uncited_lineage_and_checks_revocation_after_read(service,monkeypatch):
    allowed = {91:True}
    monkeypatch.setattr('studycenter_public.core.quest_materials.document_model_allowed',lambda db,did,con=None:allowed.get(did,True))
    with service.db.connection() as con:
        steps=json.loads(con.execute("SELECT steps FROM quests WHERE id='q'").fetchone()[0])
        steps[0]['model_document_ids']=[91]
        con.execute("UPDATE quests SET steps=? WHERE id='q'",(encode(steps),))
    proposal=await service.revisions.suggest('q',{'version':1})
    after=apply(service,proposal,[])
    assert after['steps'][0]['model_document_ids']==[91]
    allowed[91]=False
    service.adapter.callback=lambda:pytest.fail('Revoked material must never reach provider')
    with pytest.raises(QuestError,match='früher verwendeter'):
        await service.revisions.suggest('q',{'version':after['version']})
    allowed[91]=True
    def collect(*args):
        allowed[91]=False
        return [],{}
    monkeypatch.setattr('studycenter_public.core.quest_materials.collect',collect)
    with pytest.raises(QuestError,match='früher verwendeter'):
        await service.revisions.suggest('q',{'version':after['version']})


def test_other_plan_change_invalidates_schedule_preview(service):
    proposal=service.revisions.replan('q',{'version':1})
    with service.db.connection() as con:
        con.execute('''INSERT INTO quests(id,request_id,input_hash,title,mode,horizon,daily_minutes,start_date,end_date,state,steps,created_at,updated_at)
            VALUES('other','other','hash','Anderer Plan','custom',7,30,'2030-04-03','2030-04-09','active','[]',?,?)''',(now(),now()))
    with pytest.raises(QuestError,match='anderer Lernplan'): apply(service,proposal)


def test_newest_html_is_followed_before_resolving_pdf_links(service,tmp_path,monkeypatch):
    import hashlib
    from studycenter_public.core.models import Candidate
    with service.db.connection() as con:
        con.execute("INSERT INTO courses(id,title) VALUES(1,'Messtechnik')")
        con.execute("INSERT INTO sources(id,course_id,kind,title,url) VALUES(1,1,'teachcenter','Kurs','https://tc.test/course')")
        value=Candidate(title='Arbeitsblatt',kind='task',quote='Blatt',page=1,anchor='test')
        con.execute('INSERT INTO items(id,course_id,identity,payload,created_at,changed_at) VALUES(1,1,?,?,?,?)',('task:worksheet',value.model_dump_json(),now(),now()))
        for did,url,mime,text in [(1,'https://tc.test/mod/assign/view.php?id=1','text/html','<a href="old.pdf">Altes Blatt</a>'),
            (2,'https://tc.test/mod/assign/view.php?id=1','text/html','<a href="new.pdf">Neues Blatt</a>'),
            (3,'https://tc.test/mod/assign/new.pdf','application/pdf','Aufgabe 4: aktuelle Kalibrierung')]:
            path=tmp_path/f'doc{did}';path.write_text(text,encoding='utf8')
            con.execute('INSERT INTO documents(id,source_id,url,title,mime,hash,fetched_at,original_path,pages,processing_state) VALUES(?,1,?,?,?,?,?,?,?,?)',
                (did,url,f'Blatt {did}',mime,hashlib.sha256(path.read_bytes()).hexdigest(),f'2030-04-0{did}',str(path),encode([{'page':1,'text':text,'gaps':[]}]),'success'))
        con.execute("INSERT INTO item_evidence(item_id,document_id,page,anchor,quote,payload) VALUES(1,1,1,'a','Alte Abgabe','{}')")
    items=service._items()
    _,docs=service._document_context(items,follow_latest=True)
    assert {d['document_id'] for d in docs}=={2,3}
    _,docs=service._document_context(items)
    assert 3 not in {d['document_id'] for d in docs}


@pytest.mark.asyncio
async def test_active_edit_is_protected_from_later_model_suggestions(service):
    edited=service.revisions.edit('q',{'version':1,'step_id':'s1','values':{'detail':'Meine eigene Methode mit Beispiel 7.','planned_date':'2030-04-08'}})['quest']
    step=edited['steps'][1]
    assert step['detail']=='Meine eigene Methode mit Beispiel 7.'
    assert set(step['edited_fields'])=={'detail','planned_date'}
    assert step['scheduled_date']=='2030-04-08'
    service.adapter.result={'summary':'Vertiefung','changes':[change()]}
    suggestion=await service.revisions.suggest('q',{'version':edited['version']})
    after=apply(service,suggestion)
    assert after['steps'][1]['detail']==step['detail']
    assert after['steps'][1]['scheduled_date']==step['scheduled_date']
    with pytest.raises(QuestError,match='zwischenzeitlich'):
        service.revisions.edit('q',{'version':1,'step_id':'s1','values':{'title':'Veraltet'}})
    assert after['steps'][0]['completed_at']==edited['steps'][0]['completed_at']


@pytest.mark.parametrize('values',[{'planned_date':'20300403'},{'minutes':0},{'done':True},{'item_id':100},{'title':' '}])
def test_active_edit_rejects_invalid_or_server_owned_values(service,values):
    with pytest.raises(QuestError): service.revisions.edit('q',{'version':1,'step_id':'s1','values':values})
    assert quest(service)['version']==1


def test_manual_date_adjustment_updates_displayed_range(service):
    q=service.revisions.edit('q',{'version':1,'step_id':'s3','values':{'planned_date':'2030-05-02'}})['quest']
    assert q['schedule_end']=='2030-05-02'
    assert q['steps'][0]['scheduled_date']=='2030-03-29'


def test_local_scheduling_never_needs_model_permission(service,monkeypatch):
    monkeypatch.setattr('studycenter_public.core.quest_materials.document_model_allowed',lambda *args:False)
    service.settings.model_ready=False
    proposal=service.revisions.replan('q',{'version':1})
    assert apply(service,proposal)['steps'][1]['scheduled_date']=='2030-04-03'


def test_pending_resume_dismiss_and_identical_apply_replay(service):
    proposal=service.revisions.replan('q',{'version':1})
    assert service.revisions.pending('q')==proposal
    assert quest(service)['pending_revision']['id']==proposal['revision']['id']
    after=apply(service,proposal)
    replay=apply(service,proposal)
    assert replay==after and replay['version']==2
    with pytest.raises(QuestError): apply(service,proposal,[])
    new=service.revisions.replan('q',{'version':2})
    service.revisions.dismiss('q',{'version':2,'revision_id':new['revision']['id']})
    assert quest(service)['pending_revision'] is None
    with pytest.raises(QuestError): service.revisions.pending('q')
    assert quest(service)['version']==2


@pytest.mark.asyncio
async def test_source_review_scopes_existing_work_and_rejects_late_task_completion(service):
    from studycenter_public.core.models import Candidate
    with service.db.connection() as con:
        for identifier,title in [(1,'Mein Arbeitsblatt'),(2,'Andere Aufgabe ohne Quest-Schritt')]:
            value=Candidate(title=title,kind='task',quote='Eigene Aufgabe',anchor='own',page=1)
            con.execute('INSERT INTO items(id,identity,payload,created_at,changed_at) VALUES(?,?,?,?,?)',(identifier,f'note:{identifier}',value.model_dump_json(),now(),now()))
    sources=service._sources(service._items())
    with service.db.connection() as con:
        steps=json.loads(con.execute("SELECT steps FROM quests WHERE id='q'").fetchone()[0])
        for step in steps:step['item_id']=1
        con.execute("UPDATE quests SET sources=?,steps=? WHERE id='q'",(encode(sources),encode(steps)))
    service.adapter.result={'summary':'Keine weitere Änderung nötig.','changes':[]}
    proposal=await service.revisions.suggest('q',{'version':1})
    assert [s['id'] for s in service.adapter.context['sources']]==[1]
    service.db.execute("INSERT INTO courses(id,title,active) VALUES(2,'Anderer Kurs',0)")
    service.db.execute("UPDATE items SET course_id=2 WHERE id=2")
    # An unused original auto-plan candidate cannot block the actual plan.
    assert not quest(service)['sources_changed']
    proposal=await service.revisions.suggest('q',{'version':1})
    assert [s['id'] for s in proposal['revision']['sources']]==[1]
    service.db.execute("UPDATE items SET status='done' WHERE id=1")
    with pytest.raises(QuestError,match='Bearbeitungsstand'):apply(service,proposal)
    assert quest(service)['version']==1


@pytest.mark.asyncio
async def test_empty_model_step_id_is_not_a_new_step(service):
    service.adapter.result={'summary':'Ungültige ID','changes':[change('')]}
    with pytest.raises(QuestError,match='gültigen Anpassungsvorschlag'):
        await service.revisions.suggest('q',{'version':1})
    assert len(quest(service)['steps'])==4


@pytest.mark.asyncio
async def test_additions_are_inserted_in_learning_order_and_spread_without_moving_old_steps(service):
    service.db.execute("UPDATE quests SET start_date='2030-04-05' WHERE id='q'")
    service.adapter.result={'summary':'Passende Ergänzungen','changes':[change(None,insert_before='s1'),change(None,insert_before='s1')]}
    before=quest(service)
    proposal=await service.revisions.suggest('q',{'version':1})
    changes=proposal['revision']['changes']
    assert [c['after']['planned_date'] for c in changes]==['2030-04-03','2030-04-04']
    after=apply(service,proposal)
    assert [s['id'] for s in after['steps'] if s['id'] in {s['id'] for s in before['steps']}]==[s['id'] for s in before['steps']]
    assert after['steps'][0]['done'] and after['steps'][3]['id']=='s1'
    for original in before['steps']:
        kept=next(s for s in after['steps'] if s['id']==original['id'])
        assert all(kept[k]==original[k] for k in ('title','detail','scheduled_date','done','completed_at'))


@pytest.mark.asyncio
@pytest.mark.parametrize('anchor',['missing','s0'])
async def test_model_cannot_insert_relative_to_unknown_or_completed_step(service,anchor):
    service.adapter.result={'summary':'Position','changes':[change(None,insert_before=anchor)]}
    with pytest.raises(QuestError,match='Position'):await service.revisions.suggest('q',{'version':1})


@pytest.mark.asyncio
async def test_added_schedule_rejects_competing_quest_change(service):
    service.adapter.result={'summary':'Neue Arbeit','changes':[change(None)]}
    proposal=await service.revisions.suggest('q',{'version':1})
    with service.db.connection() as con:
        con.execute('''INSERT INTO quests(id,request_id,input_hash,title,mode,horizon,daily_minutes,start_date,end_date,state,steps,created_at,updated_at)
            VALUES('other','other','hash','Anderer Plan','custom',7,30,'2030-04-03','2030-04-09','active','[]',?,?)''',(now(),now()))
    with pytest.raises(QuestError,match='anderer Lernplan'):apply(service,proposal)


def test_added_prerequisite_uses_free_day_before_full_anchor(service):
    q=quest(service)
    for step in q['steps']:
        if not step['done']:step['scheduled_date']='2030-04-04'
    service.dashboard=lambda:{'quests':[q]}
    changes=[{'step_id':None,'insert_before':'s1','after':{'minutes':25,'item_id':None}}]
    service.revisions.schedule_additions(q,[],changes)
    assert changes[0]['after']['planned_date']=='2030-04-03'
    # With both days full, report overload instead of placing a prerequisite later.
    other=copy.deepcopy(q);other['id']='other'
    for step in other['steps']:step['scheduled_date']='2030-04-03'
    service.dashboard=lambda:{'quests':[q,other]}
    warnings=service.revisions.schedule_additions(q,[],changes)
    assert changes[0]['after']['planned_date']=='2030-04-04'
    assert any('Zeitbudget' in w for w in warnings)


def test_appended_addition_starts_after_existing_open_days(service):
    q=quest(service)
    q['steps'][-1]['scheduled_date']='2030-04-06'
    service.dashboard=lambda:{'quests':[q]}
    changes=[{'step_id':None,'insert_before':None,'after':{'minutes':10,'item_id':None}}]
    service.revisions.schedule_additions(q,[],changes)
    assert changes[0]['after']['planned_date']=='2030-04-06'


def test_added_work_accounts_for_longer_existing_step_even_with_partial_selection(service):
    q=quest(service)
    q['steps'][1]['scheduled_date']='2030-04-03'
    service.dashboard=lambda:{'quests':[q]}
    changes=[{'step_id':'s1','after':{'minutes':45}},
             {'step_id':None,'insert_before':'s1','after':{'minutes':10,'item_id':None}}]
    warnings=service.revisions.schedule_additions(q,[],changes)
    assert any('Zeitbudget' in w for w in warnings)


@pytest.mark.asyncio
async def test_blank_model_content_cannot_create_an_empty_step(service):
    empty=change(None);empty['title']='  '
    service.adapter.result={'summary':'Neue Aufgabe','changes':[empty]}
    with pytest.raises(QuestError,match='gültigen Anpassungsvorschlag'):
        await service.revisions.suggest('q',{'version':1})


@pytest.mark.parametrize('accepted',[False,True])
def test_backup_keeps_pending_reviews_and_safe_undo(service,tmp_path,accepted):
    from studycenter_public.core.backup import create_backup,inspect_archive
    before=quest(service)
    proposal=service.revisions.replan('q',{'version':1})
    after=apply(service,proposal) if accepted else before
    archive=create_backup(service.db,tmp_path)
    destination=tmp_path/'validated-backup'
    manifest=inspect_archive(archive,destination)
    assert manifest['counts']['quest_revisions']==1
    restored=QuestService(Database(destination/'campusboard.sqlite3'),service.settings,service.adapter)
    if accepted:
        result=restored.revisions.undo('q',{'version':after['version'],'revision_id':proposal['revision']['id']})['quest']
        assert result['steps']==before['steps']
    else:
        assert restored.revisions.pending('q')==proposal
        assert quest(restored)['steps']==before['steps']
