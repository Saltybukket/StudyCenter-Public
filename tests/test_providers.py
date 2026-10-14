import asyncio
import json

import httpx
import pytest

from studycenter_public.providers.contracts import ModelRequest, ProviderError, ProviderProfile
from studycenter_public.providers.factory import create_provider
from studycenter_public.providers.settings import ProviderSettings


class Secrets:
    persistent = False
    status = 'session-only'
    def __init__(self): self.values = {}
    def get(self, key): return self.values.get(key)
    def set(self, key, value): self.values[key] = value
    def delete(self, key): self.values.pop(key, None)


SCHEMA = {'type': 'object', 'properties': {'answer': {'type': 'string'}}, 'required': ['answer'], 'additionalProperties': False}


def run(coro): return asyncio.run(coro)


@pytest.mark.parametrize('name,path,payload', [
    ('openai', '/v1/responses', {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"answer":"ok"}'}]}]}),
    ('openai-chat', '/v1/chat/completions', {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"answer":"ok"}'}}]}),
    ('deepseek', '/chat/completions', {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"answer":"ok"}'}}]}),
    ('anthropic', '/v1/messages', {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '{"answer":"ok"}'}]}),
    ('gemini', '/v1beta/models/demo:generateContent', {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': '{"answer":"ok"}'}]}}]}),
    ('ollama', '/api/chat', {'done': True, 'message': {'content': '{"answer":"ok"}'}}),
    ('lmstudio', '/v1/chat/completions', {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"answer":"ok"}'}}]}),
])
@pytest.mark.parametrize('task', ['extraction', 'assistant', 'questions'])
def test_native_generation_validates_all_tasks(name, path, payload, task):
    seen = []
    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=payload)
    secrets = Secrets()
    secrets.set('provider/default', 'synthetic-key')
    base = 'http://127.0.0.1:12345' if name in ('ollama', 'lmstudio') else ''
    provider = create_provider(ProviderProfile(name, 'demo', base), secrets, transport=httpx.MockTransport(handler))
    result = run(provider.generate(ModelRequest(task, 'Answer from source', {'source': 'synthetic'}, SCHEMA)))
    assert result.content == {'answer': 'ok'} and result.complete
    assert seen[0].url.path == path
    assert 'synthetic' in seen[0].content.decode()
    if name == 'gemini': assert seen[0].headers['x-goog-api-key'] == 'synthetic-key'
    if name == 'anthropic': assert seen[0].headers['x-api-key'] == 'synthetic-key'


@pytest.mark.parametrize('status,code', [(401, 'authentication'), (403, 'authentication'), (429, 'rate_limit'), (500, 'unavailable'), (302, 'redirect')])
def test_provider_errors_are_safe_and_no_redirect(status, code):
    count = []
    def handler(req):
        count.append(req)
        return httpx.Response(status, json={'secret': 'must-never-surface'}, headers={'location': 'https://evil.invalid'})
    secrets = Secrets(); secrets.set('provider/default', 'synthetic-key')
    provider = create_provider(ProviderProfile('openai', 'demo'), secrets, transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as error: run(provider.generate(ModelRequest('assistant', 'x', 'x')))
    assert error.value.code == code
    assert 'must-never-surface' not in str(error.value)
    assert len(count) == 1


@pytest.mark.parametrize('payload,code', [
    ({'status': 'incomplete', 'output': []}, 'incomplete'),
    ({'status': 'completed', 'output': [{'content': [{'type': 'output_text', 'text': '{'}]}]}, 'invalid_json'),
    ({'status': 'completed', 'output': [{'content': [{'type': 'output_text', 'text': '{"other":1}'}]}]}, 'schema'),
    ({'status': 'completed', 'output': []}, 'empty'),
])
def test_invalid_output_is_rejected(payload, code):
    secrets = Secrets(); secrets.set('provider/default', 'synthetic-key')
    provider = create_provider(ProviderProfile('openai', 'demo'), secrets, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload)))
    with pytest.raises(ProviderError) as error: run(provider.generate(ModelRequest('extraction', 'x', 'x', SCHEMA)))
    assert error.value.code == code


def test_input_not_silently_truncated_and_no_environment_keys(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'do-not-use')
    seen = []
    provider = create_provider(ProviderProfile('openai', 'demo'), Secrets(), transport=httpx.MockTransport(lambda req: seen.append(req)))
    with pytest.raises(ProviderError) as error: run(provider.generate(ModelRequest('assistant', 'x', 'x')))
    assert error.value.code == 'authentication' and not seen
    with pytest.raises(ProviderError) as error: run(provider.generate(ModelRequest('assistant', 'x', 'x' * 100, max_input_chars=10)))
    assert error.value.code == 'input_limit' and not seen


@pytest.mark.parametrize('url', ['https://user:pass@evil.invalid/v1', 'http://example.com/v1', 'http://169.254.169.254/v1', 'https://example.com/v1?key=abc', 'https://example.com/v1#fragment'])
def test_rejects_unsafe_profile_urls(url):
    with pytest.raises(ValueError): ProviderProfile('compatible', 'demo', url)


def test_profile_settings_keep_credentials_outside_json_and_switch(tmp_path):
    secrets = Secrets(); settings = ProviderSettings(tmp_path / 'providers.json', secrets)
    settings.save_profile(ProviderProfile('openai', 'one', profile_id='first'))
    settings.save_profile(ProviderProfile('anthropic', 'two', profile_id='second'))
    settings.set_api_key('first', 'synthetic-first')
    settings.set_api_key('second', 'synthetic-second')
    settings.select('second')
    assert settings.active_profile().provider == 'anthropic'
    saved = (tmp_path / 'providers.json').read_text()
    assert 'synthetic-' not in saved
    assert 'synthetic-' not in repr(settings.active_profile())
    assert secrets.get('provider/first') == 'synthetic-first'


def test_bounded_response_and_timeout():
    secrets = Secrets(); secrets.set('provider/default', 'synthetic-key')
    provider = create_provider(ProviderProfile('openai', 'demo'), secrets, transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b'x' * 2_100_000)))
    with pytest.raises(ProviderError) as error: run(provider.list_models())
    assert error.value.code == 'response_limit'
    def timeout(req): raise httpx.ReadTimeout('secret-url-token')
    provider = create_provider(ProviderProfile('openai', 'demo'), secrets, transport=httpx.MockTransport(timeout))
    with pytest.raises(ProviderError) as error: run(provider.list_models())
    assert error.value.code == 'timeout' and 'secret-url' not in str(error.value)

