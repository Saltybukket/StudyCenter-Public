import asyncio
import json
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from studycenter_public.providers.oauth import ChatGPTAuth
from studycenter_public.providers.contracts import ProviderError, ModelRequest, ProviderProfile
from studycenter_public.providers.factory import create_provider
from test_providers import Secrets, run, SCHEMA


@pytest.fixture
def signer():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key())); jwk.update(kid='test', use='sig', alg='RS256')
    return key, jwk


def make_auth(signer, **changes):
    key, jwk = signer
    secrets = Secrets(); calls = []; state = {}
    def handler(req):
        calls.append(req)
        if req.url.path.endswith('openid-configuration'):
            return httpx.Response(200, json={'issuer':'https://auth.openai.com', 'jwks_uri':'https://auth.openai.com/jwks', 'revocation_endpoint':'https://auth.openai.com/revoke'})
        if req.url.path == '/jwks': return httpx.Response(200, json={'keys':[jwk]})
        if req.url.path.endswith('/oauth/token'):
            claims = {'iss':'https://auth.openai.com', 'sub':'synthetic-subject', 'aud':'oaiapp_synthetic', 'exp':int(time.time())+3600, 'iat':int(time.time()), 'nonce':state['nonce'], 'email':'learner@example.invalid'}
            claims.update(changes.pop('claims', {}))
            body = {'access_token':'synthetic-access', 'refresh_token':'synthetic-refresh', 'id_token':jwt.encode(claims,key,algorithm='RS256',headers={'kid':'test'}), 'token_type':'Bearer','expires_in':3600, 'scope':'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'}
            body.update(changes)
            return httpx.Response(200,json=body)
        if req.url.path == '/revoke': return httpx.Response(200)
        if req.url.path == '/v1/models': return httpx.Response(200,json={'models':[{'slug':'model-a','display_name':'A','visibility':'list'}, {'slug':'hidden','visibility':'hide'}]})
        raise AssertionError(req.url.path)
    auth = ChatGPTAuth(secrets, 'urn:uuid:'+str(uuid4()), transport=httpx.MockTransport(handler))
    attempt = auth.start()
    state.update({k:v[0] for k,v in parse_qs(urlsplit(attempt.authorization_url).query).items()})
    callback = attempt.redirect_uri+'?'+urlencode({'state':state['state'], 'code':'synthetic-code', 'client_id':'oaiapp_synthetic'})
    return auth, attempt, callback, secrets, calls, state


def test_dynamic_registration_pkce_exact_callback_and_scoped_models(signer):
    auth, attempt, callback, secrets, calls, state = make_auth(signer)
    assert state['client_id']=='dynamic_agent_client'
    assert state['agent_name_hint']=='StudyCenter Public'
    assert state['code_challenge_method']=='S256' and state['ext_agent_host_id'].startswith('urn:uuid:')
    assert urlsplit(attempt.redirect_uri).hostname=='127.0.0.1'
    account = run(auth.finish_callback(callback))
    assert account['subject']=='synthetic-subject' and account['plan_enabled']
    assert 'synthetic-access' not in repr(auth) and 'synthetic-access' not in repr(account)
    token_call = next(c for c in calls if c.url.path.endswith('/oauth/token'))
    form = parse_qs(token_call.content.decode())
    assert form['client_id']==['oaiapp_synthetic'] and form['redirect_uri']==[attempt.redirect_uri]
    provider = create_provider(ProviderProfile('chatgpt','model-a', profile_id=account['registration_id']), secrets, oauth=auth, transport=auth.transport)
    assert run(provider.list_models()) == [{'id':'model-a', 'name':'A'}]
    auth.close()


@pytest.mark.parametrize('change', ['state','host','path','client','duplicate'])
def test_bad_callback_rejected_before_token_exchange(signer, change):
    auth, attempt, callback, secrets, calls, state = make_auth(signer)
    if change=='state': callback=callback.replace(state['state'],'wrong')
    if change=='host': callback=callback.replace('127.0.0.1','localhost')
    if change=='path': callback=callback.replace('/auth/callback','/callback')
    if change=='client': callback=callback.replace('oaiapp_synthetic','dynamic_agent_client')
    if change=='duplicate': callback+='&state='+state['state']
    with pytest.raises(ProviderError): run(auth.finish_callback(callback))
    assert not any(c.url.path.endswith('/oauth/token') for c in calls)
    auth.close()


