"""Small HTTP adapters. The same services power UI, assistant and MCP."""
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter,Request,UploadFile,File,HTTPException
from fastapi.responses import FileResponse,Response
from .. import __version__
from ..platform.secrets import SecretStore
from ..providers.contracts import ProviderProfile
from ..providers.settings import ProviderSettings
from ..providers.factory import create_provider

router=APIRouter(prefix='/api')

@router.get('/session')
def session(r:Request):
    response=__import__('fastapi').responses.JSONResponse({'csrf':r.app.state.csrf})
    response.set_cookie('scpublic_session',r.app.state.session,httponly=True,samesite='strict')
    return response

@router.get('/health')
def health(r:Request):return {'application':'StudyCenter.Public','instance_id':r.app.state.paths.instance_id,'version':__version__}

@router.get('/items')
def items(r:Request,q:str='',kind:str='',archive:bool=False):
    values=r.app.state.db.items(query=q or None,kind=kind or None)
    return [i for i in values if (i.get('status') in ('done','ignored'))==archive]

@router.get('/items/{item_id}')
def item(r:Request,item_id:int):
    value=r.app.state.db.item(item_id)
    if not value:raise KeyError(item_id)
    return value

@router.post('/actions/preview')
async def preview(r:Request):return r.app.state.actions.preview((await r.json())['actions'])

@router.post('/actions')
async def actions(r:Request):
    data=await r.json()
    return r.app.state.actions.execute(data['actions'],data['request_id'],data.get('expected_versions'))

@router.post('/actions/undo')
async def undo(r:Request):
    data=await r.json();return r.app.state.actions.undo(data['request_id'],data['undo_id'])

@router.get('/documents')
def documents(r:Request):
    return [{k:v for k,v in d.items() if k!='original_path'} for d in r.app.state.documents.list_documents()]

@router.post('/documents/import')
async def import_document(r:Request,file:UploadFile=File(...)):
    data=await file.read(64*1024*1024+1)
    name=Path(file.filename or 'Import').name
    result=r.app.state.documents.import_bytes(data,title=name,mime=file.content_type or '')
    return {k:v for k,v in result.items() if k!='original_path'}

@router.get('/documents/{document_id}/original')
def original(r:Request,document_id:int):
    doc=r.app.state.db.document(document_id)
    path=r.app.state.documents.original(document_id)
    # Imported HTML never executes in the application's security origin.
    return FileResponse(path,media_type='application/octet-stream',filename=Path(doc['title']).name,content_disposition_type='attachment')

@router.get('/documents/{document_id}/image/{page}')
def image(r:Request,document_id:int,page:int):return Response(r.app.state.documents.page_image(document_id,page),media_type='image/png')

@router.post('/documents/{document_id}/reread/{page}')
async def reread(r:Request,document_id:int,page:int):
    data=await r.json()
    result=r.app.state.documents.reread_page(document_id,page,ignore_grid=data.get('ignore_grid'))
    return {k:v for k,v in result.items() if k!='original_path'}

@router.get('/search')
def search(r:Request,q:str):
    if len(q)>240:raise ValueError('Suchbegriff zu lang')
    return {'items':r.app.state.db.items(query=q),'documents':r.app.state.documents.search(q)}

def require_content_consent(state):
    profile=state.profiles.active_profile()
    if not profile:raise ValueError('KI-Verbindung noch nicht eingerichtet')
    signature=f'{profile.provider}|{profile.base_url}|{profile.model}|{profile.profile_id}'
    if state.settings.get('cloud_consent')!=signature:
        raise ValueError('Unter KI-Verbindung zuerst die Übertragung der ausgewählten Inhalte an diesen Anbieter bestätigen.')

def launch(state,name,work):
    if name in state.jobs and not state.jobs[name].done():raise ValueError('Dieser Vorgang läuft bereits')
    async def run():
        state.job_status[name]={'state':'working'}
        try:state.job_status[name]={'state':'complete','result':await work}
        except asyncio.CancelledError:state.job_status[name]={'state':'cancelled'};raise
        except Exception as exc:
            from ..providers.contracts import ProviderError
            safe=exc.code if isinstance(exc,ProviderError) else (str(exc) if isinstance(exc,(ValueError,KeyError)) else 'Vorgang fehlgeschlagen; Verbindung und lokale Komponenten prüfen.')
            state.job_status[name]={'state':'failed','error':safe}
    state.jobs[name]=asyncio.create_task(run())
    return {'job':name,'state':'working'}

