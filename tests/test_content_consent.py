"""A revoked provider consent stops later chunks before source admission."""
import asyncio
from uuid import uuid4

import pytest

from studycenter_public.platform.secrets import SecretStore
from studycenter_public.providers.contracts import ModelResult,ProviderProfile
from studycenter_public.providers.settings import ProviderSettings
from studycenter_public.services.extraction import ExtractionService
from studycenter_public.storage.db import Database


def test_extraction_rechecks_consent_between_chunks_before_any_ingest(tmp_path,monkeypatch):
    db=Database(tmp_path/'synthetic.sqlite3')
    doc=db.save_document({'title':'Synthetic'},pages=[{'page':1,'text':'First synthetic page'},
                                                       {'page':2,'text':'Second synthetic page'}])
    secrets=SecretStore(str(uuid4()),session_only=True)
    profiles=ProviderSettings(tmp_path/'providers.json',secrets)
    profiles.save_profile(ProviderProfile('openai','synthetic'))
    allowed=[True];calls=[]
    class Provider:
        async def generate(self,request):
            calls.append(request);allowed[0]=False
            return ModelResult({'items':[]},True,'synthetic','synthetic')
    monkeypatch.setattr('studycenter_public.services.extraction.create_provider',lambda *a,**kw:Provider())
    def authorize():
        if not allowed[0]:raise ValueError('Synthetic consent revoked')
    with pytest.raises(ValueError,match='consent revoked'):
        asyncio.run(ExtractionService(db,profiles,secrets).extract(doc['id'],authorize=authorize))
    assert len(calls)==1 and db.items()==[]


@pytest.mark.parametrize('years,success',[([2026,2025],True),([2026,2026],False)])
def test_wrong_year_gets_one_retry_without_partial_ingestion(tmp_path,monkeypatch,years,success):
    db=Database(tmp_path/'synthetic.sqlite3')
    doc=db.save_document({'title':'Synthetic'},pages=[{'page':1,'text':'Abgabe 16.10.2025'}])
    secrets=SecretStore(str(uuid4()),session_only=True)
    profiles=ProviderSettings(tmp_path/'providers.json',secrets)
    profiles.save_profile(ProviderProfile('openai','synthetic'))
    instructions=[]
    class Provider:
        async def generate(self,request):
            instructions.append(request.instructions)
            candidate={'title':'Synthetic','kind':'task','date_meaning':'deadline',
                       'date':f'{years[len(instructions)-1]}-10-16','quote':'Abgabe 16.10.2025','page':1,'anchor':'a'}
            return ModelResult({'items':[candidate]},True,'synthetic','synthetic')
    monkeypatch.setattr('studycenter_public.services.extraction.create_provider',lambda *a,**kw:Provider())
    operation=ExtractionService(db,profiles,secrets).extract(doc['id'])
    if success:
        asyncio.run(operation)
        assert db.items()[0]['date']=='2025-10-16'
    else:
        with pytest.raises(ValueError,match='Datumsjahr widerspricht'):asyncio.run(operation)
        assert db.items()==[]
    assert len(instructions)==2 and 'Datumsjahr widerspricht' in instructions[1]
