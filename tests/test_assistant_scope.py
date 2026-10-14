import asyncio
from uuid import uuid4

from studycenter_public.providers.contracts import ModelResult, ProviderProfile
from studycenter_public.providers.settings import ProviderSettings
from studycenter_public.platform.secrets import SecretStore
from studycenter_public.services.actions import ActionService
from studycenter_public.services.assistant import AssistantService
from studycenter_public.storage.db import Database


def test_selected_document_questions_do_not_transfer_unselected_items_or_history(tmp_path, monkeypatch):
    db = Database(tmp_path/'synthetic.sqlite3')
    docs = []
    for index in range(2):
        text = 'Selected evidence.' if index == 0 else 'Unselected evidence.'
        doc = db.save_document({'url':f'https://example.invalid/{index}', 'title':text}, pages=[{'page':1, 'text':text}])
        db.ingest(doc, {'title':text, 'kind':'task', 'page':1, 'quote':text, 'anchor':'synthetic'})
        docs.append(doc)
    ActionService(db).execute([{'type':'create_note','title':'Unselected personal note'}], str(uuid4()))
    secrets = SecretStore(str(uuid4()), session_only=True)
    profiles = ProviderSettings(tmp_path/'providers.json', secrets)
    profiles.save_profile(ProviderProfile('openai','synthetic'))
    captured = []
    class Provider:
        async def generate(self, request):
            captured.append(request.source_data)
            return ModelResult({'answer':'Synthetic answer', 'actions':[], 'question':None, 'choices':[], 'citations':[]}, True, 'synthetic', 'synthetic')
    monkeypatch.setattr('studycenter_public.services.assistant.create_provider', lambda *a, **k:Provider())
    service = AssistantService(db, ActionService(db), profiles, secrets)
    session = str(uuid4())
    asyncio.run(service.respond('Global synthetic question', session))
    asyncio.run(service.respond('Explain selected document', session, task='questions', document_ids=[docs[0]['id']]))
    context = captured[-1]
    assert context['history'] == []
    assert [item['title'] for item in context['items']] == ['Selected evidence.']
    assert [doc['document_id'] for doc in context['documents']] == [docs[0]['id']]
    asyncio.run(service.respond('Follow-up', session, task='questions', document_ids=[docs[0]['id']]))
    assert captured[-1]['history']
    asyncio.run(service.respond('Other document', session, task='questions', document_ids=[docs[1]['id']]))
    assert captured[-1]['history'] == []
    asyncio.run(service.respond('No selection', session, task='questions', document_ids=[]))
    assert captured[-1]['history'] == []
    assert captured[-1]['items'] == captured[-1]['documents'] == []