@pytest.mark.parametrize('name,body', [
    ('openai-chat', {'choices':[{'finish_reason':'length','message':{'content':'partial'}}]}),
    ('anthropic', {'stop_reason':'max_tokens','content':[{'type':'text','text':'partial'}]}),
    ('gemini', {'candidates':[{'finishReason':'MAX_TOKENS','content':{'parts':[{'text':'partial'}]}}]}),
    ('ollama', {'done':False,'message':{'content':'partial'}}),
])
def test_native_partial_answers_never_succeed(name, body):
    secrets=Secrets(); secrets.set('provider/default','synthetic-key')
    profile=ProviderProfile(name,'demo','http://127.0.0.1:12345' if name=='ollama' else '')
    provider=create_provider(profile,secrets,transport=httpx.MockTransport(lambda req:httpx.Response(200,json=body)))
    with pytest.raises(ProviderError) as error: run(provider.generate(ModelRequest('assistant','x','x')))
    assert error.value.code=='incomplete'


@pytest.mark.parametrize('name,body,path,expected', [
    ('openai',{'data':[{'id':'demo'}]},'/v1/models',[{'id':'demo','name':'demo'}]),
    ('anthropic',{'data':[{'id':'demo','display_name':'Native'}]},'/v1/models',[{'id':'demo','name':'Native'}]),
    ('gemini',{'models':[{'name':'models/demo','displayName':'Native','supportedGenerationMethods':['generateContent']},{'name':'models/embed','supportedGenerationMethods':['embedContent']}]},'/v1beta/models',[{'id':'demo','name':'Native'}]),
    ('ollama',{'models':[{'name':'demo'}]},'/api/tags',[{'id':'demo','name':'demo'}]),
    ('lmstudio',{'data':[{'id':'demo'}]},'/v1/models',[{'id':'demo','name':'demo'}]),
])
def test_native_catalog_and_probe_do_not_generate(name,body,path,expected):
    seen=[]
    def handler(req):
        seen.append(req);return httpx.Response(200,json=body)
    secrets=Secrets(); secrets.set('provider/default','synthetic-key')
    profile=ProviderProfile(name,'demo','http://127.0.0.1:12345' if name in ('ollama','lmstudio') else '')
    provider=create_provider(profile,secrets,transport=httpx.MockTransport(handler))
    assert run(provider.list_models())==expected
    assert run(provider.probe())['models']==expected
    assert all(r.method=='GET' and r.url.path==path for r in seen)


