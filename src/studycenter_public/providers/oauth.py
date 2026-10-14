"""Independent public-client Sign in with ChatGPT implementation.

Credentials stay in the installation's SecretStore. The host identity is supplied
by AppPaths; browser credentials or another application's client IDs are never read.
"""
import asyncio
import base64
from dataclasses import dataclass, field
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import queue
import secrets as random_secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID, uuid4

import jwt
from .contracts import ProviderError
from .http import BoundedHTTP

ISSUER='https://auth.openai.com'
RESOURCE='https://api.openai.com/v1'
AUTHORIZE=ISSUER+'/api/accounts/authorize'
TOKEN=ISSUER+'/api/accounts/oauth/token'
SCOPES='openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'


@dataclass(repr=False)
class SignInAttempt:
    authorization_url: str
    redirect_uri: str
    state: str = field(repr=False)
    nonce: str = field(repr=False)
    verifier: str = field(repr=False)
    client_id: str
    registration_id: str | None
    expires_at: float
    def __repr__(self): return 'SignInAttempt(callback='+self.redirect_uri+')'


class ChatGPTAuth:
    def __init__(self, secrets, host_id, *, transport=None):
        try:
            if not host_id.startswith('urn:uuid:'): raise ValueError()
            UUID(host_id[9:])
        except (ValueError,AttributeError): raise ValueError('Stable urn:uuid host ID required') from None
        self.secrets=secrets; self.host_id=host_id; self.transport=transport
        self.http=BoundedHTTP(transport,max_bytes=500_000)
        self._pending=None; self._server=None; self._thread=None
        self._callbacks=queue.Queue(maxsize=1); self._locks={}
        self._active=None

    def __repr__(self): return 'ChatGPTAuth(credential_storage='+str(self.secrets.status)+')'

    def _records(self):
        raw=self.secrets.get('chatgpt/registrations')
        try:
            data=json.loads(raw) if raw else {}
            if not isinstance(data,dict): raise ValueError()
            return data
        except (ValueError,TypeError): raise ProviderError('credential_storage') from None

    def _record(self, registration_id):
        record=self._records().get(registration_id)
        if not isinstance(record,dict): raise ProviderError('authentication')
        return record

    def _store(self, registration_id, record):
        records=self._records(); records[registration_id]=record
        self.secrets.set('chatgpt/registrations',json.dumps(records))

    @staticmethod
    def _safe(registration_id, record):
        return {'registration_id':registration_id,'client_id':record['client_id'],'subject':record['subject'],
                'email':record.get('email',''),'label':record.get('label',registration_id[:8]),
                'workspace_binding':record['client_id'],
                'authenticated':bool(record.get('access_token')),
                'plan_enabled':'chatgpt.tokens.use.direct' in record.get('scopes',[]) and 'resource.invoke' in record.get('scopes',[])}

    def accounts(self):
        return [self._safe(identifier, record) for identifier,record in self._records().items()]

    def select(self, registration_id):
        record=self._record(registration_id)
        if not record.get('access_token'): raise ProviderError('authentication')
        self._active=registration_id
        return self._safe(registration_id,record)

    def start(self, port=0, registration_id=None):
        self.close()
        record=self._record(registration_id) if registration_id else None
        callbacks=queue.Queue(maxsize=1); self._callbacks=callbacks
        class CallbackHandler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(handler):
                authority='127.0.0.1:'+str(handler.server.server_address[1])
                if handler.headers.get('Host')!=authority or urlsplit(handler.path).path!='/auth/callback':
                    handler.send_error(400); return
                try: callbacks.put_nowait('http://'+authority+handler.path)
                except queue.Full: handler.send_error(409); return
                handler.send_response(200); handler.send_header('Content-Type','text/plain; charset=utf-8')
                handler.send_header('Cache-Control','no-store'); handler.end_headers()
                handler.wfile.write(b'You may close this window. Check the application for the sign-in result.')
        try: server=HTTPServer(('127.0.0.1',port),CallbackHandler)
        except OSError: raise ProviderError('callback_binding') from None
        server.timeout=0.1
        self._server=server
        self._thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=0.05),daemon=True)
        self._thread.start()
        redirect='http://127.0.0.1:'+str(server.server_address[1])+'/auth/callback'
        state=random_secrets.token_urlsafe(32); nonce=random_secrets.token_urlsafe(32); verifier=random_secrets.token_urlsafe(48)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        client=record['client_id'] if record else 'dynamic_agent_client'
        params={'client_id':client,'ext_agent_host_id':self.host_id,'response_type':'code','redirect_uri':redirect,'scope':SCOPES,
                'resource':RESOURCE,'state':state,'nonce':nonce,'code_challenge_method':'S256','code_challenge':challenge}
        if not record: params['agent_name_hint']='StudyCenter Public'
        else:
            if record.get('id_token'): params['id_token_hint']=record['id_token']
            if record.get('email'): params['login_hint']=record['email']
        self._pending=SignInAttempt(AUTHORIZE+'?'+urlencode(params),redirect,state,nonce,verifier,client,registration_id,time.time()+300)
        return self._pending

    async def wait_for_callback(self, timeout=300):
        deadline=time.monotonic()+min(timeout,300)
        while time.monotonic()<deadline:
            try: callback=self._callbacks.get_nowait()
            except queue.Empty: await asyncio.sleep(0.1); continue
            return await self.finish_callback(callback)
        self.close(); raise ProviderError('callback_timeout')

    def close(self):
        self._pending=None
        if self._server:
            self._server.shutdown(); self._server.server_close(); self._server=None
        if self._thread:
            self._thread.join(timeout=1); self._thread=None

    @staticmethod
    def _trusted_url(url):
        parts=urlsplit(url)
        if parts.scheme!='https' or parts.hostname!='auth.openai.com' or parts.port not in (None,443) or parts.username or parts.password or parts.fragment:
            raise ProviderError('discovery')
        return url

    async def _discovery(self):
        metadata=await self.http.json('GET',ISSUER+'/.well-known/openid-configuration')
        if metadata.get('issuer')!=ISSUER: raise ProviderError('discovery')
        return metadata

    async def _validate_id(self, token, client_id, nonce=None, expected_subject=None):
        if not isinstance(token,str) or len(token)>50_000: raise ProviderError('id_token')
        metadata=await self._discovery()
        try: uri=self._trusted_url(metadata['jwks_uri'])
        except (KeyError,ValueError): raise ProviderError('discovery') from None
        jwks=await self.http.json('GET',uri)
        try:
            header=jwt.get_unverified_header(token)
            if header.get('alg') not in ('RS256','ES256') or not header.get('kid'): raise ValueError()
            matches=[key for key in jwks['keys'] if key.get('kid')==header['kid'] and key.get('use','sig')=='sig' and key.get('alg',header['alg'])==header['alg']]
            if len(matches)!=1: raise ValueError()
            key=jwt.PyJWK.from_dict(matches[0],algorithm=header['alg']).key
            claims=jwt.decode(token,key,algorithms=[header['alg']],audience=client_id,issuer=ISSUER,
                              options={'require':['exp','iat','iss','aud','sub']})
            if not isinstance(claims.get('sub'),str) or not claims['sub']: raise ValueError()
            if nonce is not None and (not isinstance(claims.get('nonce'),str) or not random_secrets.compare_digest(claims['nonce'],nonce)): raise ValueError()
            if expected_subject is not None and claims['sub']!=expected_subject: raise ValueError()
            if isinstance(claims['aud'],list) and len(claims['aud'])>1 and claims.get('azp')!=client_id: raise ValueError()
            return claims
        except (jwt.PyJWTError,ValueError,TypeError,KeyError): raise ProviderError('id_token') from None

    async def finish_callback(self, callback_url):
        attempt=self._pending
        if attempt is None or time.time()>attempt.expires_at:
            self.close(); raise ProviderError('callback_expired')
        # Consume one authorization attempt before any token request.
        self._pending=None
        try:
            url=urlsplit(callback_url)
            expected=urlsplit(attempt.redirect_uri)
            if url.scheme!=expected.scheme or url.netloc!=expected.netloc or url.path!=expected.path or url.fragment: raise ProviderError('callback_binding')
            params=parse_qs(url.query,keep_blank_values=True)
            if any(len(values)!=1 for values in params.values()): raise ProviderError('callback')
            if not random_secrets.compare_digest(params.get('state',[''])[0],attempt.state): raise ProviderError('callback_state')
            if 'error' in params: raise ProviderError('access_denied')
            code=params.get('code',[''])[0]; client=params.get('client_id',[attempt.client_id])[0]
            if not code or len(code)>10_000 or not client or client=='dynamic_agent_client' or len(client)>200: raise ProviderError('registration')
            if attempt.registration_id and client!=attempt.client_id: raise ProviderError('registration')
            record=self._record(attempt.registration_id) if attempt.registration_id else None
            tokens=await self.http.json('POST',TOKEN,data={'grant_type':'authorization_code','client_id':client,'code':code,
                                                         'code_verifier':attempt.verifier,'redirect_uri':attempt.redirect_uri,'resource':RESOURCE})
            claims=await self._validate_id(tokens.get('id_token'),client,nonce=attempt.nonce,expected_subject=record['subject'] if record else None)
            identifier=attempt.registration_id or uuid4().hex
            updated=self._token_record(tokens,client,claims,previous=record)
            self._store(identifier,updated); self._active=identifier
            return self._safe(identifier,updated)
        finally: self.close()

    def _token_record(self, tokens, client, claims, previous=None):
        try:
            if not isinstance(tokens.get('access_token'),str) or not tokens['access_token'] or len(tokens['access_token'])>100_000: raise ValueError()
            if tokens.get('token_type','').lower()!='bearer': raise ValueError()
            expires=tokens['expires_in']
            if isinstance(expires,bool) or not isinstance(expires,(int,float)) or not 0<expires<=86400: raise ValueError()
            if 'scope' in tokens:
                if not isinstance(tokens['scope'],str): raise ValueError()
                scopes=tokens['scope'].split()
            elif previous: scopes=previous['scopes']
            else: raise ValueError()
            refresh=tokens.get('refresh_token')
            if previous and (not isinstance(refresh,str) or not refresh): raise ValueError()
            if 'offline_access' in scopes and (not isinstance(refresh,str) or not refresh): raise ValueError()
            earliest=tokens.get('earliest_refresh_at',0)
            if not isinstance(earliest,(int,float)): raise ValueError()
            return {'client_id':client,'subject':claims['sub'],'email':claims.get('email',''), 'issuer':ISSUER,
                    'label':previous.get('label','') if previous else uuid4().hex[:8],
                    'ext_agent_host_id':self.host_id, 'access_token':tokens['access_token'],'refresh_token':refresh,
                    'id_token':tokens.get('id_token',previous.get('id_token') if previous else None),
                    'scopes':scopes,'expires_at':time.time()+expires,'earliest_refresh_at':earliest}
        except (ValueError,KeyError,TypeError): raise ProviderError('token_response') from None

    async def access_token(self, registration_id=None):
        identifier=registration_id or self._active
        record=self._record(identifier)
        if not record.get('access_token'): raise ProviderError('authentication')
        if not {'chatgpt.tokens.use.direct','resource.invoke'} <= set(record.get('scopes',[])): raise ProviderError('permission')
        if record['expires_at']<=time.time()+60:
            await self.refresh(identifier); record=self._record(identifier)
        if not {'chatgpt.tokens.use.direct','resource.invoke'} <= set(record.get('scopes',[])): raise ProviderError('permission')
        return record['access_token']

    async def refresh(self, registration_id=None):
        identifier=registration_id or self._active
        lock=self._locks.setdefault(identifier,asyncio.Lock())
        async with lock:
            record=self._record(identifier)
            if not record.get('refresh_token'): raise ProviderError('authentication')
            if time.time()<record.get('earliest_refresh_at',0): raise ProviderError('refresh_too_early')
            tokens=await self.http.json('POST',TOKEN,data={'grant_type':'refresh_token','client_id':record['client_id'],
                                                         'refresh_token':record['refresh_token'],'resource':RESOURCE})
            if tokens.get('id_token'):
                claims=await self._validate_id(tokens['id_token'],record['client_id'],expected_subject=record['subject'])
            else: claims={'sub':record['subject'],'email':record.get('email','')}
            updated=self._token_record(tokens,record['client_id'],claims,previous=record)
            self._store(identifier,updated)
            return self._safe(identifier,updated)

    async def logout(self, registration_id=None):
        identifier=registration_id or self._active
        lock=self._locks.setdefault(identifier,asyncio.Lock())
        async with lock:
            record=self._record(identifier); confirmed=False
            try:
                if record.get('refresh_token'):
                    metadata=await self._discovery()
                    endpoint=self._trusted_url(metadata['revocation_endpoint'])
                    await self.http.request('POST',endpoint,data={'token':record['refresh_token'],'token_type_hint':'refresh_token','client_id':record['client_id']})
                    confirmed=True
            except (ProviderError,KeyError,ValueError): pass
            finally:
                for key in ('access_token','refresh_token','id_token'): record.pop(key,None)
                self._store(identifier,record)
                if self._active==identifier: self._active=None
                self.close()
            return {'signed_out':True,'remote_revocation_confirmed':confirmed}
