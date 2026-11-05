"""Sammelaktionen remain explicit, bounded, reviewable and versioned."""
import asyncio
from uuid import uuid4

import pytest

from test_assistant_clarification import make_service,response
from studycenter_public.services.actions import ActionService


def test_bulk_lecture_filter_is_preview_only_even_in_direct_mode(tmp_path,monkeypatch):
    bulk={'type':'bulk_set_status','kind':'event','category':'lecture',
          'date_to':'2026-11-02','status':'done'}
    db,assistant=make_service(tmp_path,monkeypatch,[response(actions=[bulk])])
    actions=ActionService(db)
    actions.execute([
        {'type':'create_event','title':'Vorlesung synthetic','date':'2026-11-02','date_meaning':'start'},
        {'type':'create_event','title':'Übung synthetic','date':'2026-11-01','date_meaning':'start'},
        {'type':'create_event','title':'Vorlesung later','date':'2026-11-03','date_meaning':'start'},
    ],str(uuid4()))
    items=db.items()
    result=asyncio.run(assistant.respond('Markiere alle Vorlesungen bis 2026-11-02 erledigt',direct=True))
    assert result['receipt'] is None and result['preview']['count']==1
    assert result['preview']['targets'][0]['title']=='Vorlesung synthetic'
    assert result['preview']['actions']==[{'type':'set_status','item_id':items[0]['id'],'status':'done'}]
    assert all(item['status']=='open' for item in db.items())
    actions.execute([{'type':'set_status','item_id':items[0]['id'],'status':'planned'}],str(uuid4()))
    with pytest.raises(ValueError,match='version changed'):
        assistant.apply(result['proposal_id'])
    assert db.item(items[0]['id'])['status']=='planned'


def test_bulk_requires_explicit_scope_and_limits_count(tmp_path,monkeypatch):
    bulk={'type':'bulk_set_pin','kind':'task','pinned':True}
    db,assistant=make_service(tmp_path,monkeypatch,[response(actions=[bulk]),response(actions=[bulk])])
    actions=ActionService(db)
    actions.execute([{'type':'create_task','title':f'Synthetic {number}'} for number in range(100)],str(uuid4()))
    actions.execute([{'type':'create_task','title':'Synthetic extra'}],str(uuid4()))
    first=asyncio.run(assistant.respond('Pinne die Aufgabe'))
    assert 'ausdrücklichen Mehrfachauftrag' in first['question'] and first['preview'] is None
    second=asyncio.run(assistant.respond('Pinne alle Aufgaben'))
    assert 'mehr als 100' in second['question'] and second['preview'] is None
    assert not any(item.get('pinned') for item in db.items())


def test_bulk_apply_and_undo_preserve_other_items(tmp_path,monkeypatch):
    bulk={'type':'bulk_set_pin','kind':'task','filter_status':'open','pinned':True}
    db,assistant=make_service(tmp_path,monkeypatch,[response(actions=[bulk])])
    actions=ActionService(db)
    actions.execute([{'type':'create_task','title':'Synthetic task'},
                     {'type':'create_note','title':'Synthetic note'}],str(uuid4()))
    result=asyncio.run(assistant.respond('Pinne alle offenen Aufgaben'))
    receipt=assistant.apply(result['proposal_id'])
    assert db.items()[0]['pinned'] is True and not db.items()[1].get('pinned')
    undo=actions.undo(receipt['request_id'],str(uuid4()))
    assert undo['state']=='undone' and not any(item.get('pinned') for item in db.items())


