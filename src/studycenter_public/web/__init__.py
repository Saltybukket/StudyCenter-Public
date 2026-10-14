"""Local same-origin UI and shared service composition."""
import asyncio
from contextlib import asynccontextmanager,suppress
from pathlib import Path
import secrets as random_secrets
from urllib.parse import urlsplit
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from ..config import Settings
from ..platform.secrets import SecretStore
from ..storage.db import Database
from ..services.actions import ActionService
from ..services.documents import DocumentService
from ..services.reminders import ReminderService
from ..providers.settings import ProviderSettings
from ..providers.oauth import ChatGPTAuth
from ..services.assistant import AssistantService
from ..services.extraction import ExtractionService

def create_app(paths,secrets=None):
    @asynccontextmanager
    async def lifespan(app):
        async def reminders():
            from datetime import datetime,timezone
            while True:
                notifications=app.state.reminders.run_due(datetime.now(timezone.utc))
                if notifications:
                    from ..platform.notifications import notify
                    for item in notifications:notify(item,app.state.endpoint)
                await asyncio.sleep(30)
        worker=asyncio.create_task(reminders())
        try:yield
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):await worker
            for job in list(app.state.jobs.values()):job.cancel()
            if app.state.jobs:await asyncio.gather(*app.state.jobs.values(),return_exceptions=True)
            browser=getattr(app.state,'browser',None)
            if browser:await browser.close()
    app=FastAPI(title='StudyCenter Public',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    state=app.state
    state.paths=paths;state.secrets=secrets or SecretStore(paths.instance_id)
    from ..platform.components import ComponentService
    state.components=ComponentService(paths)
    state.db=Database(paths.database);state.settings=Settings(paths)
    state.csrf=random_secrets.token_urlsafe(32);state.session=random_secrets.token_urlsafe(32)
    state.endpoint='http://127.0.0.1:8767';state.stop=lambda:None
    state.actions=ActionService(state.db);state.documents=DocumentService(state.db,paths)
    state.reminders=ReminderService(state.db)
    state.profiles=ProviderSettings(paths.config/'providers.json',state.secrets)
    state.oauth=ChatGPTAuth(state.secrets,paths.host_id)
    state.assistant=AssistantService(state.db,state.actions,state.profiles,state.secrets,state.oauth,documents=state.documents)
    state.extraction=ExtractionService(state.db,state.profiles,state.secrets,state.oauth,documents=state.documents)
    state.jobs={};state.job_status={}
    from ..collectors.browser import BrowserManager
    from ..services.sync import SyncService
    state.browser=BrowserManager(paths)
    state.sync=SyncService(state.db,paths,browser=state.browser,secrets=state.secrets,documents=state.documents)

    @app.middleware('http')
    async def local_boundary(request:Request,call_next):
        expected=urlsplit(state.endpoint)
        host=request.headers.get('host','')
        allowed={expected.netloc,f'localhost:{expected.port}'}
        if host not in allowed:return JSONResponse({'error':'Unerwarteter Host'},status_code=400)
        if request.method not in ('GET','HEAD','OPTIONS') and not request.url.path.startswith('/mcp'):
            origin=request.headers.get('origin','')
            permitted={state.endpoint,f'http://localhost:{expected.port}'}
            if (origin not in permitted or not random_secrets.compare_digest(request.headers.get('x-csrf-token',''),state.csrf)
                or not random_secrets.compare_digest(request.cookies.get('scpublic_session',''),state.session)):
                return JSONResponse({'error':'Ungültiger lokaler Schreibzugriff'},status_code=403)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        response.headers['Cache-Control']='no-store' if not request.url.path.startswith('/static/') else 'public, max-age=3600'
        return response

    @app.exception_handler(ValueError)
    async def bad_input(request,exc):return JSONResponse({'error':str(exc)},status_code=400)
    @app.exception_handler(KeyError)
    async def not_found(request,exc):return JSONResponse({'error':'Eintrag oder Dokument nicht gefunden'},status_code=404)
    from ..providers.contracts import ProviderError
    @app.exception_handler(ProviderError)
    async def provider_error(request,exc):return JSONResponse({'error':'KI-Verbindung: '+exc.code},status_code=502)
    from ..platform.secrets import SecretStoreUnavailable
    @app.exception_handler(SecretStoreUnavailable)
    async def secure_error(request,exc):return JSONResponse({'error':str(exc)},status_code=503)
    app.mount('/static',StaticFiles(directory=Path(__file__).parent/'static'),name='static')
    from .api import router as api
    from .pages import router as pages
    app.include_router(api);app.include_router(pages)
    from ..mcp.server import router as mcp
    app.include_router(mcp)
    from ..services.speech import router as speech
    app.include_router(speech)
    return app
