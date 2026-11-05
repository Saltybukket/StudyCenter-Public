import json
from uuid import uuid4

import pytest

from studycenter_public.core.assistant import Command
from studycenter_public.core.db import encode
from .test_assistant_everyday import env, apply


async def ask(service,text,previous=None):
    req=Command(request_id=uuid4(),text=text,previous_id=previous,preview=True)
    return req,await service.command(req)


@pytest.mark.asyncio
async def test_noun_first_private_task_respects_direct_mode(env):
    service,db,adapter,_,_=env
    db.execute("INSERT INTO app_preferences(name,value) VALUES('assistant_mode',?)",(encode({'mode':'direct'}),))
    adapter.responses.return_value=encode({'action':'create_task','title':'Mit Julia Anime schauen'})
    req,result=await ask(service,'Aufgabe anlegen unter Tudu mit Julia Animeschauben.')
    assert result.get('created') and result.get('direct') and not result.get('preview')
    item=db.one('SELECT payload,course_id,status FROM items')
    payload=json.loads(item['payload'])
    assert payload['title']=='Mit Julia Anime schauen' and payload.get('date') is None
    assert item['course_id'] is None and item['status']=='open'
    assert not db.rows('SELECT * FROM personal_reminders')
    assert await service.command(req)==result
    assert len(db.rows('SELECT id FROM items'))==1


@pytest.mark.asyncio
async def test_clarification_reinterprets_original_and_complete_history(env):
    service,db,adapter,_,_=env
    adapter.responses.side_effect=[
        encode({'action':'clarify','planned_action':'create_task','question':'Was soll die Aufgabe konkret sein? Tudu meinst du vermutlich To-do?'}),
        encode({'action':'clarify','planned_action':'create_task','question':'Was soll die Aufgabe konkret sein? Tudu meinst du vermutlich To-do?'}),
        encode({'action':'create_task','title':'Mit Julia Anime schauen','task_date_role':'planned'}),
    ]
    first,question=await ask(service,'Aufgabe anlegen unter Tudu mit Julia Animeschauben.')
    assert question['clarify'] and question['dialog']['action'].get('title','')==''
    second,again=await ask(service,'genau',first.request_id)
    assert again['clarify'] and again['message']!=question['message']
    third,draft=await ask(service,'einfach anlegen',second.request_id)
    assert draft['preview']['plan']['title']=='Mit Julia Anime schauen'
    assert db.rows('SELECT * FROM items')==[]
    args=adapter.responses.await_args_list[-1].args
    payload=json.loads(args[1])
    assert payload['original_text']=='Aufgabe anlegen unter Tudu mit Julia Animeschauben.'
    assert [t['reply'] for t in payload['turns']]==['genau','einfach anlegen']
    applied,result=await apply(service,third)
    assert result['created'] and len(db.rows('SELECT * FROM items'))==1
    assert (await service.command(applied))==result
    assert len(db.rows('SELECT * FROM items'))==1


@pytest.mark.asyncio
async def test_ambiguous_alternative_yes_never_creates(env):
    service,db,adapter,_,_=env
    adapter.responses.return_value=encode({'action':'clarify','planned_action':'create_task','question':'Soll ich Kino oder Theater eintragen?'})
    first,_=await ask(service,'Lege eine Aufgabe für Kino oder Theater an.')
    adapter.responses.reset_mock()
    _,result=await ask(service,'ja',first.request_id)
    assert result['clarify'] and 'wähle' in result['message']
    assert not db.rows('SELECT * FROM items')
    adapter.responses.assert_not_awaited()


@pytest.mark.asyncio
async def test_correction_and_missing_event_date_still_ask(env):
    service,db,adapter,_,_=env
    adapter.responses.side_effect=[
        encode({'action':'clarify','planned_action':'create_event','question':'Welchen Termin meinst du?'}),
        encode({'action':'create_event','title':'Termin mit Julia','time_unspecified':True}),
    ]
    first,_=await ask(service,'Erstelle einen Termin mit Jana.')
    _,result=await ask(service,'Nein, mit Julia.',first.request_id)
    assert result['clarify'] and 'Tag' in result['message'] and not result.get('preview')
    assert result['dialog']['action']['title']=='Termin mit Julia'
    assert not result['dialog']['action']['time_unspecified']
    assert not db.rows('SELECT * FROM items')


def test_noun_first_creation_intent_does_not_turn_questions_into_actions():
    from studycenter_public.core.decision import creation_request
    assert creation_request('Aufgabe anlegen unter Tudu mit Julia Animeschauben.')
    assert creation_request('Eine Aufgabe erstellen: Fahrrad reparieren')
    assert not creation_request('Wie kann ich eine Aufgabe anlegen?')
    assert not creation_request('Aufgabe anlegen?')

@pytest.mark.asyncio
async def test_confirmed_clarification_followed_by_explicit_preview_acceptance(env):
    service,db,adapter,_,_=env
    adapter.responses.side_effect=[
        encode({'action':'clarify','planned_action':'create_task','question':'Tudu meinst du To-do?'}),
        encode({'action':'create_task','title':'Mit Julia Anime schauen'}),
    ]
    first,_=await ask(service,'Aufgabe anlegen unter Tudu mit Julia Animeschauben.')
    second,proposal=await ask(service,'genau',first.request_id)
    assert proposal.get('preview') and not db.rows('SELECT * FROM items')
    third,saved=await ask(service,'einfach anlegen',second.request_id)
    assert saved['created'] and len(db.rows('SELECT * FROM items'))==1
    assert await service.command(third)==saved
    replay=Command(request_id=uuid4(),text='einfach anlegen',previous_id=second.request_id,preview=True)
    assert await service.command(replay)==saved
    assert len(db.rows('SELECT * FROM items'))==1
    assert adapter.responses.await_count==2

@pytest.mark.asyncio
async def test_creation_words_cannot_confirm_status_preview(env):
    from .test_assistant_everyday import own
    service,db,adapter,course,_=env
    item=own(db,'Bestehende Aufgabe',course)
    adapter.responses.return_value=encode({'action':'set_status','target_id':item,'status':'done'})
    initial=Command(request_id=uuid4(),text='Markiere diese Aufgabe als erledigt',context_item_id=item,preview=True)
    draft=await service.command(initial)
    assert draft.get('preview')
    _,result=await ask(service,'einfach anlegen',initial.request_id)
    assert result.get('failed') and db.one('SELECT status FROM items WHERE id=?',(item,))['status']=='open'


@pytest.mark.asyncio
async def test_repeated_vague_alternative_stays_bounded(env):
    service,db,adapter,_,_=env
    adapter.responses.return_value=encode({'action':'clarify','planned_action':'create_task','question':'Soll ich Kino oder Theater eintragen?'})
    command,_=await ask(service,'Lege eine Aufgabe für Kino oder Theater an.')
    for _ in range(25):
        command,result=await ask(service,'ja',command.request_id)
        assert result['clarify'] and result['message'].count('Bitte wähle')==1
        assert len(result['message'])<=500
    assert not db.rows('SELECT * FROM items')