def test_official_gemini_compatibility_and_explicit_own_lan():
    secrets=Secrets(); secrets.set('provider/default','synthetic-key');seen=[]
    def handler(req):
        seen.append(req);return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'ok'}}]})
    provider=create_provider(ProviderProfile('gemini-compatible','demo'),secrets,transport=httpx.MockTransport(handler))
    assert run(provider.generate(ModelRequest('assistant','x','x'))).content=='ok'
    assert seen[0].url.path=='/v1beta/openai/chat/completions'
    with pytest.raises(ValueError):ProviderProfile('ollama','demo','http://192.168.1.10:11434')
    assert ProviderProfile('ollama','demo','http://192.168.1.10:11434',allow_lan=True)


def test_remote_schema_references_fail_before_http():
    seen=[];secrets=Secrets();secrets.set('provider/default','synthetic-key')
    provider=create_provider(ProviderProfile('openai','demo'),secrets,transport=httpx.MockTransport(lambda req:seen.append(req)))
    with pytest.raises(ProviderError) as error:run(provider.generate(ModelRequest('extraction','x','x',{'$ref':'https://evil.invalid/schema'})))
    assert error.value.code=='schema' and not seen


def test_native_catalog_pagination_is_complete():
    seen=[];secrets=Secrets();secrets.set('provider/default','synthetic-key')
    def handler(req):
        seen.append(req)
        if 'pageToken' not in req.url.params:
            return httpx.Response(200,json={'models':[{'name':'models/one'}],'nextPageToken':'synthetic-page'})
        assert req.url.params['pageToken']=='synthetic-page'
        return httpx.Response(200,json={'models':[{'name':'models/two'}]})
    provider=create_provider(ProviderProfile('gemini','demo'),secrets,transport=httpx.MockTransport(handler))
    assert run(provider.list_models())==[{'id':'one','name':'models/one'},{'id':'two','name':'models/two'}]
    assert len(seen)==2


def test_total_request_deadline_is_bounded():
    import asyncio
    from studycenter_public.providers.http import BoundedHTTP
    async def delayed(req):
        await asyncio.sleep(0.1)
        return httpx.Response(200,json={'data':[]})
    http=BoundedHTTP(httpx.MockTransport(delayed),max_seconds=0.01)
    with pytest.raises(ProviderError) as error:run(http.json('GET','https://example.invalid/models'))
    assert error.value.code=='timeout'


def test_malformed_native_response_shapes_are_safe_errors():
    secrets=Secrets();secrets.set('provider/default','synthetic-key')
    provider=create_provider(ProviderProfile('openai','demo'),secrets,transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'status':'completed','output':[42]})))
    with pytest.raises(ProviderError) as error:run(provider.generate(ModelRequest('assistant','x','x')))
    assert error.value.code=='protocol'