@router.post('/documents/{document_id}/extract')
async def extract(r:Request,document_id:int):
    s=r.app.state;require_content_consent(s)
    return launch(s,'extract/'+str(document_id),s.extraction.extract(document_id))

@router.get('/jobs')
def jobs(r:Request):return r.app.state.job_status

@router.post('/assistant')
async def assistant(r:Request):
    s=r.app.state;require_content_consent(s);data=await r.json()
    return await s.assistant.respond(data['text'],data.get('session_id'),task=data.get('task','assistant'),direct=s.settings.get('direct_mode',False),document_ids=data.get('document_ids'))

@router.post('/assistant/apply')
async def assistant_apply(r:Request):return r.app.state.assistant.apply((await r.json())['proposal_id'])

@router.get('/providers')
def providers(r:Request):return r.app.state.profiles.status()|{'accounts':r.app.state.oauth.accounts()}

@router.post('/providers')
async def save_provider(r:Request):
    s=r.app.state;data=await r.json()
    if data.pop('session_only',False) and not s.secrets.session_only:
        s.secrets=SecretStore(s.paths.instance_id,session_only=True)
        s.profiles.secrets=s.secrets;s.oauth.secrets=s.secrets
        s.assistant.secrets=s.secrets;s.extraction.secrets=s.secrets
        s.sync.secrets=s.secrets
    key=data.pop('api_key','');consent=data.pop('content_consent',False)
    profile=ProviderProfile(**data)
    s.profiles.save_profile(profile);s.profiles.select(profile.profile_id)
    if key:s.profiles.set_api_key(profile.profile_id,key)
    s.settings.set('cloud_consent',f'{profile.provider}|{profile.base_url}|{profile.model}|{profile.profile_id}' if consent else None)
    return s.profiles.status()

@router.post('/providers/select')
async def select_provider(r:Request):
    s=r.app.state;s.profiles.select((await r.json())['profile_id']);s.settings.set('cloud_consent',None)
    return s.profiles.status()

@router.post('/providers/probe')
async def probe_provider(r:Request):
    s=r.app.state;require_content_consent(s)
    profile=s.profiles.active_profile()
    from ..providers.contracts import ModelRequest,ProviderError
    provider=create_provider(profile,s.secrets,oauth=s.oauth)
    schema={'type':'object','additionalProperties':False,'required':['ok'],'properties':{'ok':{'type':'boolean'}}}
    result=await provider.generate(ModelRequest('assistant','Synthetischer Verbindungstest ohne Studieninhalte. Antworte als JSON mit ok=true.',{'test':'synthetic connection check'},schema,max_input_chars=2000))
    if not result.complete or result.content!={'ok':True}:raise ProviderError('probe_response')
    return {'synthetic_response_valid':True,'provider':result.provider,'model':result.model,'usage':result.usage}

@router.post('/providers/disconnect')
async def disconnect(r:Request):
    s=r.app.state;data=await r.json();pid=data['profile_id']
    profile=next((p for p in s.profiles.list_profiles() if p.profile_id==pid),None)
    if not profile:raise ValueError('Profil fehlt')
    if profile.provider=='chatgpt':await s.oauth.logout(pid)
    else:s.secrets.delete('provider/'+pid)
    s.settings.set('cloud_consent',None)
    return s.profiles.status()

@router.post('/oauth/start')
async def oauth_start(r:Request):
    s=r.app.state;data=await r.json()
    if data.get('session_only') and not s.secrets.persistent:
        s.secrets=SecretStore(s.paths.instance_id,session_only=True)
        s.profiles.secrets=s.secrets;s.oauth.secrets=s.secrets;s.assistant.secrets=s.secrets;s.extraction.secrets=s.secrets
        s.sync.secrets=s.secrets
    attempt=s.oauth.start()
    async def finish():
        account=await s.oauth.wait_for_callback()
        profile=ProviderProfile('chatgpt',profile_id=account['registration_id'])
        s.profiles.save_profile(profile);s.profiles.select(profile.profile_id)
        return account
    launch(s,'oauth',finish())
    from urllib.parse import urlsplit
    if urlsplit(attempt.authorization_url).netloc!='auth.openai.com':raise ValueError('Unerwartetes Anmeldeziel')
    from ..desktop.browser import open_system_browser
    opened=open_system_browser(attempt.authorization_url)
    return {'opened':opened,'status':'Anmeldung im Systembrowser geöffnet.' if opened else 'Der Systembrowser konnte nicht geöffnet werden.'}

