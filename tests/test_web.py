from fastapi.testclient import TestClient
from studycenter_public.platform.paths import AppPaths
from studycenter_public.platform.secrets import SecretStore
from studycenter_public.web import create_app

def client(tmp_path):
    paths=AppPaths.create(tmp_path/'public-data')
    app=create_app(paths,SecretStore(paths.instance_id,session_only=True))
    app.state.endpoint='http://127.0.0.1:8767'
    return TestClient(app,base_url=app.state.endpoint)

def test_empty_offline_page_and_cross_origin_write_protection(tmp_path):
    c=client(tmp_path)
    assert c.get('/board').status_code==200
    assert c.get('/api/items').json()==[]
    assert c.post('/api/actions',json={'actions':[]}).status_code==403
    assert c.post('/api/actions',headers={'Origin':'https://unrelated.example'},json={'actions':[]}).status_code==403
    assert c.get('/api/items',headers={'Host':'evil.example'}).status_code==400

def test_preview_create_retry_and_undo_through_http(tmp_path):
    from uuid import uuid4
    c=client(tmp_path)
    token=c.get('/api/session').json()['csrf']
    headers={'Origin':'http://127.0.0.1:8767','X-CSRF-Token':token}
    action={'type':'create_note','title':'Synthetic note'}
    preview=c.post('/api/actions/preview',json={'actions':[action]},headers=headers)
    assert preview.status_code==200
    assert c.get('/api/items').json()==[]
    req={'actions':[action],'request_id':str(uuid4())}
    applied=c.post('/api/actions',json=req,headers=headers)
    assert applied.status_code==200
    assert c.post('/api/actions',json=req,headers=headers).json()==applied.json()
    assert len(c.get('/api/items').json())==1
    result=c.post('/api/actions/undo',json={'request_id':req['request_id'],'undo_id':str(uuid4())},headers=headers)
    assert result.status_code==200
    assert c.get('/api/items').json()==[]

def test_source_login_reports_selected_browser_and_own_profile(tmp_path, monkeypatch):
    c=client(tmp_path)
    token=c.get('/api/session').json()['csrf']
    headers={'Origin':c.app.state.endpoint,'X-CSRF-Token':token}
    source=c.app.state.sync.register_source('Synthetic TU source',kind='teachcenter')
    message='Im vorhandenen System-Chrome mit eigenem Public-Profil tu-browser-system-chrome anmelden.'
    async def own_login(source_id):
        assert source_id==source['id']
        return {'visible':True,'message':message}
    monkeypatch.setattr(c.app.state.sync,'login',own_login)
    response=c.post('/api/sources/'+str(source['id'])+'/login',headers=headers)
    assert response.status_code==200
    assert response.json()=={'opened':True,'status':message}