def test_actual_extraction_schema_through_service_and_http(tmp_path,monkeypatch):
    from studycenter_public.domain.models import Extraction
    from studycenter_public.services.extraction import ExtractionService
    import studycenter_public.services.extraction as service_module
    from studycenter_public.storage.db import Database
    db=Database(tmp_path/'extract.sqlite3')
    source=db.upsert_source(name='Synthetic source')
    doc=db.save_document({'source_id':source['id'],'url':'https://example.invalid/task','title':'Synthetic course','hash':'synthetic'},pages=[{'page':1,'text':'Submit the assignment by October 20.'}])
    secrets=Secrets();secrets.set('provider/default','synthetic-key')
    settings=ProviderSettings(tmp_path/'providers.json',secrets);settings.save_profile(ProviderProfile('openai','demo'))
    seen=[]
    def handler(req):
        body=json.loads(req.content);seen.append(body)
        assert body['text']['format']=={'type':'json_object'}
        assert '#/$defs/Candidate' in body['instructions']
        output={'items':[{'title':'Assignment','kind':'task','quote':'Submit the assignment by October 20.','page':1,'anchor':'assignment'}]}
        return httpx.Response(200,json={'status':'completed','output':[{'content':[{'type':'output_text','text':json.dumps(output)}]}]})
    monkeypatch.setattr(service_module,'create_provider',lambda profile,secrets,**kwargs:create_provider(profile,secrets,transport=httpx.MockTransport(handler)))
    result=run(ExtractionService(db,settings,secrets).extract(doc['id']))
    assert len(seen)==1 and len(result)==1 and result[0]['evidence'][0]['quote']=='Submit the assignment by October 20.'
    assert Extraction.model_json_schema()['properties']['items']['items']['$ref']=='#/$defs/Candidate'


@pytest.mark.parametrize('task',['assistant','questions'])
def test_actual_action_schema_service_uses_json_mode_and_local_validation(tmp_path,monkeypatch,task):
    from studycenter_public.services.assistant import AssistantService,ACTION_SCHEMA
    import studycenter_public.services.assistant as service_module
    from studycenter_public.services.actions import ActionService
    from studycenter_public.storage.db import Database
    db=Database(tmp_path/'assistant.sqlite3');secrets=Secrets();secrets.set('provider/default','synthetic-key')
    settings=ProviderSettings(tmp_path/'providers.json',secrets);settings.save_profile(ProviderProfile('openai','demo'))
    seen=[]
    def handler(req):
        body=json.loads(req.content);seen.append(body)
        assert body['text']['format']=={'type':'json_object'}
        output={'answer':'No source evidence is available.','actions':[],'question':None,'choices':[],'citations':[]}
        return httpx.Response(200,json={'status':'completed','output':[{'content':[{'type':'output_text','text':json.dumps(output)}]}]})
    monkeypatch.setattr(service_module,'create_provider',lambda profile,secrets,**kwargs:create_provider(profile,secrets,transport=httpx.MockTransport(handler)))
    result=run(AssistantService(db,ActionService(db),settings,secrets).respond('Explain retained evidence',task=task))
    assert result['actions']==[] and result['receipt'] is None and len(seen)==1
    assert ACTION_SCHEMA['properties']['actions']['items']=={'type':'object'}


@pytest.mark.parametrize('schema',[
    {'$defs':{'value':{'$ref':'https://evil.invalid/schema'}},'$ref':'#/$defs/value'},
    {'$defs':{'value':{'$ref':'#/$defs/value'}},'$ref':'#/$defs/value'},
    {'$ref':'#/$defs/missing'},
    {'$id':'https://evil.invalid/schema','type':'object','$ref':'#/$defs/value','$defs':{'value':{'type':'object'}}},
    {'$dynamicRef':'#/$defs/value','$defs':{'value':{'type':'object'}}},
])
def test_unsafe_local_reference_graphs_fail_without_http(schema):
    seen=[];secrets=Secrets();secrets.set('provider/default','synthetic-key')
    provider=create_provider(ProviderProfile('openai','demo'),secrets,transport=httpx.MockTransport(lambda req:seen.append(req)))
    with pytest.raises(ProviderError) as error:run(provider.generate(ModelRequest('extraction','x','x',schema)))
    assert error.value.code=='schema' and not seen