def test_bulk_clarification_retains_original_request_through_vague_reply(tmp_path,monkeypatch):
    bulk={'type':'bulk_set_pin','kind':'task','filter_status':'open','pinned':True}
    db,assistant=make_service(tmp_path,monkeypatch,[
        response('Welche Aufgaben?',['Offene','Geplante']),response(actions=[bulk]),response(actions=[bulk])])
    ActionService(db).execute([{'type':'create_task','title':'Synthetic'}],str(uuid4()))
    session=str(uuid4())
    asyncio.run(assistant.respond('Pinne alle Aufgaben',session))
    unclear=asyncio.run(assistant.respond('Ja',session))
    assert unclear['preview'] is None
    resolved=asyncio.run(assistant.respond('Offene',session))
    assert resolved['preview']['count']==1 and db.items()[0].get('pinned') is None


@pytest.mark.parametrize('text,action',[
    ('Pinne alle offenen Aufgaben',{'type':'bulk_set_pin','kind':'task','pinned':True}),
    ('Markiere alle Vorlesungen bis zum 02.11.2026 als erledigt',
     {'type':'bulk_set_status','kind':'event','category':'lecture','status':'done'}),
    ('Markiere alle Übungen als erledigt',
     {'type':'bulk_set_status','kind':'event','category':'lecture','status':'done'}),
])
def test_bulk_does_not_widen_explicit_user_filters(tmp_path,monkeypatch,text,action):
    db,assistant=make_service(tmp_path,monkeypatch,[response(actions=[action])])
    result=asyncio.run(assistant.respond(text))
    assert 'Sammelvorschau' in result['question'] and result['preview'] is None
    assert db.items()==[]


def test_bulk_course_and_start_bound_cannot_be_dropped(tmp_path,monkeypatch):
    incomplete={'type':'bulk_set_status','kind':'task','status':'done'}
    db,assistant=make_service(tmp_path,monkeypatch,[response(actions=[incomplete])])
    source=db.upsert_source(name='Synthetic',kind='web')
    course=db.upsert_course(source['id'],'a','Kurs A')
    ActionService(db).execute([{'type':'create_task','title':'Synthetic','course_id':course['id'],
        'date':'2026-10-02','date_meaning':'deadline'}],str(uuid4()))
    result=asyncio.run(assistant.respond('Markiere alle Aufgaben von Kurs A ab 2026-10-01 als erledigt'))
    assert result['preview'] is None and 'Kurs' in result['question']
    assert db.items()[0]['status']=='open'


def test_bulk_applies_explicit_course_and_start_bound(tmp_path,monkeypatch):
    db,assistant=make_service(tmp_path,monkeypatch,[])
    source=db.upsert_source(name='Synthetic',kind='web')
    course=db.upsert_course(source['id'],'a','Kurs A');other=db.upsert_course(source['id'],'b','Kurs B')
    bulk={'type':'bulk_set_status','kind':'task','status':'done','course_id':course['id'],'date_from':'2026-10-01'}
    class Provider:
        async def generate(self,req):
            from studycenter_public.providers.contracts import ModelResult
            return ModelResult(response(actions=[bulk]),True,'synthetic','synthetic')
    monkeypatch.setattr('studycenter_public.services.assistant.create_provider',lambda *a,**kw:Provider())
    actions=ActionService(db)
    actions.execute([{'type':'create_task','title':'Correct','course_id':course['id'],'date':'2026-10-02','date_meaning':'deadline'},
                     {'type':'create_task','title':'Wrong course','course_id':other['id'],'date':'2026-10-02','date_meaning':'deadline'},
                     {'type':'create_task','title':'Too early','course_id':course['id'],'date':'2026-09-30','date_meaning':'deadline'}],str(uuid4()))
    result=asyncio.run(assistant.respond('Markiere alle Aufgaben von Kurs A ab 2026-10-01 als erledigt'))
    assert [target['title'] for target in result['preview']['targets']]==['Correct']
    assert all(item['status']=='open' for item in db.items())