@router.get('/sources')
def sources(r:Request):return r.app.state.db.sources()

@router.post('/sources')
async def add_source(r:Request):
    data=await r.json();return r.app.state.sync.register_source(data['name'],data.get('url',''),data.get('kind','web'),data.get('options'),source_id=data.get('source_id'))

@router.post('/sources/{source_id}/login')
async def login(r:Request,source_id:int):
    source=r.app.state.db.source(source_id)
    if not source:raise KeyError(source_id)
    result=await r.app.state.sync.login(source_id)
    return {'opened':result['visible'],'status':result['message']}

@router.post('/browser/close')
async def close_browser(r:Request):await r.app.state.browser.close();return {'closed':True}

@router.get('/sources/{source_id}/status')
def source_status(r:Request,source_id:int):return r.app.state.sync.status(source_id)

@router.post('/mail/configure')
async def mail_configure(r:Request):return r.app.state.sync.configure_mail(await r.json())

@router.post('/mcp/configure')
async def mcp_configure(r:Request):
    s=r.app.state;data=await r.json()
    enabled=data.get('enabled') is True;write=enabled and data.get('write') is True
    if enabled and (data.get('rotate') or not s.secrets.get('mcp/access')):
        import secrets
        token=secrets.token_urlsafe(32);s.secrets.set('mcp/access',token)
    else:token=None
    s.settings.set('mcp_enabled',enabled);s.settings.set('mcp_write',write)
    # Only this explicit credential creation response discloses the new client credential.
    # There is no stored-secret read route.
    return {'enabled':enabled,'write':write,'endpoint':s.endpoint+'/mcp','new_token':token}

@router.post('/backup')
def backup(r:Request):
    from ..services.backup import BackupService
    path=BackupService(r.app.state.db,r.app.state.paths).create()
    return FileResponse(path,media_type='application/zip',filename=path.name)

@router.post('/backup/preview')
async def restore_preview(r:Request,file:UploadFile=File(...)):
    from ..services.backup import BackupService
    s=r.app.state
    data=await file.read(256*1024*1024+1)
    if len(data)>256*1024*1024:raise ValueError('UI-Sicherungsimport auf 256 MiB begrenzt; größere Sicherungen im isolierten Entwicklerweg prüfen.')
    from ..platform.paths import owned_path
    restore_id=str(uuid4());path=owned_path(s.paths.cache/('restore-'+restore_id+'.zip'),s.paths.cache)
    path.write_bytes(data)
    try:preview=BackupService(s.db,s.paths).preview(path)
    except Exception:path.unlink(missing_ok=True);raise
    s.db.set_setting('restore/'+restore_id,{'path':str(path)})
    return {'restore_id':restore_id,'preview':preview}

@router.post('/backup/restore')
async def restore(r:Request):
    from ..services.backup import BackupService
    s=r.app.state;data=await r.json()
    if data.get('confirmed') is not True:raise ValueError('Bewusste Wiederherstellung muss bestätigt sein')
    if any(not job.done() for job in s.jobs.values()):raise ValueError('Laufende Vorgänge zuerst beenden; Wiederherstellung wird danach möglich.')
    saved=s.db.setting('restore/'+data['restore_id'])
    if not saved:raise ValueError('Vorherige Prüfung fehlt')
    path=Path(saved['path']).resolve()
    if path.parent!=s.paths.cache.resolve():raise ValueError('Ungültiger Wiederherstellungsort')
    result=BackupService(s.db,s.paths).restore(path,confirmed=True)
    from ..config import Settings
    s.settings=Settings(s.paths)
    s.settings.set('cloud_consent',None)
    s.profiles=ProviderSettings(s.paths.config/'providers.json',s.secrets)
    s.assistant.profiles=s.profiles;s.extraction.profiles=s.profiles
    path.unlink(missing_ok=True)
    return result