def test_closed_flat_schema_retains_strict_wire_support():
    seen=[];secrets=Secrets();secrets.set('provider/default','synthetic-key')
    def handler(req):
        body=json.loads(req.content);seen.append(body)
        assert body['text']['format']=={'type':'json_schema','name':'result','strict':True,'schema':SCHEMA}
        return httpx.Response(200,json={'status':'completed','output':[{'content':[{'type':'output_text','text':'{"answer":"ok"}'}]}]})
    provider=create_provider(ProviderProfile('openai','demo'),secrets,transport=httpx.MockTransport(handler))
    assert run(provider.generate(ModelRequest('assistant','x','x',SCHEMA))).content=={'answer':'ok'}
    assert len(seen)==1


def test_local_pointer_schema_still_rejects_invalid_model_output():
    schema={'type':'object','properties':{'answer':{'$ref':'#/$defs/value'}},'required':['answer'],'additionalProperties':False,'$defs':{'value':{'type':'string'}}}
    secrets=Secrets();secrets.set('provider/default','synthetic-key')
    provider=create_provider(ProviderProfile('openai','demo'),secrets,transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'status':'completed','output':[{'content':[{'type':'output_text','text':'{"answer":42}'}]}]})))
    with pytest.raises(ProviderError) as error:run(provider.generate(ModelRequest('assistant','x','x',schema)))
    assert error.value.code=='schema'

@pytest.mark.parametrize('graph',['depth','fanout'])
def test_local_schema_expansion_is_bounded_before_http(graph):
    if graph=='depth':
        schema={'type':'string'}
        for _ in range(70):schema={'type':'array','items':schema}
    else:
        definitions={'d0':{'type':'string'}}
        for i in range(1,15):definitions['d'+str(i)]={'allOf':[{'$ref':'#/$defs/d'+str(i-1)},{'$ref':'#/$defs/d'+str(i-1)}]}
        schema={'$defs':definitions,'$ref':'#/$defs/d14'}
    seen=[];secrets=Secrets();secrets.set('provider/default','synthetic-key')
    provider=create_provider(ProviderProfile('openai','demo'),secrets,transport=httpx.MockTransport(lambda req:seen.append(req)))
    with pytest.raises(ProviderError) as error:run(provider.generate(ModelRequest('extraction','x','x',schema)))
    assert error.value.code=='schema' and not seen


def test_local_pointer_allows_property_names_matching_schema_keywords():
    schema={'type':'object','properties':{'$ref':{'$ref':'#/$defs/value'}},'required':['$ref'],'additionalProperties':False,'$defs':{'value':{'type':'string'}}}
    seen=[];secrets=Secrets();secrets.set('provider/default','synthetic-key')
    def handler(req):
        seen.append(req)
        return httpx.Response(200,json={'status':'completed','output':[{'content':[{'type':'output_text','text':'{"$ref":"ok"}'}]}]})
    provider=create_provider(ProviderProfile('openai','demo'),secrets,transport=httpx.MockTransport(handler))
    assert run(provider.generate(ModelRequest('extraction','x','x',schema))).content=={'$ref':'ok'}
    assert len(seen)==1