@pytest.mark.parametrize('text',[
    'Markiere alle Aufgaben aus Mathe als erledigt',
    'Markiere alle Mathe-Aufgaben als erledigt',
])
def test_unmatched_course_alias_never_expands_to_all_courses(tmp_path,monkeypatch,text):
    bulk={'type':'bulk_set_status','kind':'task','status':'done'}
    db,assistant=make_service(tmp_path,monkeypatch,[response(actions=[bulk])])
    source=db.upsert_source(name='Synthetic',kind='web')
    a=db.upsert_course(source['id'],'a','Mathematik 1');b=db.upsert_course(source['id'],'b','Synthetic')
    ActionService(db).execute([{'type':'create_task','title':'Task A','course_id':a['id']},
                              {'type':'create_task','title':'Task B','course_id':b['id']}],str(uuid4()))
    result=asyncio.run(assistant.respond(text))
    assert result['preview'] is None and 'Kurs' in result['question']
    assert all(item['status']=='open' for item in db.items())


def test_relative_week_cannot_be_dropped_or_widened(tmp_path,monkeypatch):
    from datetime import datetime,timedelta
    from studycenter_public.config import VIENNA
    today=datetime.now(VIENNA).date();first=today-timedelta(days=today.weekday());last=first+timedelta(days=6)
    unbounded={'type':'bulk_set_status','kind':'task','status':'done'}
    bounded=unbounded | {'date_from':first.isoformat(),'date_to':last.isoformat()}
    db,assistant=make_service(tmp_path,monkeypatch,[response(actions=[unbounded]),response(actions=[bounded])])
    ActionService(db).execute([{'type':'create_task','title':'This week','date':today.isoformat(),'date_meaning':'deadline'},
        {'type':'create_task','title':'Next week','date':(last+timedelta(days=1)).isoformat(),'date_meaning':'deadline'}],str(uuid4()))
    first_result=asyncio.run(assistant.respond('Markiere alle Aufgaben dieser Woche als erledigt'))
    assert first_result['preview'] is None and 'Zeitraum' in first_result['question']
    second=asyncio.run(assistant.respond('Markiere alle Aufgaben dieser Woche als erledigt'))
    assert [target['title'] for target in second['preview']['targets']]==['This week']


@pytest.mark.parametrize('text',[
    'In den nächsten 7 Tagen alle Aufgaben erledigen',
    'Heute alle Aufgaben erledigen',
    'Alle Aufgaben am 2026-10-01 erledigen',
])
def test_unparsed_temporal_scope_never_expands_to_all_dates(tmp_path,monkeypatch,text):
    db,assistant=make_service(tmp_path,monkeypatch,[response(actions=[{'type':'bulk_set_status','kind':'task','status':'done'}])])
    ActionService(db).execute([{'type':'create_task','title':'Synthetic'}],str(uuid4()))
    result=asyncio.run(assistant.respond(text))
    assert result['preview'] is None and result['question']
    assert db.items()[0]['status']=='open'

@pytest.mark.parametrize('text',[
    'Markiere alle nicht erledigten Aufgaben als erledigt',
    'Markiere alle überfälligen Aufgaben als erledigt',
])
def test_unsupported_qualifier_never_expands_to_all_tasks(tmp_path,monkeypatch,text):
    db,assistant=make_service(tmp_path,monkeypatch,[response(actions=[{'type':'bulk_set_status','kind':'task','status':'done'}])])
    ActionService(db).execute([{'type':'create_task','title':'Synthetic'}],str(uuid4()))
    result=asyncio.run(assistant.respond(text))
    assert result['preview'] is None and result['question']
    assert db.items()[0]['status']=='open'


def test_extra_time_qualifier_cannot_be_dropped_from_relative_week(tmp_path,monkeypatch):
    from datetime import datetime,timedelta
    from studycenter_public.config import VIENNA
    today=datetime.now(VIENNA).date();first=today-timedelta(days=today.weekday());last=first+timedelta(days=6)
    action={'type':'bulk_set_status','kind':'task','status':'done','date_from':first.isoformat(),'date_to':last.isoformat()}
    db,assistant=make_service(tmp_path,monkeypatch,[response(actions=[action])])
    ActionService(db).execute([{'type':'create_task','title':'Synthetic','date':today.isoformat(),'date_meaning':'deadline'}],str(uuid4()))
    result=asyncio.run(assistant.respond('Markiere alle Aufgaben dieser Woche, die vor heute fällig sind, als erledigt'))
    assert result['preview'] is None and 'Zeitraum' in result['question']
    assert db.items()[0]['status']=='open'


