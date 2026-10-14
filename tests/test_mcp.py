import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from studycenter_public.mcp.server import router
from studycenter_public.storage.db import Database
from studycenter_public.services.actions import ActionService
from test_providers import Secrets


@pytest.fixture
def connected(tmp_path):
    app=FastAPI();app.include_router(router)
    db=Database(tmp_path/'data.sqlite3');secrets=Secrets();secrets.set('mcp/access','synthetic-mcp-token')
    values={}
    app.state.db=db;app.state.secrets=secrets;app.state.actions=ActionService(db)
    app.state.settings=SimpleNamespace(get=lambda key,default=None:values.get(key,default))
    app.state.endpoint='http://127.0.0.1:8767'
    client=TestClient(app,base_url=app.state.endpoint)
    return client,values,db


def call(client,name,args=None,**kwargs):
    return client.post('/mcp',headers={'Authorization':'Bearer synthetic-mcp-token','Accept':'application/json, text/event-stream'},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args or {}}},**kwargs)


def structured(response):return response.json()['result']['structuredContent']


def test_mcp_disabled_and_bearer_and_origin_host_boundaries(connected):
    c,settings,db=connected
    assert call(c,'list_items').status_code==404
    settings['mcp_enabled']=True
    assert c.post('/mcp',json={}).status_code==401
    assert c.post('/mcp',headers={'Authorization':'Bearer wrong'},json={}).status_code==401
    headers={'Authorization':'Bearer synthetic-mcp-token','Origin':'https://evil.invalid'}
    assert c.post('/mcp',headers=headers,json={}).status_code==403
    headers={'Authorization':'Bearer synthetic-mcp-token','Host':'evil.invalid'}
    assert c.post('/mcp',headers=headers,json={}).status_code==403
    assert c.get('/mcp',headers={'Authorization':'Bearer synthetic-mcp-token'}).status_code==405


def test_initialize_notifications_and_tools_readonly_default(connected):
    c,settings,db=connected;settings['mcp_enabled']=True
    init=c.post('/mcp',headers={'Authorization':'Bearer synthetic-mcp-token'},json={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'synthetic','version':'1'}}})
    assert init.json()['result']['protocolVersion']=='2025-11-25'
    listed=c.post('/mcp',headers={'Authorization':'Bearer synthetic-mcp-token'},json={'jsonrpc':'2.0','id':2,'method':'tools/list'})
    names={t['name'] for t in listed.json()['result']['tools']}
    assert {'search','list_items','get_item','get_source'}<=names and 'apply_actions' not in names
    notification=c.post('/mcp',headers={'Authorization':'Bearer synthetic-mcp-token'},json={'jsonrpc':'2.0','method':'notifications/initialized'})
    assert notification.status_code==202 and not notification.content
    bad=c.post('/mcp',headers={'Authorization':'Bearer synthetic-mcp-token','MCP-Protocol-Version':'2099-01-01'},json={'jsonrpc':'2.0','id':3,'method':'ping'})
    assert bad.status_code==400


def test_paginated_items_search_and_safe_source_pages(connected):
    c,settings,db=connected;settings['mcp_enabled']=True
    for i in range(3):db.add_item({'kind':'note','title':f'Note {i}','body':'synthetic text'})
    first=structured(call(c,'list_items',{'limit':2}))
    assert len(first['items'])==2 and first['next_offset']==2
    assert len(structured(call(c,'list_items',{'offset':2,'limit':2}))['items'])==1
    assert len(structured(call(c,'search',{'query':'Note 1'}))['items'])==1
    item=structured(call(c,'get_item',{'item_id':1}))
    assert item['title']=='Note 0' and 'original_path' not in json.dumps(item)
    src=db.upsert_source(name='Synthetic source',metadata={'password':'must-not-leak'})
    db.save_document({'source_id':src['id'],'title':'Old','url':'https://example.invalid','hash':'old','fetched_at':'2026-01-01T00:00:00Z','original_path':'must-not-leak'},pages=[{'text':'old'}])
    latest=db.save_document({'source_id':src['id'],'title':'New','url':'https://example.invalid','hash':'new','fetched_at':'2026-02-01T00:00:00Z'},pages=[{'text':'new'}])
    source=structured(call(c,'get_source',{'source_id':src['id']}))
    assert source['document']['id']==latest['id'] and source['pages'][0]['text']=='new'
    assert 'must-not-leak' not in json.dumps(source)


def test_writes_require_separate_enable_preview_versions_uuid_retry(connected):
    c,settings,db=connected;settings['mcp_enabled']=True
    actions=[{'type':'create_note','title':'Explicit note'}]
    denied=call(c,'apply_actions',{'actions':actions,'request_id':str(uuid4()),'confirmed':True})
    assert denied.json()['result']['isError'] and db.items()==[]
    settings['mcp_write']=True
    preview=structured(call(c,'preview_actions',{'actions':actions}))
    assert preview['preview'] and db.items()==[]
    request=str(uuid4())
    missing=call(c,'apply_actions',{'actions':actions,'request_id':request})
    assert missing.json()['result']['isError'] and db.items()==[]
    args={'actions':actions,'request_id':request,'confirmed':True}
    one=structured(call(c,'apply_actions',args));two=structured(call(c,'apply_actions',args))
    assert one==two and len(db.items())==1
    rejected=call(c,'apply_actions',{'actions':[{'type':'decide_source','item_id':1,'field':'date','value':'2026-10-20','document_id':1,'reason':'untrusted'}],'request_id':str(uuid4()),'confirmed':True})
    assert rejected.json()['result']['isError'] and db.item(1)['version']==1
    update=[{'type':'update','item_id':1,'fields':{'title':'Revised'}}]
    no_version=call(c,'apply_actions',{'actions':update,'request_id':str(uuid4()),'confirmed':True})
    assert no_version.json()['result']['isError']
    good=structured(call(c,'apply_actions',{'actions':update,'request_id':str(uuid4()),'confirmed':True,'expected_versions':{'1':1}}))
    assert good['state']=='applied' and db.item(1)['title']=='Revised'


def test_unknown_secret_network_tools_and_bounds_are_rejected(connected):
    c,settings,db=connected;settings['mcp_enabled']=True
    for name in ('get_secret','sync_source','run_shell'):
        assert call(c,name).json()['result']['isError']
    assert call(c,'list_items',{'limit':10000}).json()['result']['isError']
    assert call(c,'search',{'query':'x'*1000}).json()['result']['isError']
    too_big=c.post('/mcp',headers={'Authorization':'Bearer synthetic-mcp-token'},content=b'x'*150_001)
    assert too_big.status_code==413