@pytest.mark.parametrize('clock',['15:00','15:00:00','15:00:00.123456'])
def test_actual_extraction_accepts_quoted_local_clock(tmp_path,monkeypatch,clock):
    from studycenter_public.domain.models import Extraction
    from studycenter_public.services.extraction import ExtractionService
    import studycenter_public.services.extraction as service_module
    from studycenter_public.storage.db import Database
    db=Database(tmp_path/'timed.sqlite3');source=db.upsert_source(name='Synthetic timed source')
    quote='The assignment deadline is October 20 at '+clock+'.'
    doc=db.save_document({'source_id':source['id'],'url':'https://example.invalid/timed','title':'Timed source','hash':'synthetic-timed'},pages=[{'page':1,'text':quote}])
    secrets=Secrets();secrets.set('provider/default','synthetic-key')
    settings=ProviderSettings(tmp_path/'providers.json',secrets);settings.save_profile(ProviderProfile('openai','demo'))
    seen=[]
    def handler(req):
        body=json.loads(req.content);seen.append(body)
        assert 'studycenter-local-time' in body['instructions']
        output={'items':[{'title':'Timed assignment','kind':'task','date':'2026-10-20','time':clock,'date_meaning':'deadline','quote':quote,'page':1,'anchor':'deadline'}]}
        return httpx.Response(200,json={'status':'completed','output':[{'content':[{'type':'output_text','text':json.dumps(output)}]}]})
    monkeypatch.setattr(service_module,'create_provider',lambda profile,secrets,**kwargs:create_provider(profile,secrets,transport=httpx.MockTransport(handler)))
    result=run(ExtractionService(db,settings,secrets).extract(doc['id']))
    assert len(seen)==1 and len(result)==1
    expected='15:00:00' if clock=='15:00' else clock
    assert result[0]['time']==expected and result[0]['evidence'][0]['quote']==quote
    schema=Extraction.model_json_schema()
    assert schema['$defs']['Candidate']['properties']['time']['anyOf'][0]['format']=='studycenter-local-time'


@pytest.mark.parametrize('clock,date,expected_error',[
    ('15:00',None,ValueError),
    ('24:60','2026-10-20',ProviderError),
    ('15:00Z','2026-10-20',ProviderError),
    ('15:00:00+01:00','2026-10-20',ProviderError),
    ('15:00:60','2026-10-20',ProviderError),
])
def test_actual_extraction_rejects_invalid_or_unanchored_local_clock(tmp_path,monkeypatch,clock,date,expected_error):
    from studycenter_public.services.extraction import ExtractionService
    import studycenter_public.services.extraction as service_module
    from studycenter_public.storage.db import Database
    db=Database(tmp_path/'timed-bad.sqlite3');source=db.upsert_source(name='Synthetic source')
    quote='Deadline at '+clock
    doc=db.save_document({'source_id':source['id'],'url':'https://example.invalid/timed','title':'Timed source','hash':'synthetic'},pages=[{'page':1,'text':quote}])
    secrets=Secrets();secrets.set('provider/default','synthetic-key')
    settings=ProviderSettings(tmp_path/'providers.json',secrets);settings.save_profile(ProviderProfile('openai','demo'))
    def handler(req):
        output={'items':[{'title':'Timed assignment','kind':'task','date':date,'time':clock,'quote':quote,'page':1,'anchor':'deadline'}]}
        return httpx.Response(200,json={'status':'completed','output':[{'content':[{'type':'output_text','text':json.dumps(output)}]}]})
    monkeypatch.setattr(service_module,'create_provider',lambda profile,secrets,**kwargs:create_provider(profile,secrets,transport=httpx.MockTransport(handler)))
    with pytest.raises(expected_error):run(ExtractionService(db,settings,secrets).extract(doc['id']))
    assert db.items()==[]


def test_custom_local_time_does_not_weaken_standard_datetime_formats():
    from studycenter_public.providers.schema import validator_for
    from jsonschema.exceptions import ValidationError
    standard=validator_for({'type':'string','format':'date-time'})
    standard.validate('2026-10-20T15:00:00+02:00')
    with pytest.raises(ValidationError):standard.validate('2026-10-20T15:00')
    standard_time=validator_for({'type':'string','format':'time'})
    with pytest.raises(ValidationError):standard_time.validate('15:00')
    local=validator_for({'type':'string','format':'studycenter-local-time'})
    local.validate('15:00')
    for clock in ('24:00','15:60','15:00+02:00','15:00:00.1234567'):
        with pytest.raises(ValidationError):local.validate(clock)

@pytest.mark.parametrize('value',['2026-10-20T15:00:00+01:60','2026-02-30T15:00:00Z','2026-10-20T25:00:00Z'])
def test_datetime_format_rejects_invalid_clock_calendar_and_offset(value):
    from studycenter_public.providers.schema import validator_for
    from jsonschema.exceptions import ValidationError
    with pytest.raises(ValidationError):validator_for({'type':'string','format':'date-time'}).validate(value)
