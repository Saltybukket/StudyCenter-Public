"""Synthetic provider responses cannot skip or duplicate clarification."""
import asyncio
from uuid import uuid4

from studycenter_public.platform.secrets import SecretStore
from studycenter_public.providers.contracts import ModelResult,ProviderProfile
from studycenter_public.providers.settings import ProviderSettings
from studycenter_public.services.actions import ActionService
from studycenter_public.services.assistant import AssistantService
from studycenter_public.storage.db import Database


def make_service(tmp_path,monkeypatch,responses):
    db=Database(tmp_path/'synthetic.sqlite3')
    secrets=SecretStore(str(uuid4()),session_only=True)
    profiles=ProviderSettings(tmp_path/'providers.json',secrets)
    profiles.save_profile(ProviderProfile('openai','synthetic'))
    class Provider:
        async def generate(self,request):
            return ModelResult(responses.pop(0),True,'synthetic','synthetic')
    monkeypatch.setattr('studycenter_public.services.assistant.create_provider',lambda *a,**k:Provider())
    return db,AssistantService(db,ActionService(db),profiles,secrets)


def response(question=None,choices=None,actions=None):
    return {'answer':'Synthetic response','question':question,'choices':choices or [],
            'actions':actions or [],'citations':[]}


def test_ambiguous_question_requires_choice_and_retains_context(tmp_path,monkeypatch):
    create={'type':'create_task','title':'Synthetic task'}
    db,service=make_service(tmp_path,monkeypatch,[
        response('Welcher Termin?',['Montag','Freitag'],[create]),
        response(actions=[create]),response(actions=[create]),response(actions=[create])])
    session=str(uuid4())
    first=asyncio.run(service.respond('Erstelle eine Aufgabe',session))
    assert first['question'] and first['preview'] is None and first['actions']==[]
    second=asyncio.run(service.respond('Ja',session))
    assert second['choices']==['Montag','Freitag'] and second['preview'] is None
    repeated=asyncio.run(service.respond('Ja',session))
    assert repeated['question']==second['question'] and repeated['preview'] is None
    third=asyncio.run(service.respond('Freitag',session))
    assert third['preview'] and third['preview']['actions']==[create]
    assert db.items()==[]


def test_yes_no_clarification_can_produce_preview_but_ack_does_not_duplicate(tmp_path,monkeypatch):
    create={'type':'create_note','title':'Synthetic note'}
    db,service=make_service(tmp_path,monkeypatch,[
        response('Soll ich eine Notiz vorbereiten?',['Ja','Nein']),
        response(actions=[create]),response(actions=[create])])
    session=str(uuid4())
    asyncio.run(service.respond('Kannst du eine Notiz anlegen?',session))
    preview=asyncio.run(service.respond('Ja',session))
    assert preview['preview'] and db.items()==[]
    duplicate=asyncio.run(service.respond('Ja',session))
    assert duplicate['preview'] is None and duplicate['actions']==[]
    assert 'Vorschau' in duplicate['answer'] and db.items()==[]
