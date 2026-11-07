import json
from uuid import uuid4

import pytest

from studycenter_public.core.assistant import Command, Action
from studycenter_public.core.db import encode, now
from studycenter_public.core.documents import DocumentStore
from .test_assistant_everyday import env, submit


def document_fixture(env):
    service,db,adapter,course,_=env
    source=db.execute("INSERT INTO sources(course_id,kind,title,url) VALUES(?,'public','Synthetic','https://example.org')",(course,))
    store=DocumentStore(db,service.settings)
    doc=store.save(source,'https://example.org/sheet','Arbeitsblatt','text/plain',b'Calculate the gain of the amplifier.')
    store.process(doc)
    pages=[{'page':1,'anchor':'one','text':'First understand the circuit.','gaps':[]},
           {'page':2,'anchor':'two','text':'Calculate the gain of the amplifier.','gaps':[]}]
    db.execute('UPDATE documents SET pages=? WHERE id=?',(encode(pages),doc))
    return doc


@pytest.mark.asyncio
async def test_document_context_selected_page_followup_and_no_writes(env):
    service,db,adapter,course,_=env
    doc=document_fixture(env)
    def answer(instructions,payload):
        data=json.loads(payload)
        if 'sources' not in data:
            assert data['previous']['focus_document']['id']==doc
            return encode({'action':'answer_course_question','query':''})
        assert data['focus_document']['page']==2
        assert data['sources'][0]['source_id']==f'document:{doc}:page:2'
        assert any(s['source_id']==f'document:{doc}:page:1' for s in data['sources'])
        return encode({'claims':[{'text':'Berechne die Verstärkung.','citations':[{'source_id':f'document:{doc}:page:2','quote':'Calculate the gain of the amplifier.'}]}]})
    adapter.responses.side_effect=answer
    first=Command(request_id=uuid4(),text='Was muss ich hier machen?',context_document_id=doc,context_document_page=2)
    result=await service.command(first)
    assert result['message']=='Berechne die Verstärkung.' and result['focus_document']['id']==doc
    follow=await service.command(Command(request_id=uuid4(),text='Wie gehe ich dabei vor?',previous_id=first.request_id))
    assert follow['focus_document']['page']==2 and not follow['changed']
    assert not db.rows('SELECT * FROM items')
    pages=json.loads(db.one('SELECT pages FROM documents WHERE id=?',(doc,))['pages'])
    pages[1]['text']='Corrected page reading.'
    db.execute('UPDATE documents SET pages=? WHERE id=?',(encode(pages),doc))
    stale=await service.command(Command(request_id=uuid4(),text='Und jetzt?',previous_id=first.request_id))
    assert stale['failed'] and 'geändert' in stale['message']


@pytest.mark.asyncio
@pytest.mark.parametrize('page',[3,99])
async def test_missing_document_page_never_calls_model(env,page):
    service,db,adapter,_,_=env
    doc=document_fixture(env)
    result=await service.command(Command(request_id=uuid4(),text='Erklär das',context_document_id=doc,context_document_page=page))
    assert result['failed']
    adapter.responses.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('reply',['genau','passt','okay','Ja genau!'])
async def test_short_preview_confirmation_saves_once_and_is_undoable(env,reply):
    service,db,adapter,_,_=env
    first,preview=await submit(service,adapter,'Aufgabe anlegen: Tasche packen',{'action':'create_task','title':'Tasche packen'})
    assert preview['preview']
    command=Command(request_id=uuid4(),text=reply,previous_id=first.request_id)
    result=await service.command(command)
    assert result.get('created'),result
    assert await service.command(command)==result
    assert len(db.rows('SELECT * FROM items'))==1
    assert (await service.undo(command.request_id,True))['undone']


@pytest.mark.asyncio
async def test_superseded_document_never_silently_uses_replacement(env):
    service,db,adapter,_,_=env
    doc=document_fixture(env)
    old=db.one('SELECT source_id,url FROM documents WHERE id=?',(doc,))
    store=DocumentStore(db,service.settings)
    store.save(old['source_id'],old['url'],'Replacement','text/plain',b'New content for the next sheet.')
    result=await service.command(Command(request_id=uuid4(),text='Was muss ich machen?',context_document_id=doc))
    assert result['failed'] and 'neuere Fassung' in result['message']
    adapter.responses.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('reply,cancel',[('Genau?',False),('Okay?',False),('passt',True)])
async def test_question_or_cancel_never_confirms_preview(env,reply,cancel):
    service,db,adapter,_,_=env
    first,_=await submit(service,adapter,'Aufgabe anlegen: Tasche packen',{'action':'create_task','title':'Tasche packen'})
    adapter.responses.return_value=encode({'updates':{}})
    result=await service.command(Command(request_id=uuid4(),text=reply,previous_id=first.request_id,cancel=cancel))
    assert not result.get('changed') and not db.rows('SELECT * FROM items')