@router.get('/calendar/export')
def calendar_export(r:Request):
    import hashlib
    lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//StudyCenter Public//Personal planning//DE']
    for item in r.app.state.db.items(active_only=True):
        day=item.get('end_date') or item.get('date')
        if not day:continue
        # Personal local export does not create or assert an official booking.
        title=str(item['title']).replace('\\','\\\\').replace('\n','\\n').replace(',','\\,').replace(';','\\;')
        lines+=['BEGIN:VEVENT',f"UID:{r.app.state.paths.instance_id}-{item['id']}@studycenter-public",'DTSTART;VALUE=DATE:'+day.replace('-',''),'SUMMARY:'+title,'DESCRIPTION:Persönliche Planung. Originalquelle in StudyCenter prüfen.','END:VEVENT']
    lines.append('END:VCALENDAR')
    return Response('\r\n'.join(lines)+'\r\n',media_type='text/calendar',headers={'Content-Disposition':'attachment; filename="studycenter-public.ics"'})

@router.post('/sources/{source_id}/discover')
async def discover(r:Request,source_id:int):return launch(r.app.state,'discover/'+str(source_id),r.app.state.sync.discover_courses(source_id))

@router.post('/sources/{source_id}/select')
async def select_courses(r:Request,source_id:int):return r.app.state.sync.select_courses(source_id,(await r.json())['courses'])

@router.post('/sources/{source_id}/sync')
async def sync(r:Request,source_id:int):
    data=await r.json();return launch(r.app.state,'sync/'+str(source_id),r.app.state.sync.sync(source_id,resume=data.get('resume',False)))

@router.post('/sources/{source_id}/cancel')
def cancel(r:Request,source_id:int):r.app.state.sync.cancel(source_id);return {'cancelled':True}

@router.get('/notifications')
def notifications(r:Request):return r.app.state.reminders.notifications()

@router.post('/notifications/read')
async def read_notification(r:Request):r.app.state.reminders.mark_read((await r.json())['job_key']);return {'read':True}

@router.get('/settings')
def settings(r:Request):
    s=r.app.state
    return {'settings':s.settings.values,'data_dir':str(s.paths.data),'secret_storage':s.secrets.status,'reminders':s.reminders.settings()}

@router.post('/settings')
async def settings_write(r:Request):
    s=r.app.state;data=await r.json()
    for key,value in data.items():s.settings.set(key,value)
    return s.settings.values

@router.post('/reminders/settings')
async def reminder_settings(r:Request):return r.app.state.reminders.save_settings(await r.json())

@router.post('/shutdown')
def shutdown(r:Request):r.app.state.stop();return {'stopping':True}

@router.post('/data/remove')
async def remove_own_data(r:Request):
    s=r.app.state;data=await r.json()
    if data.get('confirmed') is not True or data.get('confirmation')!='StudyCenter Public löschen':
        raise ValueError('Endgültige Datenentfernung braucht die angezeigte ausdrückliche Bestätigung')
    if any(not job.done() for job in s.jobs.values()):raise ValueError('Laufende Vorgänge zuerst beenden')
    identity=json.loads((s.paths.data/'instance.json').read_text(encoding='utf-8'))
    if identity.get('application')!='StudyCenter.Public' or identity.get('instance_id')!=s.paths.instance_id:
        raise ValueError('Eigene Public-Instanzkennung passt nicht')
    from ..platform.cleanup import cleanup_preflight
    cleanup_preflight(s.paths,s.secrets,confirmed=True)
    s.purge_requested=True;s.stop()
    return {'stopping':True,'own_data_removal_after_shutdown':True}

@router.get('/components')
def components(r:Request):return r.app.state.components.status()

@router.post('/components/install')
async def components_check(r:Request):
    data=await r.json()
    component=data.get('component')
    if component is None:
        return await asyncio.to_thread(r.app.state.components.verify)
    return await asyncio.to_thread(r.app.state.components.install,component,allow_download=data.get('allow_download',False))