@pytest.mark.parametrize('claims', [{'nonce':'wrong'}, {'aud':'other'}, {'iss':'https://evil.invalid'}, {'exp':1}, {'sub':''}])
def test_id_token_claim_checks(signer, claims):
    auth, attempt, callback, secrets, calls, state = make_auth(signer, claims=claims)
    with pytest.raises(ProviderError): run(auth.finish_callback(callback))
    assert auth.accounts()==[]
    auth.close()


def test_missing_plan_scope_does_not_authorize_inference(signer):
    auth, attempt, callback, secrets, calls, state = make_auth(signer, scope='openid profile email')
    account=run(auth.finish_callback(callback))
    assert not account['plan_enabled']
    with pytest.raises(ProviderError) as error: run(auth.access_token(account['registration_id']))
    assert error.value.code=='permission'
    auth.close()


def test_refresh_rotation_logout_retains_registration(signer):
    auth, attempt, callback, secrets, calls, state = make_auth(signer)
    account=run(auth.finish_callback(callback)); registration=account['registration_id']
    run(auth.refresh(registration))
    form = parse_qs([c for c in calls if c.url.path.endswith('/oauth/token')][-1].content.decode())
    assert form['grant_type']==['refresh_token'] and 'scope' not in form
    result=run(auth.logout(registration))
    assert result['remote_revocation_confirmed']
    assert auth.accounts()[0]['authenticated'] is False
    assert auth.accounts()[0]['client_id']=='oaiapp_synthetic'
    later=auth.start(registration_id=registration)
    query=parse_qs(urlsplit(later.authorization_url).query)
    assert query['client_id']==['oaiapp_synthetic'] and 'agent_name_hint' not in query and 'id_token_hint' not in query
    auth.close()