def test_bulk_command_cannot_bypass_preview_via_single_model_action(tmp_path,monkeypatch):
    db,assistant=make_service(tmp_path,monkeypatch,[])
    actions=ActionService(db)
    actions.execute([{'type':'create_task','title':'Task A'},{'type':'create_task','title':'Task B'}],str(uuid4()))
    item=db.items()[0]
    class Provider:
        async def generate(self,req):
            from studycenter_public.providers.contracts import ModelResult
            return ModelResult(response(actions=[{'type':'set_status','item_id':item['id'],'status':'done'}]),True,'synthetic','synthetic')
    monkeypatch.setattr('studycenter_public.services.assistant.create_provider',lambda *a,**kw:Provider())
    result=asyncio.run(assistant.respond('Markiere alle Aufgaben als erledigt',direct=True))
    assert result['receipt'] is None and result['preview'] is None and result['question']
    assert all(task['status']=='open' for task in db.items())


def test_completed_filter_cannot_be_dropped(tmp_path,monkeypatch):
    db,assistant=make_service(tmp_path,monkeypatch,[response(actions=[{'type':'bulk_set_status','kind':'task','status':'open'}])])
    ActionService(db).execute([{'type':'create_task','title':'Synthetic'}],str(uuid4()))
    result=asyncio.run(assistant.respond('Markiere alle erledigten Aufgaben als offen'))
    assert result['preview'] is None and result['question']
    assert db.items()[0]['status']=='open'


def test_personal_filter_cannot_be_dropped(tmp_path,monkeypatch):
    db,assistant=make_service(tmp_path,monkeypatch,[response(actions=[{'type':'bulk_set_pin','kind':'task','pinned':True}])])
    ActionService(db).execute([{'type':'create_task','title':'Synthetic'}],str(uuid4()))
    result=asyncio.run(assistant.respond('Pinne alle persönlichen Aufgaben'))
    assert result['preview'] is None and result['question']
    assert not db.items()[0].get('pinned')


def test_completed_filter_keeps_only_completed_tasks(tmp_path,monkeypatch):
    db,assistant=make_service(tmp_path,monkeypatch,[response(actions=[{'type':'bulk_set_status','kind':'task','status':'open','filter_status':'done'}])])
    actions=ActionService(db)
    actions.execute([{'type':'create_task','title':'Completed'},{'type':'create_task','title':'Open'}],str(uuid4()))
    completed=db.items()[0]
    actions.execute([{'type':'set_status','item_id':completed['id'],'status':'done'}],str(uuid4()))
    result=asyncio.run(assistant.respond('Markiere alle erledigten Aufgaben als offen'))
    assert [target['title'] for target in result['preview']['targets']]==['Completed']
    assert db.item(completed['id'])['status']=='done'


@pytest.mark.parametrize('extra',[
    {'filter_status':'open'},
    {'date_from':'2026-10-01'},
    {'date_to':'2026-10-31'},
])
def test_model_cannot_invent_narrower_scope_for_all(tmp_path,monkeypatch,extra):
    bulk={'type':'bulk_set_pin','kind':'task','pinned':True} | extra
    db,assistant=make_service(tmp_path,monkeypatch,[response(actions=[bulk])])
    ActionService(db).execute([{'type':'create_task','title':'Synthetic','date':'2026-10-15','date_meaning':'deadline'}],str(uuid4()))
    result=asyncio.run(assistant.respond('Pinne alle Aufgaben'))
    assert result['preview'] is None and result['question']
    assert not db.items()[0].get('pinned')