def test_document_answer_does_not_create_hidden_item_focus(env):
    service,*_=env
    context={'focus_document':{'id':42,'page':1,'title':'Worksheet'}}
    result={'items':[{'id':7,'title':'Booked lab'}],'changed':False}
    service.retain_context(result,context,'request')
    assert result['focus_document']==context['focus_document'] and 'focus_item' not in result
    assert service.local_plan('Damit bin ich fertig',result) is None


@pytest.mark.asyncio
async def test_weak_confirmation_cannot_bypass_course_preview_without_preview_flag(env):
    service,db,adapter,course,_=env
    first,_=await submit(service,adapter,'Erstelle eine Aufgabe für CON',{'action':'create_task','title':'Lernen','course':'CON'})
    result=await service.command(Command(request_id=uuid4(),text='okay',previous_id=first.request_id))
    assert result.get('preview') and not db.rows('SELECT * FROM items')


@pytest.mark.asyncio
async def test_legacy_typed_receipt_is_replayed_without_new_write(env):
    service,db,adapter,_,_=env
    identifier='00000000-0000-4000-8000-000000000123'
    request=Command(request_id=identifier,text='Tasche packen')
    # Captured from the pre-document-context implementation, not recomputed by the code under test.
    old_input=request.model_dump(mode='json',exclude={'context_document_id','context_document_page'})
    receipt={'message':'Bereits gespeichert.','changed':True,'created':True,'item_id':123}
    db.execute('INSERT INTO assistant_requests(id,input_hash,input,state,result,created_at) VALUES(?,?,?,?,?,?)',
        (identifier,'99f7c690fd1760c2a5bcd70a144ca71f27a7836f35afd01a701598254686506e',encode(old_input),'completed',encode(receipt),now()))
    assert await service.command(request,direct_plan=Action(action='create_task',title='Tasche packen'))==receipt
    assert not db.rows('SELECT * FROM items')
    adapter.responses.assert_not_called()


def test_document_reader_pinned_current_and_historical_reading(env):
    from fastapi.testclient import TestClient
    from studycenter_public.core.app import create_app
    from studycenter_public.core.documents import reading_id
    service,db,_,_,_=env
    doc=document_fixture(env)
    pages=json.loads(db.one('SELECT pages FROM documents WHERE id=?',(doc,))['pages'])
    pinned=reading_id(pages[1])
    app=create_app(service.settings)
    with TestClient(app) as client:
        current=client.get(f'/documents/{doc}?page=2&reading={pinned}')
        assert current.status_code==200 and 'Calculate the gain' in current.text
        pages[1]['reading_history']=[dict(pages[1],reading_id=pinned)]
        pages[1]['text']='Corrected reading.'
        db.execute('UPDATE documents SET pages=? WHERE id=?',(encode(pages),doc))
        historical=client.get(f'/documents/{doc}?page=2&reading={pinned}')
        assert historical.status_code==200 and 'Calculate the gain' in historical.text
        assert 'data-voice-document=' not in historical.text
        assert client.get(f'/documents/{doc}?page=2&reading=missing').status_code==404


@pytest.mark.asyncio
async def test_default_document_question_skips_unnecessary_intent_clarification(env):
    service,db,adapter,_,_=env
    doc=document_fixture(env)
    def answer(instructions,payload):
        data=json.loads(payload)
        assert 'sources' in data  # No planner call that could ask which document.
        return encode({'claims':[{'text':'Lies zuerst die Schaltung.','citations':[{'source_id':f'document:{doc}:page:1','quote':'First understand the circuit.'}]}]})
    adapter.responses.side_effect=answer
    result=await service.command(Command(request_id=uuid4(),text='Was muss ich auf dieser Seite machen?',context_document_id=doc))
    assert result['message']=='Lies zuerst die Schaltung.' and not result.get('clarify')
    assert adapter.responses.await_count==1


@pytest.mark.asyncio
async def test_default_document_question_preserves_model_readiness_gate(env):
    service,db,adapter,_,_=env
    doc=document_fixture(env)
    service.settings.oauth_probe_passed=False
    result=await service.command(Command(request_id=uuid4(),text='Was muss ich auf dieser Seite machen?',context_document_id=doc))
    assert result['failed'] and 'Live-Probe' in result['message']
    adapter.responses.assert_not_called()


@pytest.mark.asyncio
async def test_security_mail_context_never_reaches_planner(env):
    service,db,adapter,_,_=env
    doc=document_fixture(env)
    db.execute("UPDATE documents SET mime='message/rfc822',title='Verification code 123456' WHERE id=?",(doc,))
    result=await service.command(Command(request_id=uuid4(),text='Bitte beschreibe dieses Dokument',context_document_id=doc))
    assert result['failed'] and 'Sicherheitsmails' in result['message']
    adapter.responses.assert_not_called()