@pytest.mark.parametrize('tail,code', [('', 'incomplete'), ('data: {"type":"response.incomplete"}\n\n','incomplete'), ('data: {"type":"response.failed","response":{"error":{"code":"subscription_sharing_usage_limit_exceeded"}}}\n\n','rate_limit'), ('data: invalid\n\n','protocol')])
def test_oauth_response_stream_requires_terminal_success(signer, tail, code):
    auth, attempt, callback, secrets, calls, state = make_auth(signer)
    account=run(auth.finish_callback(callback))
    body='data: {"type":"response.output_text.delta","delta":"partial"}\n\n'+tail
    def handler(req):
        sent=json.loads(req.content); assert sent['stream'] is True and sent['store'] is False
        return httpx.Response(200,content=body,headers={'content-type':'text/event-stream'})
    provider=create_provider(ProviderProfile('chatgpt','model-a',profile_id=account['registration_id']), secrets, oauth=auth, transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as error: run(provider.generate(ModelRequest('assistant','x','x')))
    assert error.value.code==code
    auth.close()


def test_completed_oauth_stream_schema_validation(signer):
    auth, attempt, callback, secrets, calls, state = make_auth(signer)
    account=run(auth.finish_callback(callback))
    events=[{'type':'response.output_text.delta','delta':'{"answer":"ok"}'},{'type':'response.completed','response':{'status':'completed','usage':{'output_tokens':3}}}]
    body=''.join('data: '+json.dumps(e)+'\n\n' for e in events)
    provider=create_provider(ProviderProfile('chatgpt','model-a',profile_id=account['registration_id']),secrets,oauth=auth,transport=httpx.MockTransport(lambda req:httpx.Response(200,content=body,headers={'content-type':'text/event-stream'})))
    assert run(provider.generate(ModelRequest('questions','x','x',SCHEMA))).content=={'answer':'ok'}
    auth.close()


def test_invalid_signature_cannot_install_credentials(signer):
    key,jwk=signer
    auth,attempt,callback,secrets,calls,state=make_auth(signer)
    other_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    bad=jwt.encode({'iss':'https://auth.openai.com','sub':'synthetic-subject','aud':'oaiapp_synthetic','iat':int(time.time()),'exp':int(time.time())+3600,'nonce':state['nonce']},other_key,algorithm='RS256',headers={'kid':'test'})
    original=auth.http.transport
    def handler(req):
        if req.url.path.endswith('/oauth/token'):return httpx.Response(200,json={'access_token':'fake','refresh_token':'fake','id_token':bad,'token_type':'Bearer','expires_in':3600,'scope':'openid'})
        return original.handle_request(req)
    auth.http.transport=httpx.MockTransport(handler)
    with pytest.raises(ProviderError) as error:run(auth.finish_callback(callback))
    assert error.value.code=='id_token' and not auth.accounts()
    auth.close()


def test_access_denied_and_callback_replay_do_not_exchange(signer):
    auth,attempt,callback,secrets,calls,state=make_auth(signer)
    denied=attempt.redirect_uri+'?'+urlencode({'state':state['state'],'error':'access_denied'})
    with pytest.raises(ProviderError) as error:run(auth.finish_callback(denied))
    assert error.value.code=='access_denied'
    with pytest.raises(ProviderError):run(auth.finish_callback(callback))
    assert not any(c.url.path.endswith('/oauth/token') for c in calls)
    auth.close()


def test_refresh_missing_rotating_token_preserves_old_credentials(signer):
    auth,attempt,callback,secrets,calls,state=make_auth(signer)
    account=run(auth.finish_callback(callback));identifier=account['registration_id']
    before=secrets.get('chatgpt/registrations')
    original=auth.http.transport
    def handler(req):
        if req.url.path.endswith('/oauth/token'):return httpx.Response(200,json={'access_token':'new','token_type':'Bearer','expires_in':3600})
        return original.handle_request(req)
    auth.http.transport=httpx.MockTransport(handler)
    with pytest.raises(ProviderError):run(auth.refresh(identifier))
    assert secrets.get('chatgpt/registrations')==before
    auth.close()


def test_refresh_lost_permission_does_not_issue_access_token(signer):
    auth,attempt,callback,secrets,calls,state=make_auth(signer)
    account=run(auth.finish_callback(callback));identifier=account['registration_id']
    records=json.loads(secrets.get('chatgpt/registrations'));records[identifier]['expires_at']=1
    secrets.set('chatgpt/registrations',json.dumps(records))
    original=auth.http.transport
    def handler(req):
        if req.url.path.endswith('/oauth/token'):return httpx.Response(200,json={'access_token':'new','refresh_token':'new-refresh','token_type':'Bearer','expires_in':3600,'scope':'openid'})
        return original.handle_request(req)
    auth.http.transport=httpx.MockTransport(handler)
    with pytest.raises(ProviderError) as error:run(auth.access_token(identifier))
    assert error.value.code=='permission'
    auth.close()


def test_logout_network_failure_clears_tokens_and_reports_unconfirmed(signer):
    auth,attempt,callback,secrets,calls,state=make_auth(signer)
    account=run(auth.finish_callback(callback));identifier=account['registration_id']
    original=auth.http.transport
    def handler(req):
        if req.url.path=='/revoke':raise httpx.ConnectError('synthetic')
        return original.handle_request(req)
    auth.http.transport=httpx.MockTransport(handler)
    assert run(auth.logout(identifier))=={'signed_out':True,'remote_revocation_confirmed':False}
    saved=secrets.get('chatgpt/registrations')
    assert 'synthetic-access' not in saved and 'synthetic-refresh' not in saved
    assert auth.accounts()[0]['client_id']=='oaiapp_synthetic'
    auth.close()


def test_loopback_callback_listener_and_completion(signer):
    auth,attempt,callback,secrets,calls,state=make_auth(signer)
    async def complete():
        async with httpx.AsyncClient(trust_env=False,follow_redirects=False) as client:
            rejected=await client.get(attempt.redirect_uri,headers={'Host':'localhost'})
            assert rejected.status_code==400
            received=await client.get(callback)
            assert received.status_code==200 and received.headers['cache-control']=='no-store'
        return await auth.wait_for_callback(timeout=5)
    try:
        account=run(complete())
        assert account['subject']=='synthetic-subject'
    finally:auth.close()


def test_returning_account_client_and_subject_cannot_be_replaced(signer):
    auth,attempt,callback,secrets,calls,state=make_auth(signer)
    account=run(auth.finish_callback(callback))
    attempt=auth.start(registration_id=account['registration_id'])
    params=parse_qs(urlsplit(attempt.authorization_url).query)
    assert 'id_token_hint' in params and 'id_token_hint' not in repr(attempt)
    callback=attempt.redirect_uri+'?'+urlencode({'state':params['state'][0],'code':'synthetic','client_id':'oaiapp_other'})
    before=secrets.get('chatgpt/registrations');call_count=len(calls)
    with pytest.raises(ProviderError):run(auth.finish_callback(callback))
    assert before==secrets.get('chatgpt/registrations') and len(calls)==call_count
    auth.close()
