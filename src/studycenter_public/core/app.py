import asyncio
import time as monotonic_clock
import hashlib
import json
import logging
import re
import secrets
import shutil
import sqlite3
import tempfile
import zipfile
import webbrowser
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit

import portalocker
from fastapi import FastAPI, HTTPException, Request, Form, Query
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.gzip import GZipMiddleware, DEFAULT_EXCLUDED_CONTENT_TYPES

from .browser import BrowserService, BrowserUnavailable, LoginRequired
from .collectors import MAX_BYTES, safe_url
from .config import PACKAGE, VIENNA, load_settings
from .db import Database, encode, now
from .sync import SyncService
from .chatgpt import ChatGPTError
from .gitlab import project_url, remote_url, wsl_ssh_from_form
from .mail import MailError, probe as probe_mail
from .backup import MAX_ARCHIVE, create_backup, inspect_archive, apply_pending
from .async_utils import finish_thread, settle_cancelled
from .ical import export_calendar
from .search import SearchIndex, TYPES
from .assistant import Assistant, Command
from .speech import SpeechService, SpeechError, MAX_AUDIO
from .presentation import TAGS, item_tags, personal_state
from .reminders import ReminderService, ReminderSettings, ItemPreferences,rule_label
from .google_calendar import GoogleCalendar, GoogleError, projection
from .tunnel import TunnelService
from .mcp import MCPGateway,issue_credential
from .page_images import PageImages
from .quests import QuestService, QuestError
from .action_state import (KINDS, COURSE_COLORS, course_color, item_timing, action_state, pin_state,
                           available_later, view_action_state, view_pin_state)

STATES = {"new": "Noch nicht abgerufen", "running": "Abruf läuft", "success": "Abruf vollständig", "partial": "Abruf unvollständig", "failed": "Abruf fehlgeschlagen", "login_required": "Anmeldung nötig"}
ANALYSIS = {"pending": "Auswertung ausstehend", "failed": "Auswertung fehlgeschlagen", "partial": "Text ausgewertet · Leselücken", "success": "Ausgewertet", "archived": "Archiv"}
OBLIGATIONS = {'required':'Verpflichtend','recommended':'Empfohlen','optional':'Optional','unclear':'Verpflichtung unklar'}
ITEM_STATES = {'active':'Aktive Quellenangabe','cancelled':'Abgesagt / aufgehoben','unclear':'Quellenangabe unklar'}
WORK_STATES = {'open':'Offen','doing':'In Arbeit','done':'Erledigt'}


def item_title(item):
    return item.get('item_display',item.get('task_display',{})).get('title') or item['data']['title']


def item_summary(item):
    return item.get('item_display',item.get('task_display',{})).get('summary',item['data'].get('description') or '')


def change_values(history):
    labels = {'date':'Datum', 'time':'Uhrzeit', 'end_date':'Schlussdatum', 'end_time':'Schlusszeit',
              'obligation':'Verpflichtung', 'source_state':'Quellenstatus', 'title':'Titel', 'description':'Beschreibung'}
    result = []
    before_data, after_data = history.get('before',{}), history.get('after',{})
    if before_data.get('kind') and after_data.get('kind') and item_timing(before_data)[2] and item_timing(after_data)[2]:
        fields = ('date','time','end_date','end_time')
        if any(before_data.get(f)!=after_data.get(f) for f in fields):
            def span(value):
                closing_date, closing_time, _ = item_timing(value)
                text = 'Bis '+(date.fromisoformat(closing_date).strftime('%d.%m.%Y') if closing_date else 'Datum offen')
                if closing_time: text += ' '+closing_time[:5]
                if value.get('end_date'):
                    text += ' · Beginn '+date.fromisoformat(value['date']).strftime('%d.%m.%Y')
                return text
            result.append({'label':'Frist / Fenster','before':span(before_data),'after':span(after_data)})
        labels = {k:v for k,v in labels.items() if k not in ('date','time','end_date','end_time')}
    for field, label in labels.items():
        before, after = history.get('before',{}).get(field), history.get('after',{}).get(field)
        if before == after:
            continue
        def display(value):
            if not value: return 'Nicht angegeben'
            if field in ('date','end_date'): return date.fromisoformat(value).strftime('%d.%m.%Y')
            if field in ('time','end_time'): return value[:5]
            return str((OBLIGATIONS if field=='obligation' else ITEM_STATES if field=='source_state' else {}).get(value,value))
        result.append({'label':label, 'before':display(before), 'after':display(after)})
    return result


def static_url(filename):
    version = hashlib.sha256((PACKAGE / 'static' / filename).read_bytes()).hexdigest()[:16]
    return f'/static/{filename}?v={version}'


def readable_filename(value):
    """Decode URL-encoded names for display without changing stored evidence."""
    decoded=unquote(value or '')
    return value if any(ord(char)<32 for char in decoded) else decoded


def return_destination(value):
    """Local breadcrumb context only; never turn a query parameter into an external link."""
    fallback=('/documents','Dokumente')
    if not value or len(value)>2000 or not value.startswith('/') or value.startswith('//') or '\\' in value or any(ord(c)<32 for c in value):
        return fallback
    parsed=urlsplit(value)
    if parsed.scheme or parsed.netloc:return fallback
    labels={'/search':'Zurück zur Suche','/documents':'Zurück zu Dokumenten','/board':'Aufgaben & Notizen',
            '/overview':'Übersicht','/calendar':'Kalender','/sources':'Kurse','/':'Timeline'}
    label=labels.get(parsed.path)
    if not label and re.fullmatch(r'/items/[0-9]+',parsed.path):label='Zurück zum Eintrag'
    return (value,label) if label else fallback


def calendar_events(items, *, present=False):
    events = []
    for item in items:
        value = item["data"]
        if item["ignored"] or not value.get("date") or value.get("source_state") == "cancelled" or item.get('calendar_duplicate_of'):
            continue
        if value['kind']=='notice' and value.get('date_meaning')=='none':
            continue
        choice = personal_state(item)
        if value['kind'] in ('exam','event') and choice['offered']:
            continue
        due_date, due_time, closing = item_timing(value)
        deadline_point = closing and value['kind']=='task'
        shown_date, shown_time = (due_date,due_time) if deadline_point else (value['date'],value.get('time'))
        all_day = not shown_time
        start = shown_date + ("T" + shown_time if not all_day else "")
        event = {"id": str(item["id"]), "title": value["title"], "start": start, "allDay": all_day,
                 "url": f"/items/{item['id']}", "extendedProps": {"kind": KINDS.get(value["kind"]), "precision": "Uhrzeit nicht angegeben" if all_day else "Europe/Vienna", "conflict": bool(item["conflict"]),
                    "course": item.get('course_title') or ('Eigene Notiz' if item.get('identity','').startswith('note:') else 'TU-Mail / Dokument'),
                    "dateMeaning": value['date_meaning'], "status": item['status'], "itemKind": value['kind'],
                    "sourceDate": value['date'], "sourceTime": value.get('time'),
                    "sourceEndDate": value.get('end_date'), "sourceEndTime": value.get('end_time'),
                    "description": value.get('description'),
                    "uncertainty": value.get('uncertainty'), 'courseColor':course_color(item.get('course_id')),
                    'closingDate':due_date if closing else None, 'closingTime':due_time if closing else None,
                    'personal':choice['personal'], 'bookingProven':choice['booking_proven'], 'personallyPlanned':choice['personally_planned'], 'obligation':OBLIGATIONS[value['obligation']], 'tags':item_tags(item)},
                 "classNames": ["kind-" + value["kind"], "done" if item["status"] == "done" else ""]}
        if value.get("end_date") and not deadline_point:
            if all_day:
                event["end"] = (date.fromisoformat(value["end_date"]) + timedelta(days=1)).isoformat()
            elif value.get("end_time"):
                event["end"] = value["end_date"] + "T" + value["end_time"]
        if present:
            event['title']=item_title(item)
            event['extendedProps']['description']=item_summary(item)
            display=item.get('task_display',{})
            if display.get('deadline_date') and display.get('deadline_source_id')!=item['id']:
                day,clock=display['deadline_date'],display['deadline_time']
                event.update(start=day+('T'+clock if clock else ''),allDay=not clock)
                event.pop('end',None)
                event['extendedProps'].update(sourceDate=day,sourceTime=clock,sourceEndDate=None,sourceEndTime=None,
                    dateMeaning='deadline',closingDate=day,closingTime=clock,
                    planningDate=display.get('planned_date'),deadlineSourceId=display.get('deadline_source_id'))
        events.append(event)
    return events


def create_app(settings=None):
    settings = settings or load_settings()
    db = Database(settings.data_dir / "campusboard.sqlite3")
    page_images = PageImages(settings.data_dir)
    browser = BrowserService(settings)
    sync = SyncService(db, settings, browser)
    search = SearchIndex(db)
    assistant = Assistant(db, settings, sync.model, search, view_action_state)
    quests = QuestService(db, settings, sync.model)
    speech = SpeechService(lambda: assistant.questions.courses(),engine=settings.stt_engine,name=settings.stt_model,device=settings.stt_device,root=settings.speech_dir,model_path=settings.speech_model_path,ensure_model=settings.ensure_speech_model)
    speech_gate = asyncio.Lock()
    reminders = ReminderService(db,item_timing,adapter=settings.notifications,base_url=lambda:getattr(app.state,'endpoint',''))
    from .class_completion import ClassCompletionService
    class_completion = ClassCompletionService(db)
    def google_projection(options):
        items=db.items()
        def minutes(item):
            jobs,_=reminders.plans(item,datetime.now(VIENNA))
            return [int((datetime.fromisoformat(j['target_at'])-datetime.fromisoformat(j['due_at'])).total_seconds()/60)
                    for j in jobs if j['target_precision']=='datetime' and not j['rule'].startswith('voice:')]
        return projection(items,calendar_events(items),{i['id']:action_state(i,datetime.now(VIENNA)) for i in items},options,options['dataset'],minutes)
    google=GoogleCalendar(db,settings,google_projection)
    tunnel = TunnelService(directory=settings.data_dir / 'tunnel', secret_store=settings.tunnel_store, mcp_store=settings.mcp_store, endpoint=lambda: getattr(app.state, 'endpoint', ''))
    tunnel.ownership_directory=settings.data_dir
    tunnel.bridge_command=settings.bridge_command
    csrf = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        owner = portalocker.Lock(str(settings.data_dir / "app.owner.lock"), timeout=0)
        try:
            owner.acquire()
        except portalocker.exceptions.LockException:
            raise RuntimeError("CampusBoard läuft bereits mit diesem Datenverzeichnis. Die vorhandene App verwenden.") from None
        try:settings.runtime.save_backend_owner(settings.data_dir)
        except OSError:
            owner.release()
            raise
        delivered=db.rows("SELECT * FROM reminder_jobs WHERE state IN ('delivered','quiet','expired')")
        identities={i['id']:i['identity'] for i in db.rows('SELECT id,identity FROM items')}
        restoring=(settings.data_dir/'restore-pending.zip').exists()
        await asyncio.to_thread(apply_pending, db, settings.data_dir)
        db.initialize()  # Restore older snapshots before adding optional local-action receipts.
        google.initialize(restored=restoring)
        if restoring:
            with db.connection() as con:
                for row in delivered:
                    item=con.execute('SELECT identity FROM items WHERE id=?',(row['item_id'],)).fetchone()
                    if item and item['identity']==identities.get(row['item_id']):
                        con.execute('''INSERT INTO reminder_jobs(id,item_id,target_at,due_at,rule,state,updated_at,error) VALUES(?,?,?,?,?,?,?,?)
                            ON CONFLICT(id) DO UPDATE SET item_id=excluded.item_id,target_at=excluded.target_at,
                            due_at=excluded.due_at,rule=excluded.rule,state=excluded.state,updated_at=excluded.updated_at,error=excluded.error''',tuple(row[k] for k in ('id','item_id','target_at','due_at','rule','state','updated_at','error')))
        db.recover_runs()
        try:await asyncio.to_thread(class_completion.tick)
        except Exception as error:class_completion.error=str(error)
        completion_task=asyncio.create_task(class_completion.run(lambda:not sync.active and not assistant.gate.locked()
            and not app.state.maintenance and not (settings.data_dir/'restore-pending.zip').exists() and not app.state.lifecycle_request))
        search_task = asyncio.create_task(asyncio.to_thread(search.refresh))
        reminder_task = asyncio.create_task(reminders.run(lambda:not app.state.maintenance
            and not (settings.data_dir/'restore-pending.zip').exists() and not app.state.lifecycle_request))
        tunnel_task = asyncio.create_task(tunnel.run())
        google_task = asyncio.create_task(google.run(lambda:not sync.active and not assistant.gate.locked()
            and not app.state.maintenance and not (settings.data_dir/'restore-pending.zip').exists() and not app.state.lifecycle_request))
        loop=asyncio.get_running_loop()
        app.state.system_shutdown=lambda:loop.call_soon_threadsafe(begin_stop,True)
        try:
            yield
        finally:
            google.stop.set()
            tunnel.stop_event.set()
            reminders.stop.set()
            class_completion.stop.set()
            try:
                # Windows may impose a short deadline; cancel collectors and
                # release browser profiles before waiting for remote Google I/O.
                for name,close in (('Quellen-Sync',sync.close),('TU-Browser',browser.close)):
                    try:await close()
                    except Exception as error:logging.getLogger(__name__).warning('%s beim Beenden fehlgeschlagen: %r',name,error)
                # Signal every worker before waiting: slow Google I/O must not
                # leave the tunnel monitor or reminder dispatch running.
                names=('Google','Tunnel','Erinnerungen','Suchindex','Lehrtermine abschließen')
                results=await asyncio.gather(google_task,tunnel_task,reminder_task,search_task,completion_task,return_exceptions=True)
                for name,result in zip(names,results):
                    if isinstance(result,BaseException):logging.getLogger(__name__).warning('%s beim Beenden fehlgeschlagen: %r',name,result)
            finally:
                owner.release()

    app = FastAPI(title="CampusBoard", lifespan=lifespan)
    app.state.db, app.state.sync, app.state.browser = db, sync, browser
    app.state.settings, app.state.csrf = settings, csrf
    app.state.search = search
    app.state.assistant, app.state.speech = assistant, speech
    app.state.quests = quests
    app.state.reminders=reminders
    app.state.class_completion=class_completion
    app.state.tunnel=tunnel
    app.state.google=google
    gateway=MCPGateway(app)
    app.state.gateway=gateway
    app.state.maintenance = False
    app.state.instance_id = settings.instance_id
    app.state.lifecycle_request = ''
    app.state.restore_preview = None
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=1,
                       exclude_content_types=DEFAULT_EXCLUDED_CONTENT_TYPES + ('application/octet-stream', 'application/pdf'))
    app.mount("/static", StaticFiles(directory=PACKAGE / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE / "templates")
    templates.env.globals.update(kinds=KINDS, source_states=STATES, analysis_states=ANALYSIS,
                                obligations=OBLIGATIONS, item_states=ITEM_STATES, work_states=WORK_STATES, static_url=static_url,
                                course_color=course_color, change_values=change_values, item_timing=item_timing, item_title=item_title, item_summary=item_summary, available_later=available_later,
                                tag_registry=TAGS, item_tags=item_tags,reminder_rule=rule_label,
                                reminder_states={'pending':'Vorgemerkt','geplant':'Geplant','delivered':'An Windows übergeben','quiet':'Ruhezeit · ausgelassen','expired':'Nicht nachgeholt','cancelled':'Aufgehoben','error':'Windows-Fehler'})
    templates.env.filters['fromjson'] = json.loads
    templates.env.filters['readable_filename'] = readable_filename

    def display_date(value):
        if not value:
            return "—"
        try:
            parsed = datetime.fromisoformat(value)
            if len(value) == 10:
                return parsed.strftime('%d.%m.%Y')
            if parsed.tzinfo:
                parsed = parsed.astimezone(VIENNA)
            return parsed.strftime("%d.%m.%Y %H:%M")
        except ValueError:
            return value
    templates.env.filters["localdate"] = display_date

    # Admission is decided without an await on the event loop. Ordinary writes
    # can overlap, but maintenance starts only after all admitted writes finish.
    admission = {'writers': 0, 'exclusive': False}
    exclusive_paths = {'/settings/backups/create', '/settings/backups/check',
                       '/settings/backups/confirm', '/settings/backups/cancel',
                       '/settings/stop', '/settings/restart'}

    @app.middleware("http")
    async def local_guard(request, call_next):
        writing = request.method not in ("GET", "HEAD", "OPTIONS")
        exclusive = writing and request.url.path in exclusive_paths
        if writing:
            if app.state.maintenance or ((settings.data_dir / 'restore-pending.zip').exists()
                                        and request.url.path not in ('/settings/backups/cancel','/settings/stop','/settings/restart')):
                return JSONResponse({'detail': 'Sicherung läuft oder Wiederherstellung wartet auf Neustart. Bitte Einstellungen öffnen.'}, status_code=409)
            if app.state.lifecycle_request and request.url.path != '/settings/'+app.state.lifecycle_request:
                return JSONResponse({'detail':'CampusBoard wird beendet oder neu gestartet. Bitte kurz warten.'},status_code=409)
            origin = request.headers.get("origin")
            # Sandboxed app browsers have an opaque origin. Only our explicit AJAX
            # header with the unpredictable, current page token proves app access.
            header_token = request.headers.get('x-csrf-token','')
            trusted_opaque = origin == 'null' and bool(header_token) and secrets.compare_digest(header_token,csrf)
            if origin == 'null' and header_token and not trusted_opaque:
                return JSONResponse({'detail': 'App-Verbindung erneuern (lokaler Formularschutz)', 'code': 'csrf_expired'}, status_code=403)
            if origin and urlsplit(origin).netloc != request.headers.get("host") and not trusted_opaque:
                return JSONResponse({"detail": "Fremder Ursprung abgewiesen"}, status_code=403)
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                return JSONResponse({"detail": "Ungültige Anfrage"}, status_code=400)
            maximum = getattr(app.state, 'upload_limits', {}).get(
                request.url.path, MAX_ARCHIVE if request.url.path == '/settings/backups/check' else MAX_BYTES)
            if length > maximum + 65536:
                return JSONResponse({"detail": "Dateigrenze überschritten"}, status_code=413)
            token = request.headers.get("x-csrf-token")
            if not token and "form" in request.headers.get("content-type", ""):
                await request.body()  # Cache for the downstream request as well as the CSRF parser.
                form = await request.form()
                token = form.get("csrf")
            if request.url.path!='/mcp' and (not token or not secrets.compare_digest(str(token), csrf)):
                return JSONResponse({"detail": "Seite neu laden (lokaler Formularschutz)", "code": "csrf_expired"}, status_code=403)
            # Form parsing above can suspend, so recheck after authentication.
            if (app.state.maintenance or admission['exclusive']
                    or ((settings.data_dir / 'restore-pending.zip').exists()
                        and request.url.path not in ('/settings/backups/cancel','/settings/stop','/settings/restart'))
                    or (app.state.lifecycle_request and request.url.path != '/settings/'+app.state.lifecycle_request)
                    or (exclusive and admission['writers'])):
                return JSONResponse({'detail':'Eine Änderung oder Sicherung läuft. Bitte kurz warten.'}, status_code=409)
            if exclusive:
                admission['exclusive'] = True
            else:
                admission['writers'] += 1
        try:
            if writing:
                task = asyncio.create_task(call_next(request))
                try:
                    response = await asyncio.shield(task)
                except asyncio.CancelledError:
                    await settle_cancelled(task)
                    raise
            else:
                response = await call_next(request)
        finally:
            if exclusive:
                admission['exclusive'] = False
            elif writing:
                admission['writers'] -= 1
        if 'text/html' in response.headers.get('content-type', ''):
            response.headers['Cache-Control'] = 'no-store'
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Native local form POSTs need their same-origin Origin header. With
        # no-referrer Chromium sends Origin:null and our guard rejects them.
        # External destinations still receive no referrer.
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        return response

    def render(request, name, **context):
        return templates.TemplateResponse(request=request, name=name, context={"csrf": csrf, "settings": settings, "stage": sync.stage, "path": request.url.path, 'page_key':name.split('.')[0],
            'class_completion_error':class_completion.error, 'restore_pending': (settings.data_dir / 'restore-pending.zip').exists(), 'note_request_id':secrets.token_hex(16), **context})

    app.state.render = render

    def activities():
        return [label for busy,label in ((sync.active,'TU-Quellen-Synchronisierung'),
            (assistant.gate.locked(),'Studi-Anfrage'),
            (quests.lock.locked(),'Quest planen'),
            (class_completion.busy,'Lehrtermine abschließen'),
            (reminders.busy,'Erinnerungen verarbeiten'),
            (bool(sync.import_followup and not sync.import_followup.done()),'Dokumentimport/Auswertung'),
            (speech_gate.locked() or speech.lock.locked(),'Spracherkennung'),
            (google.gate.locked(),'Google-Kalenderabgleich')) if busy]

    def backup_idle(allow_google_stop=False):
        running=[label for label in activities() if label!='Spracherkennung' and (not allow_google_stop or label!='Google-Kalenderabgleich')]
        if running:raise HTTPException(409, 'Gerade läuft: '+', '.join(running)+'. Bitte abschließen lassen.')

    def begin_stop(system=False):
        if app.state.lifecycle_request=='stop':return
        app.state.lifecycle_request='stop'
        google.stop.set();tunnel.stop_event.set();reminders.stop.set();class_completion.stop.set()
        if system:
            if sync.import_followup and not sync.import_followup.done():sync.import_followup.cancel()
            if sync.active:sync.task.cancel()
        (settings.data_dir/'desktop-stop.signal').touch()
        if hasattr(app.state,'stop_callback'):asyncio.get_running_loop().call_later(.1 if system else 1,app.state.stop_callback)

    app.state.begin_stop=begin_stop

    @app.post('/mcp')
    async def mcp_request(request:Request):
        client=gateway.authenticate(request.headers.get('authorization',''))
        if client is None:raise HTTPException(401,'Separater CampusBoard-MCP-Zugang erforderlich.',headers={'WWW-Authenticate':'Bearer'})
        try:message=await request.json()
        except ValueError:return JSONResponse({'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Ungültiges JSON.'}},status_code=400)
        if not isinstance(message,dict):raise HTTPException(400,'Einzelne JSON-RPC-Anfrage erforderlich.')
        result=await gateway.dispatch(message,client)
        return JSONResponse(result) if result is not None else Response(status_code=202)

    @app.get('/mcp')
    async def mcp_no_stream():
        return Response(status_code=405,headers={'Allow':'POST'})

    @app.exception_handler(QuestError)
    async def quest_error(request, error):
        return JSONResponse({'detail':error.message}, status_code=error.status)

    async def quest_input(request):
        try:
            payload = await request.json()
        except (ValueError, UnicodeError):
            raise HTTPException(422, 'Ungültige Quest-Eingabe.') from None
        if not isinstance(payload, dict):
            raise HTTPException(422, 'Ungültige Quest-Eingabe.')
        return payload

    @app.get('/quests')
    async def quest_page(request: Request):
        return render(request, 'quests.html')

    @app.get('/api/quests')
    async def quest_dashboard():
        return JSONResponse(await asyncio.to_thread(quests.dashboard), headers={'Cache-Control':'no-store'})

    @app.get('/api/quests/options')
    async def quest_options():
        return JSONResponse(await asyncio.to_thread(quests.options), headers={'Cache-Control':'no-store'})

    @app.post('/api/quests/preview')
    async def quest_preview(request: Request):
        return await quests.preview(await quest_input(request))

    @app.post('/api/quests/{quest_id}/activate')
    async def quest_activate(quest_id: str, request: Request):
        return await finish_thread(quests.activate, quest_id, await quest_input(request))

    @app.post('/api/quests/{quest_id}/step')
    async def quest_step(quest_id: str, request: Request):
        return await finish_thread(quests.set_step, quest_id, await quest_input(request))

    @app.post('/api/quests/{quest_id}/archive')
    async def quest_archive(quest_id: str, request: Request):
        return await finish_thread(quests.archive, quest_id, await quest_input(request))

    @app.post('/api/quests/{quest_id}/discard')
    async def quest_discard(quest_id: str, request: Request):
        return await finish_thread(quests.discard, quest_id, await quest_input(request))

    @app.get('/api/voice/status')
    async def voice_status():
        active=speech.effective
        return {'model': active.name if active.engine=='whisper' else 'parakeet-tdt-0.6b-v3',
                'local_model': (active.directory / ('model.bin' if active.engine=='whisper' else 'encoder.int8.onnx')).exists(),
                'seconds': 60, 'connected': settings.model_provider=='chatgpt' and settings.model_ready,
                'llm_model': settings.oauth_model, 'stt_busy': speech_gate.locked() or speech.lock.locked(),
                'engine':active.engine,'device':active.device,'stt_warning':speech.warning or active.warning,
                'configured_engine':speech.engine,'configured_model':speech.name,'configured_device':speech.requested_device,
                'stt_phase':speech.phase,'stt_elapsed_seconds':round(monotonic_clock.perf_counter()-speech.started,1) if speech.started is not None else 0,
                'stt_timings':dict(speech.timings)}

    @app.get('/api/reminders')
    async def due_reminders():
        return {'reminders':[], 'native_worker':reminders.running,'error':reminders.error}

    @app.post('/api/reminders/{item_id}/claim')
    async def claim_reminder(item_id:int):
        return {'claimed':False,'message':'Zustellung ausschließlich durch den nativen App-Worker; Browser-Tabs melden nicht doppelt.'}

    @app.post('/api/voice/transcribe')
    async def voice_transcribe(request: Request):
        if request.headers.get('content-type','').split(';')[0] not in ('audio/ogg','audio/webm','audio/wav','audio/x-wav','audio/mp4'):
            raise HTTPException(400,'Bitte eine Ogg-, WebM-, WAV- oder MP4-Audioaufnahme senden.')
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>MAX_AUDIO:
                raise HTTPException(413,'Aufnahme größer als 16 MiB.')
        if speech_gate.locked() or speech.lock.locked():
            raise HTTPException(409,'Eine Transkription läuft bereits. Bitte kurz warten.')
        await speech_gate.acquire()
        task=asyncio.create_task(asyncio.to_thread(speech.transcribe, bytes(raw)))
        # A cancelled HTTP request must not release the engine while its thread
        # is still decoding. The shielded task owns the gate until completion.
        def finished(done):
            speech_gate.release()
            if not done.cancelled():done.exception()
        task.add_done_callback(finished)
        try:
            return await asyncio.shield(task)
        except SpeechError as error:
            raise HTTPException(400,str(error)) from None

    @app.post('/api/voice/command')
    async def voice_command(request: Request):
        from pydantic import ValidationError
        try:
            command = Command.model_validate(await request.json())
            return await assistant.command(command)
        except (ValueError, ValidationError) as error:
            raise HTTPException(400,str(error)[:400]) from None

    @app.post('/api/voice/undo')
    async def voice_undo(request: Request):
        from uuid import UUID
        try:
            payload = await request.json()
            return await assistant.undo(UUID(payload['request_id']), payload.get('confirm') is True)
        except (ValueError,KeyError) as error:
            raise HTTPException(400,str(error)) from None

    @app.get('/search')
    async def global_search(request: Request, q: str = '', course: str = '', content: str = '', history: bool = False, page: int = 1):
        if course and not course.isdigit():
            raise HTTPException(400, 'Ungültiger Kursfilter.')
        content = content if content in TYPES else ''
        q = q.strip()[:240]
        results, total, page = [], 0, max(1, page)
        error = ''
        try:
            if q:
                results, total, page = await asyncio.to_thread(search.search, q, course, content, history, page, prioritize_current=True)
        except (sqlite3.Error, ValueError, OSError) as exception:
            error = search.error or f'Suche nicht verfügbar: {exception}'
        if any(r['kind']=='item' for r in results):
            matched={i['id']:i for i in db.items()}
            for result in results:
                if result['kind']=='item':result['entry']=matched.get(result['entity_id'])
        return render(request, 'search.html', q=q, course=course, content=content, history=history,
                      results=results, total=total, page=page, pages=max(1, (total+19)//20),
                      types=TYPES, courses=db.rows('SELECT * FROM courses ORDER BY active DESC,title'),
                      index_message=search.message, index_error=error or search.error)

    @app.get('/entries')
    @app.get('/archive')
    async def stored_entries(request: Request, view: str = 'updates', course: str = '', q: str = '', include_inactive: bool = False, page: int = 1):
        archived=request.url.path=='/archive'
        view=view if view in ('updates','undated','all') else 'updates'
        q=q.strip()[:240]
        query=q.casefold()
        local_now=datetime.now(VIENNA)
        rows=[i | action_state(i,local_now) for i in db.items(active_only=not include_inactive)
              if not i['ignored'] and not i.get('calendar_duplicate_of')
              and (i['status']=='done' if archived else i['status']!='done')
              and (not course or str(i['course_id'])==course)]
        if not archived:
            if view=='updates':rows=[i for i in rows if not i['seen']]
            elif view=='undated':rows=[i for i in rows if not i['data'].get('date') and not i['data'].get('end_date')]
        if query:rows=[i for i in rows if query in ' '.join([i['data']['title'],i['data'].get('description') or '',i['course_title'] or '']).casefold()]
        total=len(rows);pages=max(1,(total+19)//20);page=max(1,min(page,pages))
        return render(request,'entries.html',archived=archived,view=view,course=course,q=q,include_inactive=include_inactive,
                      items=rows[(page-1)*20:page*20],total=total,page=page,pages=pages,
                      courses=db.rows('SELECT * FROM courses ORDER BY active DESC,title'))

    @app.get('/settings')
    async def settings_page(request: Request):
        result_path = settings.data_dir / 'restore-result.json'
        result = json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else None
        backups = sorted((settings.data_dir / 'backups').glob('CampusBoard-*.zip'), reverse=True)
        return render(request, 'settings.html', preview=app.state.restore_preview,
                      speech_status=await voice_status(),
                      pending=(settings.data_dir / 'restore-pending.zip').exists(), result=result,
                      notification_settings=reminders.settings(),notification_error=reminders.error,notification_running=reminders.running,
                      autostart=settings.runtime.autostart_enabled(),google=google.public(),
                      tunnel=tunnel.public(),
                      connection_tools={client:[t['name'] for t in gateway.tools(client)] for client in ('codex','chatgpt_web','work_local','work_hosted')},
                      connections=gateway.permissions(),connection_proof=json.loads((db.one("SELECT value FROM app_preferences WHERE name='connection_proof'") or {'value':'{}'})['value']),
                      reminder_jobs=db.rows('SELECT j.*,i.payload FROM reminder_jobs j JOIN items i ON i.id=j.item_id ORDER BY j.due_at DESC LIMIT 60'),
                      courses=assistant.questions.courses(),
                      aliases=db.rows('SELECT a.alias,a.course_id,c.title FROM course_aliases a JOIN courses c ON c.id=a.course_id ORDER BY a.alias,c.title'),
                      reminders=db.rows('SELECT r.item_id,r.due_at,r.delivered_at,i.payload FROM personal_reminders r JOIN items i ON i.id=r.item_id ORDER BY r.due_at'),
                      backups=[{'name': p.name, 'size': round(p.stat().st_size / 1024**2, 1)} for p in backups])

    @app.post('/settings/speech')
    async def speech_settings(stt_engine:str=Form(...),stt_model:str=Form(...),stt_device:str=Form(...)):
        nonlocal speech
        try:settings.validate_speech(stt_engine,stt_model,stt_device)
        except ValueError as error:raise HTTPException(400,str(error)) from None
        if speech_gate.locked() or speech.lock.locked():
            raise HTTPException(409,'Eine Transkription läuft bereits. Einstellungen danach ändern.')
        replacement=SpeechService(speech.courses,engine=stt_engine,name=stt_model,device=stt_device,root=settings.speech_dir,model_path=settings.speech_model_path,ensure_model=settings.ensure_speech_model)
        previous=(settings.stt_engine,settings.stt_model,settings.stt_device)
        settings.stt_engine,settings.stt_model,settings.stt_device=stt_engine,stt_model,stt_device
        try:settings.save()
        except OSError:
            settings.stt_engine,settings.stt_model,settings.stt_device=previous
            raise HTTPException(500,'Spracherkennungs-Einstellungen konnten nicht gespeichert werden.') from None
        speech=replacement
        app.state.speech=speech
        return RedirectResponse('/settings#speech',status_code=303)

    @app.post('/settings/aliases')
    async def save_alias(alias:str=Form(...),course_id:int=Form(...),remove:bool=Form(False)):
        from .decision import key
        normalized_alias=key(alias)
        if not normalized_alias or len(normalized_alias)>120 or not db.one('SELECT id FROM courses WHERE id=?',(course_id,)):
            raise HTTPException(400,'Bitte ein gültiges Kürzel und einen vorhandenen Kurs wählen.')
        if remove:db.execute('DELETE FROM course_aliases WHERE alias=? AND course_id=?',(normalized_alias,course_id))
        else:db.execute('INSERT OR IGNORE INTO course_aliases(alias,course_id) VALUES(?,?)',(normalized_alias,course_id))
        return RedirectResponse('/settings',status_code=303)

    @app.post('/settings/reminders/{item_id}/cancel')
    async def cancel_reminder(item_id:int):
        db.execute('DELETE FROM personal_reminders WHERE item_id=?',(item_id,))
        return RedirectResponse('/settings',status_code=303)

    @app.post('/settings/notifications')
    async def save_notifications(request:Request):
        form=await request.form();values={}
        try:
            for field,info in ReminderSettings.model_fields.items():
                if info.annotation is bool:values[field]=form.get(field)=='on'
                elif field.endswith('_minutes'):values[field]=[int(n.strip()) for n in str(form.get(field,'')).split(',') if n.strip()]
                else:values[field]=form.get(field)
            value=ReminderSettings.model_validate(values)
        except (ValueError,TypeError) as error:raise HTTPException(400,str(error)) from None
        db.execute("INSERT INTO app_preferences(name,value) VALUES('notifications',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(value.model_dump_json(),))
        return RedirectResponse('/settings#notifications',status_code=303)

    @app.post('/settings/connections')
    async def connections_settings(request:Request):
        form=await request.form();value={name:{'read':form.get(name+'_read')=='on','write':form.get(name+'_write')=='on'} for name in ('codex','chatgpt_web','work_local','work_hosted')}
        db.execute("INSERT INTO app_preferences(name,value) VALUES('connections',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(encode(value),))
        return RedirectResponse('/settings#connections',status_code=303)

    @app.post('/settings/connections/credential')
    async def connection_credential(client:str=Form(...),confirm:str=Form('')):
        if confirm!='yes':raise HTTPException(400,'Neuen lokalen MCP-Zugang ausdrücklich bestätigen.')
        try:token=issue_credential(client,settings.mcp_store)
        except ValueError as error:raise HTTPException(400,str(error)) from None
        return {'client':client,'credential':token,'message':'Nur in der privaten Client-Verbindung eintragen. Dieser neue MCP-Schlüssel ist kein OpenAI-API-Schlüssel. Alter Schlüssel für diesen Client wurde ersetzt.'}

    @app.get('/settings/tunnel/status')
    async def tunnel_status():
        try:
            async with tunnel.gate:
                await tunnel.refresh()
        except ValueError as error:
            tunnel.error=str(error)
            tunnel.state.update(state='Tunnel-Prüfung fehlgeschlagen',connected=False,openai=False,mcp=False)
        return JSONResponse(tunnel.public(), headers={'Cache-Control':'no-store'})

    @app.post('/settings/tunnel')
    async def tunnel_settings(executable:str=Form(...),auto_start:bool=Form(False),runtime_key:str=Form(''),tunnel_id:str=Form('')):
        try:
            await tunnel.configure(executable,auto_start,runtime_key,tunnel_id)
        except ValueError as error:
            raise HTTPException(400,str(error)) from None
        return {'message':'Tunnel-Einstellungen gespeichert. Ein eingegebener Key liegt ausschließlich Windows-verschlüsselt vor. Zum Verbinden Start bzw. manuellen Tunnel ablösen wählen.'}

    @app.post('/settings/tunnel/{action}')
    async def tunnel_action(action:str):
        try:
            if action=='stop':await tunnel.stop()
            elif action=='restart':
                await tunnel.stop()
                await tunnel.start()
            elif action in ('start','takeover'):await tunnel.start(takeover=action=='takeover')
            else:raise HTTPException(404,'Unbekannte Tunnel-Aktion.')
        except ValueError as error:
            tunnel.error=str(error)
            raise HTTPException(409,str(error)) from None
        return tunnel.public()

    @app.post('/settings/autostart')
    async def autostart_settings(request:Request, enabled:bool=Form(False)):
        try:await asyncio.to_thread(settings.runtime.set_autostart,enabled)
        except (ValueError,OSError) as error:raise HTTPException(400,str(error)) from None
        if request.headers.get('x-requested-with')=='fetch':
            return {'enabled':settings.runtime.autostart_enabled(),'message':'Windows-Autostart aktiviert.' if enabled else 'Windows-Autostart ausgeschaltet.'}
        return RedirectResponse('/settings#background',status_code=303)

    @app.get('/api/desktop/status')
    async def desktop_status():
        return {'application':'StudyCenter','instance_id':app.state.instance_id,
                'dataset_path':hashlib.sha256(str(settings.data_dir).encode()).hexdigest()[:16],
                'active':sync.active,'stt_busy':speech_gate.locked() or speech.lock.locked(),'lifecycle_request':app.state.lifecycle_request,
                'activities':activities(),'windows_shutdown_ready':getattr(app.state,'windows_shutdown_ready',False),
                'source_sync':'manual-only','google':google.public(),'tunnel':tunnel.public()}

    @app.post('/settings/desktop/install')
    async def desktop_install():
        try:path=await asyncio.to_thread(settings.runtime.install_desktop)
        except (OSError,ValueError):raise HTTPException(500,'Startmenüeintrag konnte nicht eingerichtet werden.') from None
        return {'message':'StudyCenter im Startmenü und auf dem Desktop eingerichtet. Vorhandener Autostart übernommen.','path':path}

    @app.get('/settings/google/preview')
    async def google_preview():
        selected,skipped=await asyncio.to_thread(google.preview)
        return {'selected':selected,'skipped':skipped,'status':google.public()}

    @app.post('/settings/google/client')
    async def google_client(request:Request):
        form=await request.form()
        upload=form.get('client')
        if not hasattr(upload,'read'):raise HTTPException(400,'Desktop-Client-JSON auswählen.')
        raw=await upload.read(65537)
        if len(raw)>65536:raise HTTPException(400,'Client-JSON ist zu groß.')
        try:google.auth.import_client(raw)
        except GoogleError as error:raise HTTPException(400,str(error)) from None
        return {'message':'Desktop-Client lokal DPAPI-verschlüsselt gespeichert.'}

    @app.post('/settings/google/connect')
    async def google_connect():
        options=google.options();options['enabled']=False;options['consent']=False;google.save(options)
        async def connected():
            options=google.options();options.pop('calendar_id',None);google.save(options)
            await google.identify()  # Read-only target detection; no external writes yet.
        try:await google.auth.connect(connected)
        except GoogleError as error:raise HTTPException(400,str(error)) from None
        return {'message':'Google-Anmeldung im Systembrowser geöffnet. Danach Vorschau prüfen und Spiegelung freigeben.'}

    @app.post('/settings/google/setup')
    async def google_setup():
        # Google Cloud can fail inside sandboxed/embedded browsers. This is a fixed
        # official setup page, not a general URL or shell bridge; no API writes.
        opened=await asyncio.to_thread(webbrowser.open,'https://console.cloud.google.com/apis/library/calendar-json.googleapis.com')
        if not opened:raise HTTPException(503,'Normalen Browser öffnen und Google-Cloud-Link aus der Anleitung verwenden.')
        return {'message':'Google-Ersteinrichtung im normalen Browser geöffnet. Mit deinem Google-Konto anmelden; noch kein Kalenderexport.'}

    @app.post('/settings/google/configure')
    async def google_configure(request:Request):
        form=await request.form();options=google.options()
        enabled=form.get('enabled')=='on'
        if enabled and (form.get('consent')!='yes' or not google.public()['connected']):
            raise HTTPException(400,'Google verbinden und automatische Erstellung/Änderung/Entfernung eigener Spiegelereignisse ausdrücklich freigeben.')
        if google.gate.locked():raise HTTPException(409,'Google-Abgleich läuft; kurz warten.')
        options.update(enabled=enabled,consent=enabled,restore_paused=False,minute_marker=form.get('minute_marker')=='on',reminders=form.get('reminders')=='on')
        google.save(options);google.retry_at=0
        return {'message':'Google-Spiegelung automatisch aktiviert.' if enabled else 'Google-Spiegelung pausiert.'}

    @app.post('/settings/google/selection')
    async def google_selection(item_id:int=Form(...),include:bool=Form(...)):
        item=db.one('SELECT identity FROM items WHERE id=?',(item_id,))
        if not item:raise HTTPException(404,'Eintrag fehlt.')
        if google.gate.locked():raise HTTPException(409,'Google-Abgleich läuft; kurz warten.')
        options=google.options();options.setdefault('selection',{})[item['identity']]=include;google.save(options)
        return {'message':'Google-Auswahl gespeichert; Studienbestand unverändert.'}

    @app.post('/settings/google/sync')
    async def google_sync():
        backup_idle()
        await google.sync()
        return google.public()

    @app.post('/settings/google/resolve')
    async def google_resolve(identity:str=Form(...),role:str=Form(...),keep:bool=Form(...)):
        try:await google.resolve(identity,role,keep)
        except GoogleError as error:raise HTTPException(400,str(error)) from None
        return {'message':'Google-Version bleibt ohne weitere Spiegelung.' if keep else 'StudyCenter-Wert ausdrücklich übernommen.'}

    @app.post('/settings/google/disconnect')
    async def google_disconnect():
        if google.gate.locked():raise HTTPException(409,'Google-Abgleich läuft; kurz warten.')
        options=google.options();options.update(enabled=False,consent=False);google.save(options)
        await google.auth.close(disconnect=True)
        return {'message':'Google-Zugang lokal getrennt; Zielkalender und Ereignisse bleiben erhalten.'}

    @app.post('/settings/stop')
    async def stop_app(confirm:str=Form('')):
        if confirm!='yes':raise HTTPException(400,'Stoppen ausdrücklich bestätigen.')
        backup_idle(allow_google_stop=True)
        if not hasattr(app.state,'stop_callback'):raise HTTPException(409,'Diesen App-Start im Terminal beenden.')
        if speech_gate.locked() or speech.lock.locked():raise HTTPException(409,'Bitte die laufende Spracherkennung beenden lassen.')
        google_busy=google.gate.locked()
        begin_stop()
        return {'message':'StudyCenter wird beendet.'+(' Google-Kalenderabgleich läuft aus: Die aktuelle Anfrage wird abgeschlossen und ihre Zuordnung gespeichert; keine weiteren Ereignisse werden gestartet.' if google_busy else '')+' Keine Erinnerungen bis zum nächsten lokalen Start.'}

    @app.post('/settings/restart')
    async def restart_app():
        backup_idle()
        if not hasattr(app.state,'stop_callback'):raise HTTPException(409,'Neustart nur beim regulären CampusBoard-Start verfügbar.')
        if speech_gate.locked() or speech.lock.locked():raise HTTPException(409,'Bitte die laufende Spracherkennung beenden lassen.')
        if app.state.lifecycle_request=='restart':
            return {'message':'Neustart läuft bereits.','instance_id':app.state.instance_id}
        app.state.lifecycle_request='restart'
        try:settings.runtime.launch_restart(settings.data_dir)
        except (ValueError,OSError):
            app.state.lifecycle_request=''
            raise HTTPException(500,'Neustart konnte nicht vorbereitet werden. Die laufende App bleibt geöffnet.') from None
        asyncio.get_running_loop().call_later(1,app.state.stop_callback)
        return {'message':'CampusBoard startet kontrolliert neu. Diese Seite verbindet sich anschließend wieder.','instance_id':app.state.instance_id}

    @app.post('/settings/notifications/test')
    async def test_notification(item_id:int|None=Form(None)):
        if item_id is None:
            candidate=next((i for i in db.items(active_only=True) if not i['ignored']),None)
            if not candidate:raise HTTPException(404,'Zuerst einen eigenen Eintrag anlegen.')
            item_id=candidate['id']
        item=db.one('SELECT payload FROM items WHERE id=?',(item_id,))
        if not item:raise HTTPException(404,'Eintrag fehlt.')
        try:await asyncio.to_thread(reminders.adapter.send,'CampusBoard · Native Testmeldung',json.loads(item['payload'])['title'],f"{getattr(app.state, 'endpoint', '')}/items/{item_id}",secrets.token_hex(8),reminders.settings().sound)
        except Exception as error:raise HTTPException(503,str(error)) from None
        return {'message':'Native Testmeldung an Windows übergeben. Bitte Sichtbarkeit und Klick auf den Eintragslink prüfen; dies ist keine Bestätigung deiner OS-Einstellungen.','url':f'/items/{item_id}'}

    @app.post('/items/{item_id}/reminders')
    async def item_reminders(item_id:int,request:Request):
        item=db.one('SELECT payload FROM items WHERE id=?',(item_id,))
        if not item:raise HTTPException(404)
        form=await request.form()
        try:value=ItemPreferences.model_validate(dict(mode=form.get('mode','standard'),minutes=[int(n.strip()) for n in str(form.get('minutes','')).split(',') if n.strip()],attendance=form.get('attendance',''),personal=form.get('personal')=='on',at_closing=form.get('at_closing')=='on'),context={'registration_start':json.loads(item['payload'])['kind']=='registration'})
        except ValueError as error:raise HTTPException(400,str(error)) from None
        saved=db.one('SELECT value FROM item_preferences WHERE item_id=?',(item_id,))
        preferences=(json.loads(saved['value']) if saved else {})|value.model_dump(mode='json')
        db.execute('INSERT INTO item_preferences(item_id,value) VALUES(?,?) ON CONFLICT(item_id) DO UPDATE SET value=excluded.value',(item_id,encode(preferences)))
        return RedirectResponse(f'/items/{item_id}#reminders',status_code=303)

    @app.post('/settings/backups/create')
    async def backup_download(request: Request):
        backup_idle()
        app.state.maintenance = True
        try:
            path = await finish_thread(create_backup, db, settings.data_dir)
        except (ValueError, OSError) as error:
            raise HTTPException(400, str(error)) from None
        finally:
            app.state.maintenance = False
        if request.headers.get('x-requested-with') == 'fetch':
            return {'download': '/settings/backups/' + path.name}
        return FileResponse(path, media_type='application/zip', filename=path.name)

    @app.get('/settings/backups/{name}')
    async def saved_backup(name: str):
        if not re.fullmatch(r'CampusBoard-[\w-]+\.zip', name):
            raise HTTPException(404, 'Backup nicht gefunden')
        path = settings.data_dir / 'backups' / name
        if not path.is_file():
            raise HTTPException(404, 'Backup nicht gefunden')
        return FileResponse(path, media_type='application/zip', filename=name)

    @app.post('/settings/backups/check')
    async def backup_check(request: Request):
        backup_idle()
        app.state.maintenance = True
        staged = settings.data_dir / 'restore-preview.zip'
        try:
            with tempfile.TemporaryDirectory(dir=settings.data_dir) as temporary:
                archive = Path(temporary) / 'upload.zip'
                size = 0
                with archive.open('wb') as output:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > MAX_ARCHIVE:
                            raise ValueError('Backup überschreitet 2 GiB.')
                        output.write(chunk)
                manifest = await finish_thread(inspect_archive, archive, Path(temporary) / 'checked')
                shutil.copy2(archive, staged)
            app.state.restore_preview = {'manifest': manifest, 'token': secrets.token_urlsafe(32)}
            return {'message': 'Archiv geprüft. Bitte Inhalt und Wiederherstellung bestätigen.'}
        except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError, sqlite3.Error) as error:
            raise HTTPException(400, 'Backup ungültig: ' + str(error)) from None
        finally:
            app.state.maintenance = False

    @app.post('/settings/backups/confirm')
    async def backup_confirm(request: Request):
        form = await request.form()
        backup_idle()
        preview = app.state.restore_preview
        if not preview or form.get('confirm') != 'yes' or not secrets.compare_digest(str(form.get('preview', '')), preview['token']):
            raise HTTPException(400, 'Geprüftes Backup und ausdrückliche Bestätigung erforderlich.')
        (settings.data_dir / 'restore-preview.zip').replace(settings.data_dir / 'restore-pending.zip')
        app.state.restore_preview = None
        return RedirectResponse('/settings', status_code=303)

    @app.post('/settings/backups/cancel')
    async def backup_cancel():
        (settings.data_dir / 'restore-pending.zip').unlink(missing_ok=True)
        (settings.data_dir / 'restore-preview.zip').unlink(missing_ok=True)
        app.state.restore_preview = None
        return RedirectResponse('/settings', status_code=303)

    @app.get('/mail')
    async def mail_config(request: Request):
        try:
            account, error = settings.mail_store.status(), ''
        except MailError as exception:
            account, error = {}, str(exception)
        hint = settings.data_dir / 'mail-hint.json'
        if hint.exists() and not account.get('address'):
            account['address'] = json.loads(hint.read_text(encoding='utf-8')).get('address', '')
        courses = db.rows('SELECT * FROM courses WHERE active=1 ORDER BY title')
        semester = courses[0]['semester'] if courses else ''
        since = (semester[:4] + ('-10-01' if semester.endswith('W') else '-03-01')) if semester[:4].isdigit() else date.today().isoformat()
        return render(request, 'mail.html', account=account, error=error, since=since,
                      courses=courses,
                      mail_sources=[s for s in db.sources() if s['kind']=='mail'])

    @app.post('/api/mail/login')
    @app.post('/mail/login')
    async def mail_login():
        if sync.active:
            raise HTTPException(409, 'Laufenden Sync zuerst abschließen lassen.')
        try:
            await browser.open_login('https://webmail.tugraz.at/imp/dynamic.php?page=mailbox#mbox:SU5CT1g')
        except BrowserUnavailable as error:
            raise HTTPException(503, str(error)) from None
        return RedirectResponse('/mail', status_code=303)

    @app.post('/api/mail/session')
    async def mail_session():
        check = (await session_probe('mail'))['mail']
        return {**check, 'authenticated': check['state']=='authenticated'}

    @app.post('/api/sessions/probe')
    async def session_probe(service: str | None = None):
        if sync.active:
            return {key:{'state':'busy','reason':'Manueller Sync läuft.'} for key in ([service] if service else ('teachcenter','tugrazonline','mail'))}
        if service not in (None,'teachcenter','tugrazonline','mail'):
            raise HTTPException(400,'Unbekannter TU-Dienst.')
        return await browser.probe([service] if service else None)

    @app.get('/api/browser/status')
    async def browser_status():
        pages = list(browser.context.pages) if browser.context else []
        return {'owned_tabs': len(pages), 'visible':browser.visible, 'login_mode':browser.login_mode,
                'driver_active': bool(browser.playwright), 'error': browser.last_error,
                'hosts':[urlsplit(p.url).hostname or 'Leerer Tab' for p in pages],
                'paths':[urlsplit(p.url).path for p in pages], 'mail_blocked':browser.blocked_mail_requests}

    @app.post('/mail/webmail')
    async def configure_webmail(request: Request):
        if sync.active:
            raise HTTPException(409, 'Sync läuft; Quelle danach konfigurieren.')
        form = await request.form()
        try:
            all_mail = form.get('include_all') == 'on'
            course = None if all_mail else db.one('SELECT * FROM courses WHERE id=? AND active=1',(int(form['course_id']),))
            if not all_mail and not course:
                raise ValueError('Aktiver Kurs fehlt')
            config = {'transport':'webmail','folder':str(form.get('folder','INBOX')).strip(),
                      'since':date.fromisoformat(str(form['since'])).isoformat(),
                      'keywords':[v.strip() for v in str(form.get('keywords','')).splitlines() if v.strip()],
                      'senders':[v.strip() for v in str(form.get('senders','')).splitlines() if v.strip()],
                      'max_pages':20,'max_analysis_documents':3}
            config['allow_model_analysis'] = form.get('allow_model_analysis') == 'on'
            config['include_all'] = all_mail
            config['discover_folders'] = all_mail and form.get('all_folders') == 'on'
            config['folders'] = [v.strip() for v in str(form.get('folders','')).splitlines() if v.strip()] or [config['folder']]
            if not config['folder'] or not (all_mail or config['keywords'] or config['senders']):
                raise ValueError('Ordner und konkreten Kursfilter angeben')
            if any('\r' in value or '\n' in value for value in config['senders']):
                raise ValueError('Absender ungültig')
            from urllib.parse import quote
            url = 'https://webmail.tugraz.at/imp/?folder=' + quote(config['folder'],safe='') + ('&scope=all' if all_mail else f"&course={course['id']}")
            existing = db.one("SELECT * FROM sources WHERE kind='mail' AND url=?",(url,))
            if existing:
                previous = json.loads(existing['config'])
                restart = any(previous.get(k)!=config[k] for k in ('folder','folders','since','keywords','senders','include_all','discover_folders'))
                previous.update({k:v for k,v in config.items() if k not in ('max_pages','max_analysis_documents')})
                if all_mail and config['allow_model_analysis']:
                    previous.pop('model_document_ids',None)
                db.execute("UPDATE sources SET active=1,config=?,crawl_state=CASE WHEN ? THEN '{}' ELSE crawl_state END WHERE id=?",(encode(previous),restart,existing['id']))
            else:
                db.execute("INSERT INTO sources(course_id,kind,title,url,config) VALUES(?,'mail',?,?,?)",(course['id'] if course else None,'TU-Webmail · '+(course['title'] if course else 'Alle Mails')+' · '+config['folder'],url,encode(config)))
        except (ValueError, KeyError) as error:
            raise HTTPException(400,str(error)) from None
        return RedirectResponse('/mail',status_code=303)

    @app.post('/api/mail/collect')
    async def collect_mail_locally(source_id: int):
        from .mail import collect_webmail
        if sync.active:
            raise HTTPException(409,'Sync läuft; lokalen Mailabruf danach starten.')
        source = db.source(source_id)
        if not source or source['kind']!='mail' or json.loads(source['config']).get('transport')!='webmail':
            raise HTTPException(400,'Webmail-Kursquelle wählen.')
        try:
            check = (await browser.probe(['mail']))['mail']
            if check['state']!='authenticated':
                raise HTTPException(401 if check['state']=='login_required' else 503,check['reason'])
            async with browser.crawl_context() as context:
                result = await collect_webmail(source,context,db,sync.store,browser)
            return result
        except (LoginRequired, MailError, BrowserUnavailable) as error:
            db.execute('UPDATE sources SET state=?,message=? WHERE id=?',('login_required' if isinstance(error,LoginRequired) else 'failed',str(error),source_id))
            raise HTTPException(401 if isinstance(error,LoginRequired) else 502,str(error)) from None
        finally:
            await browser.close_idle()

    @app.post('/api/browser/close')
    async def browser_close():
        if sync.active:
            raise HTTPException(409, 'Aktiver Sync; TU-Browser danach schließen.')
        await browser.close()
        browser.login_mode = False
        return {'state':'closed', 'message':'Nur CampusBoard-TU-Browser geschlossen; Profil und Sitzungsdaten erhalten.'}

    @app.post('/mail')
    async def mail_configure(request: Request):
        form = await request.form()
        if sync.active:
            raise HTTPException(409, 'Laufenden Sync erst abschließen lassen.')
        credentials = settings.mail_store
        try:
            old = credentials.read()
            host = str(form.get('host', '')).strip().lower()
            username = str(form.get('username', '')).strip()
            password = str(form.get('password', ''))
            if not password and (host, username) == (old.get('host'), old.get('username')):
                password = old.get('password', '')
            values = {'host': host, 'username': username, 'password': password,
                      'address': str(form.get('address', '')).strip()}
            if any('\r' in v or '\n' in v for v in values.values()):
                raise ValueError('Ungültige Zugangsdaten')
            course = db.one('SELECT * FROM courses WHERE id=? AND active=1', (int(form['course_id']),))
            if not course:
                raise ValueError('Aktiver Kurs fehlt')
            since = date.fromisoformat(str(form['since'])).isoformat()
            keywords = [v.strip() for v in str(form.get('keywords', '')).splitlines() if v.strip()]
            senders = [v.strip() for v in str(form.get('senders', '')).splitlines() if v.strip()]
            if not keywords and not senders:
                raise ValueError('Kursfilter fehlt')
            folder = str(form.get('folder', 'INBOX')).strip()
            await asyncio.to_thread(probe_mail, values, folder)
            credentials.write(values)
            from urllib.parse import quote
            url = f"imap://{host}/{quote(folder, safe='')}?course={course['id']}"
            config = {'folder': folder, 'keywords': keywords, 'senders': senders, 'since': since,
                      'allow_model_analysis':form.get('allow_model_analysis')=='on'}
            existing = db.one("SELECT * FROM sources WHERE kind='mail' AND url=?", (url,))
            if existing:
                previous = json.loads(existing['config'])
                restart = any(previous.get(k) != config[k] for k in ('keywords', 'senders', 'since'))
                previous.update(config)
                db.execute("UPDATE sources SET active=1,config=?,crawl_state=CASE WHEN ? THEN '{}' ELSE crawl_state END WHERE id=?",
                           (encode(previous),restart,existing['id']))
            else:
                db.execute("INSERT INTO sources(course_id,kind,title,url,config) VALUES(?,'mail',?,?,?)",
                           (course['id'], 'TU-Mail · ' + course['title'] + ' · ' + folder, url, encode(config)))
        except (MailError, LoginRequired) as error:
            raise HTTPException(400, str(error)) from None
        except (ValueError, KeyError):
            raise HTTPException(400, 'TU-Mailkonfiguration ungültig; Kurs, Datum, Filter und Zugang prüfen.') from None
        return RedirectResponse('/mail', status_code=303)

    @app.get('/overview')
    async def overview(request: Request, kind: str = ''):
        if kind not in ('', 'exam', 'registration', 'task'):
            kind = ''
        local_now = datetime.now(VIENNA)
        today = local_now.date().isoformat()
        horizon = (local_now.date() + timedelta(days=14)).isoformat()
        rows = [i | view_action_state(i, local_now) for i in (await asyncio.to_thread(db.items,active_only=True)) if not i['ignored']]
        filtered = [i for i in rows if not kind or i['data']['kind']==kind]
        due_key = lambda i: (i['action_date'] or '9999', i['action_time'] or '', i['id'])
        tasks = [i for i in rows if i['actionable'] and i['data']['kind']=='task']
        expired_registrations = sorted([i for i in filtered if i['actionable'] and i['action_deadline']
                                       and i['data']['kind']=='registration' and i['remaining'] is not None and i['remaining']<0],
                                      key=due_key,reverse=True)
        expired_ids = {i['id'] for i in expired_registrations}
        due = sorted([i for i in filtered if i['actionable'] and i['action_deadline'] and i['action_date']
                      and i['id'] not in expired_ids],key=due_key)
        checks = sorted([i for i in filtered if i['status']!='done' and i['data']['source_state']!='cancelled' and not i['offered']
                         and (i['data']['obligation']=='required' or i['personal'] or i['pinned'])
                         and (i['conflict'] or i['data']['source_state']=='unclear')
                         and (not i['action_date'] or i['action_date']<=horizon)],key=due_key)
        priority = sorted([i for i in filtered if i['actionable'] and i['action_date'] and i['id'] not in expired_ids
                           and (i['action_deadline'] and i['action_date']<=horizon
                                or (i['appointment'] or i['planned_task']) and today<=i['action_date']<=horizon)],
                          key=lambda i:(not i['urgent'], *due_key(i)))
        # Ordinary personal lectures belong in the agenda, leaving action space for
        # closing deadlines, own planning and explicitly required appointments.
        focus_priority = [i for i in priority if i['action_deadline'] or i['planned_task']
                          or i['appointment'] and i['data']['obligation']=='required']
        check_ids = {i['id'] for i in checks}
        def acute_focus(item):
            return (item['urgent'] and item['data']['source_state']=='active'
                    and not item.get('date_conflict',item['conflict']))
        # A source conflict is not a reliable deadline. Keep dated conflicts visible
        # in their own section; undated source information must not displace tasks.
        focus_actions = [i for i in focus_priority if i['id'] not in check_ids][:6]
        display_priority = [i for i in focus_priority if i['id'] not in check_ids
                            and (not i['appointment'] or i['urgent'])]
        focus_display = display_priority[:6]
        dated_checks = [i for i in checks if i['action_date']]
        undated_checks = [i for i in checks if not i['action_date']]
        actions = focus_actions
        action_ids = {i['id'] for i in actions}
        week = []
        week_end = (local_now.date()+timedelta(days=7)).isoformat()
        for i in filtered:
            if not i['relevant'] or i['offered'] or i['status']=='done': continue
            # Keep conflicting dated claims visible in the agenda for comparison,
            # without treating them as reliable deadlines or urgent actions.
            if not (i['appointment'] or i['action_deadline'] or i['planned_task']
                    or i['closing'] and i.get('date_conflict')): continue
            if i['action_date'] and today<=i['action_date']<=week_end:
                week.append(i)
        week.sort(key=due_key)
        # The compact Today view must not be limited by the weekly preview's
        # twelve-entry cap, and deadlines are not calendar appointments.
        today_appointments = [i for i in week if i['action_date']==today and i['appointment']]
        upcoming_deadline = next((i for i in due if i['action_date']>=today and i['id'] not in check_ids), None)
        undated_tasks = sorted([i for i in filtered if i['data']['kind']=='task' and i['actionable']
                                and not i['action_date'] and i['id'] not in check_ids], key=lambda i:i['id'])
        # Seven calendar days including today; overdue deadlines are never hidden
        # among upcoming entries, and uncertain claims remain in the check section.
        upcoming_end = (local_now.date() + timedelta(days=6)).isoformat()
        reliable = [i for i in filtered if i['relevant'] and i['status'] != 'done'
                    and not i['offered'] and i['id'] not in check_ids]
        overdue_entries = sorted([i for i in reliable if i['actionable'] and i['action_deadline']
                                  and i['remaining'] is not None and i['remaining'] < 0], key=due_key)
        overdue_ids = {i['id'] for i in overdue_entries}
        upcoming_entries = sorted([i for i in reliable if i['action_date']
                                   and today <= i['action_date'] <= upcoming_end
                                   and i['id'] not in overdue_ids
                                   and (i['appointment'] or i['action_deadline'] or i['planned_task'])], key=due_key)
        quest_dashboard = await asyncio.to_thread(quests.dashboard)
        quest_candidates = []
        for quest in quest_dashboard['quests']:
            if quest['state'] != 'active': continue
            open_steps = [s for s in quest['steps'] if not s['done']]
            if not open_steps: continue
            next_step = (open_steps[0] if quest['planning']=='checklist' else
                         min(open_steps, key=lambda s:s['scheduled_date']))
            scheduled = next_step['scheduled_date']
            rank = 0 if scheduled and scheduled <= today else 1 if quest['planning']=='checklist' else 2
            quest_candidates.append((rank, scheduled or '', {'quest':quest, 'step':next_step}))
        next_quest = min(quest_candidates, key=lambda candidate:candidate[:2])[2] if quest_candidates else None
        # Bounded lists, no persistent merging of source records or personal states.
        run = db.one("SELECT * FROM sync_runs WHERE state!='running' AND kind='manual' ORDER BY id DESC LIMIT 1")
        since = run['started_at'] if run else None
        updates = [i for i in filtered if not i['seen'] and (not since or i['changed_at']>=since)
                   and (i['relevant'] or i['data']['source_state']=='cancelled')]
        updates.sort(key=lambda i:i['changed_at'],reverse=True)
        for item in updates[:5]:
            history = [h for h in item['history'] if not since or h['at']>=since]
            if history:
                item['overview_change'] = {'before':history[0]['before'], 'after':history[-1]['after']}
        sources = [s for s in db.sources() if s['active'] and s['course_active'] != 0]
        attention = [s for s in sources if s['analysis_pending'] > s.get('deferred_analysis', 0) or s['state'] != 'success']
        gaps = db.one("""SELECT count(DISTINCT d.id) n FROM documents d JOIN sources s
            ON d.source_id=s.id OR d.id IN (SELECT value FROM json_each(s.crawl_state,'$.document_ids'))
            WHERE s.id IN (SELECT value FROM json_each(?)) AND d.analysis_state!='archived'
            AND (d.analysis_state='partial' OR d.processing_state='partial')
            AND (d.mime='message/rfc822' OR d.id=(SELECT d2.id FROM documents d2
                WHERE d2.source_id=d.source_id AND d2.url=d.url ORDER BY fetched_at DESC,id DESC LIMIT 1))""",
            (encode([s['id'] for s in sources]),))['n']
        return render(request, 'overview.html', kind=kind, today=today, tasks=tasks,
                      upcoming_end=upcoming_end, upcoming_entries=upcoming_entries, overdue_entries=overdue_entries,
                      today_appointments=today_appointments, upcoming_deadline=upcoming_deadline,
                      undated_tasks=undated_tasks, next_quest=next_quest,
                      expired_registrations=expired_registrations[:8], expired_registration_count=len(expired_registrations),
                      actions=actions, action_ids=action_ids, action_count=len({i['id'] for i in priority+checks}),
                      focus_actions=focus_actions, focus_display=focus_display, focus_display_count=len(display_priority),
                      focus_action_count=len([i for i in focus_priority if i['id'] not in check_ids]),
                      dated_checks=dated_checks[:6], dated_check_count=len(dated_checks), undated_checks=undated_checks[:6], undated_check_count=len(undated_checks),
                      focus_urgent=sum(acute_focus(i) for i in focus_priority if i['id'] not in check_ids),
                      due=due[:6], due_count=len(due), week=week[:12], week_count=len(week),
                      urgent=sum(i['urgent'] for i in priority), checks=checks, updates=updates[:5], update_count=len(updates), since=since,
                      changed=sum(not i['seen'] for i in rows), attention=attention, gaps=gaps,
                      courses=db.rows('SELECT * FROM courses WHERE active=1 ORDER BY title'))

    @app.get("/")
    async def timeline(request: Request, course: str = "", kind: str = "", days: int = 14, include_inactive: bool = False, past: int = 0):
        days = max(1, min(366, days))
        past = max(0, min(30, past))
        today = datetime.now(VIENNA).date()
        start = today - timedelta(days=past)
        end = today + timedelta(days=days)
        local_now = datetime.now(VIENNA)
        rows = [i | view_action_state(i,local_now) for i in (await asyncio.to_thread(db.items,active_only=not include_inactive and not course)) if not i["ignored"] and (not course or str(i["course_id"]) == course) and (not kind or i["data"]["kind"] == kind)]
        upcoming = sorted([i for i in rows if i['action_date']
                           and start.isoformat() <= i['action_date'] <= end.isoformat()
                           and (i['status'] != 'done' or i['action_date'] < today.isoformat())
                           and (i['appointment'] or i['planned_task'] or i['action_deadline'] and i['relevant'])
                           and not (i['action_deadline'] and i['remaining'] is not None and i['remaining'] < 0
                                    and i['action_date'] >= today.isoformat())],
                          key=lambda i: (i['action_date'], i['action_time'] or '', i['data']['title']))
        overdue_count = sum(i['actionable'] and i['action_deadline'] and i['remaining'] is not None and i['remaining']<0 for i in rows)
        by_day = {}
        for item in upcoming:
            by_day.setdefault(item['action_date'], []).append(item)
        time_days = [{'date': (start + timedelta(days=n)).isoformat(),
                      'label': (start + timedelta(days=n)).strftime('%d.%m.'),
                      'weekday': ['Mo','Di','Mi','Do','Fr','Sa','So'][(start + timedelta(days=n)).weekday()],
                      'items': by_day.get((start + timedelta(days=n)).isoformat(), [])}
                     for n in range(past + days + 1)]
        return render(request, "timeline.html", time_days=time_days, today=today.isoformat(), upcoming=upcoming, overdue_count=overdue_count, courses=db.rows("SELECT * FROM courses ORDER BY active DESC,title"), course=course, kind=kind, days=days, past=past, include_inactive=include_inactive)

    @app.get("/board")
    async def board(request: Request, show_ignored: bool = False, include_inactive: bool = False,
                    course: str = '', q: str = '', status: str = '', due: str = 'all', personal: bool = False, page: int = 1):
        if status == 'done':
            return RedirectResponse('/archive?' + str(request.query_params), status_code=303)
        due = due if due in ('all','1','7','30') else 'all'
        local_now = datetime.now(VIENNA)
        rows = [i | view_action_state(i,local_now) | view_pin_state(i, local_now) for i in (await asyncio.to_thread(db.items,active_only=not include_inactive)) if show_ignored or not i['ignored']]
        query = q.strip().casefold()
        rows = [i for i in rows if (not course or str(i['course_id']) == course)
                and (not status or i['status'] == status)
                and (not personal or i['identity'].startswith('note:'))
                and (not query or query in ' '.join([i['data']['title'], i['data'].get('description') or '', i['course_title'] or '']).casefold())]
        archived_count = sum(i['status']=='done' for i in rows)
        rows = [i for i in rows if i['status'] != 'done']
        if due != 'all':
            horizon = (local_now.date()+timedelta(days=int(due))).isoformat()
            rows = [i for i in rows if i['expired'] or i['action_date'] and local_now.date().isoformat() <= i['action_date'] <= horizon]
        rows.sort(key=lambda i: (not i['pinned'], not i['actionable'], not i['urgent'], i['action_date'] or '9999', i['action_time'] or '', i['id']))
        later=[i for i in rows if i.get('available_later')]
        rows=[i for i in rows if not i.get('available_later')]
        total = len(rows)
        pages = max(1, (total + 23) // 24)
        page = max(1, min(page, pages))
        return render(request, "board.html", items=rows[(page-1)*24:page*24], later=later, total=total, page=page, pages=pages, show_ignored=show_ignored, include_inactive=include_inactive,
                      courses=db.rows('SELECT * FROM courses ORDER BY active DESC,title'), course=course, q=q, status=status,
                      due=due, personal=personal, archived_count=archived_count)

    @app.get("/pins")
    async def pins(request: Request, show_ignored: bool = False, include_inactive: bool = False,
                   due: str = 'all', personal: bool = False, course: str = '', q: str = '', status: str = '', page: int = 1):
        if status == 'done':
            return RedirectResponse('/archive?' + str(request.query_params), status_code=303)
        due = due if due in ('all','1','7','30') else 'all'
        local_now = datetime.now(VIENNA)
        available = [i | view_action_state(i,local_now) | view_pin_state(i, local_now) for i in (await asyncio.to_thread(db.items,active_only=not include_inactive)) if not i['ignored'] or show_ignored]
        def important_task(item):
            value = item['data']
            return (value['kind']=='task' and value.get('source_state')=='active'
                    and not item.get('calendar_duplicate_of')
                    and not (value.get('group') and item.get('course_group') and value['group']!=item['course_group']))
        query = q.strip().casefold()
        available = [i for i in available if (not course or str(i['course_id']) == course)
                     and (not personal or i['identity'].startswith('note:'))
                     and (not query or query in ' '.join([i['data']['title'], i['data'].get('description') or '', i['course_title'] or '']).casefold())]
        archived_count = sum(i['status']=='done' and (i['pinned'] or important_task(i)) for i in available)
        available = [i for i in available if i['status'] != 'done' and (not status or i['status'] == status)]
        rows = [i for i in available if i['pinned'] or i['auto_pin'] or important_task(i) or (show_ignored and i['ignored'])]
        if due != 'all':
            horizon = (local_now.date()+timedelta(days=int(due))).isoformat()
            rows = [i for i in rows if i['expired'] or i['action_date'] and local_now.date().isoformat() <= i['action_date'] <= horizon]
        rows.sort(key=lambda i: (not i['pinned'], not i['expired'], not i['urgent'], not i['auto_pin'], i['action_date'] or '9999', i['id']))
        later=[i for i in rows if i.get('available_later')]
        rows=[i for i in rows if not i.get('available_later')]
        total = len(rows)
        pages = max(1, (total + 23) // 24)
        page = max(1, min(page, pages))
        return render(request, "pins.html", items=rows[(page-1)*24:page*24], later=later, total=total, page=page, pages=pages, show_ignored=show_ignored, include_inactive=include_inactive,
                      courses=db.rows("SELECT * FROM courses ORDER BY active DESC,title"), course=course, q=q, status=status, due=due, personal=personal, archived_count=archived_count)

    @app.get("/calendar")
    async def calendar(request: Request):
        return render(request, "calendar.html", courses=db.rows("SELECT * FROM courses"))

    @app.get("/api/calendar")
    async def event_feed(course: str = "", kind: str = "", include_inactive: bool = False):
        items = [i for i in (await asyncio.to_thread(db.items,active_only=not include_inactive and not course)) if (not course or str(i["course_id"]) == course) and (not kind or i["data"]["kind"] == kind)]
        return calendar_events(items,present=True)

    @app.get('/calendar/export.ics')
    async def calendar_export(course: str = '', start: str = '', end: str = ''):
        try:
            start = date.fromisoformat(start) if start else None
            end = date.fromisoformat(end) if end else None
        except ValueError:
            raise HTTPException(400, 'Ungültiger Exportzeitraum.') from None
        if start and end and end < start:
            raise HTTPException(400, 'Zeitraumende liegt vor dem Beginn.')
        if course and not course.isdigit():
            raise HTTPException(400, 'Ungültiger Kursfilter.')
        items = [i for i in db.items(active_only=True) if not course or str(i['course_id']) == course]
        local_now = datetime.now(VIENNA)
        raw, count = export_calendar(items, calendar_events(items), {i['id']: action_state(i, local_now) for i in items}, start, end, base_url=getattr(app.state,'endpoint',''))
        return Response(raw, media_type='text/calendar; charset=utf-8', headers={
            'Content-Disposition': 'attachment; filename="CampusBoard.ics"',
            'Cache-Control': 'no-store', 'X-CampusBoard-Events': str(count)})

    @app.get("/items/{item_id}")
    async def item_detail(request: Request, item_id: int):
        items = await asyncio.to_thread(db.items)
        item = next((i for i in items if i["id"] == item_id), None)
        if not item:
            raise HTTPException(404, "Eintrag nicht gefunden")
        if item.get('calendar_duplicate_of'):
            return RedirectResponse('/items/'+str(item['calendar_duplicate_of'])+('?' + request.url.query if request.url.query else ''),status_code=303)
        evidence = db.rows("SELECT e.*,d.title document_title,d.url,d.mime,d.published_at,d.revision,d.repository_path,d.source_url,d.mail_metadata FROM item_evidence e JOIN documents d ON d.id=e.document_id WHERE item_id=? ORDER BY e.id DESC", (item_id,))
        for value in evidence:
            value["data"] = json.loads(value["payload"])
            value['mail'] = json.loads(value['mail_metadata'])
        related = []
        key = item["data"].get("related_key")
        if key:
            related = [i for i in items if i["id"] != item_id and i["course_id"] == item["course_id"] and (i["data"].get("identity_key") == key or i["data"].get("related_key") == key)]
        import re
        own_links = re.findall(r'https?://[^\s<>]+', item['data'].get('description','')) if not evidence else []
        plans,issues=reminders.preview(item)
        return render(request, "item.html", item=item, evidence=evidence, related=related, own_links=own_links,
                      timing_label=action_state(item,datetime.now(VIENNA))['action_label'],reminder_plans=plans,reminder_issues=issues)

    # Drafts are process-local and bounded. Only POST creates/prunes them; GET
    # remains read-only and the opaque key keeps personal values out of URLs.
    comparison_drafts = {}
    def comparison_redirect(item_id, fact, *, pending=None, error=None, token=None):
        now = monotonic_clock.monotonic()
        expired = [key for key, draft in comparison_drafts.items() if draft['expires'] <= now]
        for key in expired: del comparison_drafts[key]
        while len(comparison_drafts) >= 100: del comparison_drafts[next(iter(comparison_drafts))]
        key = secrets.token_urlsafe(24)
        comparison_drafts[key] = {'item_id':item_id, 'fact':fact, 'token':token,
                                  'pending':pending, 'error':error[:1000] if error else None,
                                  'expires':now + 600}
        return RedirectResponse(f'/items/{item_id}/compare?fact={fact}&draft={key}',status_code=303)

    def comparison_render(request, view, pending=None, error=None):
        from .comparison import project_value
        preview = project_value(view['item']['data'],view['fact'],pending['value']) if pending else None
        if preview:
            copy=view['item'] | {'data':preview}
            plans,issues=reminders.preview(copy)
        else: plans,issues=[],[]
        return render(request,'comparison.html',comparison=view,pending=pending,preview=preview,
                      reminder_plans=plans,reminder_issues=issues,error=error)

    @app.get('/items/{item_id}/compare')
    async def compare_item(item_id: int, request: Request, fact: str = '', draft: str = ''):
        from .comparison import comparison
        try: view=await asyncio.to_thread(comparison,db,item_id,fact or None)
        except LookupError: raise HTTPException(404,'Eintrag nicht gefunden') from None
        except ValueError as error: raise HTTPException(400,str(error)) from None
        pending = error = None
        if draft:
            entry = comparison_drafts.get(draft) if len(draft) <= 64 else None
            if not entry or entry['expires'] <= monotonic_clock.monotonic() or entry['item_id'] != item_id or entry['fact'] != view['fact']:
                error = 'Die Vorschau ist nicht mehr verfügbar. Bitte neu vergleichen.'
            elif entry['pending'] and entry['token'] != view['token']:
                error = 'Die Beleglage oder deine Korrektur hat sich geändert. Bitte neu vergleichen.'
            else:
                pending, error = entry['pending'], entry['error']
        return comparison_render(request,view,pending,error)

    @app.post('/items/{item_id}/compare')
    async def compare_choice(item_id: int, request: Request):
        from .comparison import comparison, save, project_value
        form=await request.form(); fact=str(form.get('fact','')); action=str(form.get('action','preview'))
        try:
            view=await asyncio.to_thread(comparison,db,item_id,fact)
            if str(form.get('token',''))!=view['token']:
                raise ValueError('Die Beleglage oder deine Korrektur hat sich geändert. Bitte neu vergleichen.')
            choice=str(form.get('choice','')); manual=None
            if form.get('mode')=='manual':
                choice=''  # Manual drafts carry no source key or proof.
                manual={'obligation':str(form.get('obligation',''))} if fact=='obligation' else {'date':str(form.get('date','')),'time':str(form.get('time','')) or None}
            selected=next((c for c in view['cards'] if c['key']==choice),None)
            reason=str(form.get('reason','')).strip()
            if len(reason)>500 or (manual and any(len(str(v or ''))>100 for v in manual.values())):
                raise ValueError('Die Eingabe ist zu lang.')
            if action in ('apply','reset'):
                await asyncio.to_thread(save,db,item_id,fact,view['token'],choice,manual,reason,action=='reset')
                return RedirectResponse(f'/items/{item_id}/compare?fact={fact}',status_code=303)
            if action!='preview' or (manual is None and selected is None): raise ValueError('Bitte eine Angabe auswählen.')
            from .models import Candidate
            value=manual if manual is not None else selected['value']
            Candidate.model_validate(project_value(view['item']['data'],fact,value))
            pending={'value':value,'choice':choice,'mode':'manual' if manual is not None else 'source','reason':reason}
            return comparison_redirect(item_id,view['fact'],pending=pending,token=view['token'])
        except LookupError: raise HTTPException(404,'Eintrag nicht gefunden') from None
        except (ValueError,KeyError) as error:
            try: view=await asyncio.to_thread(comparison,db,item_id,fact if fact else None)
            except ValueError: view=await asyncio.to_thread(comparison,db,item_id,None)
            return comparison_redirect(item_id,view['fact'],error='Auswahl nicht übernommen: '+str(error))

    @app.post("/items/{item_id}")
    async def update_item(item_id: int, request: Request):
        from .item_state import REVISIONS, StaleItem, revision, set_correction, set_personal_field
        form = await request.form()
        action = form.get("action", "status")
        try:
            if len(form.getlist('expected_revision')) > 1:
                raise ValueError('Ungültiger Bearbeitungsstand.')
            expected = revision(form.get('expected_revision'))
            with db.connection() as con:
                con.execute('BEGIN IMMEDIATE')
                row = con.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
                if row is None:
                    raise LookupError('Eintrag nicht gefunden.')
                current_revision = None
                if action == 'status':
                    field = 'status'
                    row = set_personal_field(con, item_id, field, form.get('status'), expected_revision=expected)
                    current_revision = row[REVISIONS[field]]
                elif action in ('pin', 'ignore'):
                    field = 'pinned' if action == 'pin' else 'ignored'
                    target = form.get('target')
                    if target is None and expected is None:  # Old clients used a toggle.
                        target = 1 - row[field]
                    elif target not in ('0', '1'):
                        raise ValueError('Ungültiges Ziel für die Einstellung.')
                    row = set_personal_field(con, item_id, field, int(target), expected_revision=expected)
                    current_revision = row[REVISIONS[field]]
                elif action == 'seen':
                    con.execute('UPDATE items SET seen=1 WHERE id=?', (item_id,))
                elif action in ('correct', 'reset'):
                    overrides = ({'title': str(form.get('title', '')), 'date': str(form.get('date', '')) or None,
                                  'time': str(form.get('time', '')) or None, 'end_date': str(form.get('end_date', '')) or None,
                                  'end_time': str(form.get('end_time', '')) or None, 'description': str(form.get('description', ''))}
                                 if action == 'correct' else {})
                    row = set_correction(con, item_id, overrides, expected_revision=expected)
                    current_revision = row['content_revision']
                else:
                    raise ValueError('Ungültige Aktion.')
        except StaleItem as error:
            raise HTTPException(409, str(error)) from None
        except LookupError as error:
            raise HTTPException(404, str(error)) from None
        except ValueError as error:
            raise HTTPException(400, 'Korrektur ungültig: Datum, Uhrzeit und Zeitraum prüfen' if action == 'correct' else str(error)) from None
        if request.headers.get("x-requested-with") == "fetch":
            return {"ok": True, "revision": current_revision}
        return RedirectResponse(f"/items/{item_id}", status_code=303)

    @app.get("/sources")
    async def sources(request: Request, view: str = 'all'):
        from .personal import coverage
        view=view if view in ('all','clarify','refresh') else 'all'
        all_coverage=await asyncio.to_thread(coverage,db)
        # A course recommendation must point to an actual current source, not a
        # guessed relationship between similarly named files.
        evidence = await asyncio.to_thread(db.rows, """SELECT e.item_id,e.page,d.id,d.title,d.mime
            FROM item_evidence e JOIN documents d ON d.id=e.document_id JOIN sources s ON s.id=d.source_id
            WHERE e.current=1 AND s.active=1 AND d.analysis_state!='archived'
              AND (d.mime='message/rfc822' OR d.id=(SELECT d2.id FROM documents d2
                   WHERE d2.source_id=d.source_id AND d2.url=d.url ORDER BY d2.fetched_at DESC,d2.id DESC LIMIT 1))
            ORDER BY e.id DESC""")
        proofs = {}
        for row in evidence:
            proofs.setdefault(row['item_id'], []).append(row)
        course_items = await asyncio.to_thread(db.items, active_only=True)
        from .quests import eligible
        local_now=datetime.now(VIENNA)
        today=local_now.date().isoformat()
        for card in all_coverage:
            candidates = [i for i in course_items if i['course_id']==card['course_id']
                          and i['data']['kind']=='task' and not i['identity'].startswith('note:')
                          and i['id'] in proofs and not i['ignored'] and i['status']!='done'
                          and i['data']['source_state']=='active' and not i.get('date_conflict',i.get('conflict'))
                          and not (i['data'].get('group') and i.get('course_group') and i['data']['group']!=i['course_group'])]
            def task_deadline(item):
                display=item.get('task_display') or {}
                day,clock,closing=item_timing(item['data'])
                day=display.get('deadline_date') or (day if closing else None)
                clock=display.get('deadline_time') if display.get('deadline_date') else clock
                return day,clock
            def past_deadline(item):
                day,clock=task_deadline(item)
                return bool(day and (day<today or (day==today and clock and clock[:5]<local_now.strftime('%H:%M'))))
            overdue=[i for i in candidates if past_deadline(i)]
            upcoming=[i for i in candidates if not past_deadline(i)]
            card['next_item']=min(upcoming,key=lambda i:(not bool(task_deadline(i)[0]),task_deadline(i)[0] or '',task_deadline(i)[1] or '',i['id']),default=None)
            card['overdue_items']=sorted(overdue,key=lambda i:(task_deadline(i)[0],i['id']),reverse=True)
            card['next_quest_eligible']=bool(card['next_item'] and eligible(card['next_item'],date.fromisoformat(today)))
            linked=proofs.get(card['next_item']['id'],[]) if card['next_item'] else []
            card['next_material']=next((p for p in linked if p['mime']=='application/pdf'),None) or next(iter(linked),None)
            day,clock=task_deadline(card['next_item']) if card['next_item'] else (None,None)
            attention=bool(card['refresh_needed'] or card['pending'] or card['reading_gaps'])
            card['_course_priority']=(0,day,clock or '',card['title'].casefold()) if day else (1 if attention else 2,'','',card['title'].casefold())
        cards=[c for c in all_coverage if view=='all' or (c['needs_clarification'] if view=='clarify' else c['refresh_needed'])]
        cards.sort(key=lambda c:c['_course_priority'])
        featured=cards[:1]
        additional=cards[1:]
        return render(request, "sources.html", coverage=cards, all_coverage=all_coverage, coverage_view=view,
                      featured_coverage=featured, additional_coverage=additional,
                      sources=await asyncio.to_thread(db.sources), courses=db.rows("SELECT * FROM courses ORDER BY id"),
                      runs=db.rows("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 8"), discovery=db.one("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1"))

    @app.post("/courses")
    async def course_add(request: Request):
        form = await request.form()
        title = str(form.get("title", "")).strip()
        if not title:
            raise HTTPException(400, "Kurstitel fehlt")
        db.execute("INSERT INTO courses(title,semester,group_name) VALUES(?,?,?)", (title, str(form.get("semester", "")), str(form.get("group", ""))))
        return RedirectResponse("/sources", status_code=303)

    @app.post("/courses/{course_id}")
    async def course_edit(course_id: int, request: Request):
        form = await request.form()
        if not str(form.get("title", "")).strip():
            raise HTTPException(400, "Kurstitel fehlt")
        old = db.one('SELECT * FROM courses WHERE id=?', (course_id,))
        if not old:
            raise HTTPException(404)
        semester, group = str(form.get('semester','')), str(form.get('group',''))
        db.execute("UPDATE courses SET title=?,semester=?,group_name=?,active=? WHERE id=?", (str(form["title"]), semester, group, int(form.get("active") == "on"), course_id))
        if semester != old['semester'] or group != old['group_name']:
            # Re-key existing entries together with their personal status; same course, corrected context.
            import hashlib
            for item in db.items():
                if item['course_id'] != course_id:
                    continue
                payload = item['source_payload']
                explicit_semester = payload.get('semester') not in (None, '', old['semester'])
                explicit_group = payload.get('group') not in (None, '', old['group_name'])
                if not explicit_semester:
                    payload['semester'] = semester
                if not explicit_group:
                    payload['group'] = group
                evidence = db.one('SELECT d.url FROM item_evidence e JOIN documents d ON d.id=e.document_id WHERE e.item_id=? ORDER BY e.current DESC,e.id DESC LIMIT 1', (item['id'],))
                if evidence:
                    context = [course_id,payload['semester'],payload['group'],payload['kind']]
                    context += [payload['identity_key'].casefold().strip()] if payload['identity_scope']=='course' else [evidence['url'],payload['identity_key'] or payload['anchor']]
                    identity = hashlib.sha256(encode(context).encode()).hexdigest()
                    db.execute('UPDATE items SET identity=?,payload=? WHERE id=?', (identity,encode(payload),item['id']))
            db.execute("UPDATE documents SET analysis_state='pending',progress='{}' WHERE source_id IN (SELECT id FROM sources WHERE course_id=?)", (course_id,))
        return RedirectResponse("/sources", status_code=303)

    @app.post("/sources")
    async def source_add(request: Request):
        form = await request.form()
        try:
            kind = str(form.get("kind", "public"))
            original_url = str(form.get('url',''))
            url = safe_url(original_url)
            if kind == 'tugrazonline' and urlsplit(original_url).fragment:
                url += '#' + urlsplit(original_url).fragment
            if kind not in ("public", "teachcenter", "tugrazonline", "gitlab"):
                raise ValueError("Quelle unbekannt")
            if kind == 'tugrazonline' and (urlsplit(url).netloc != 'online.tugraz.at' or urlsplit(url).scheme != 'https'):
                raise ValueError('TUGRAZonline-URL muss zum TU-System gehören')
            if kind == "teachcenter" and urlsplit(url).netloc != urlsplit(settings.teachcenter_url).netloc:
                raise ValueError("TeachCenter-URL muss zum konfigurierten TU-System gehören")
            scope = str(form.get("scope", "")).strip()
            config = {"scope": scope} if scope else {}
            if kind == 'gitlab':
                url = project_url(original_url)
                wsl_config=wsl_ssh_from_form(form)
                config = {'remote': remote_url(url, str(form.get('remote', '')),wsl=bool(wsl_config))}
                if wsl_config:config['wsl_ssh']=wsl_config
            if scope and not scope.startswith("/"):
                raise ValueError("Kursbereich beginnt mit /")
            course_id = int(form["course_id"])
            if not db.one("SELECT id FROM courses WHERE id=?", (course_id,)):
                raise ValueError("Kurs fehlt")
            db.execute("INSERT INTO sources(course_id,kind,title,url,config) VALUES(?,?,?,?,?)", (course_id, kind, str(form.get("title")) or url, url, encode(config)))
        except (ValueError, KeyError):
            raise HTTPException(400, "Quelle ungültig: Kurs, URL und Kursbereich prüfen") from None
        except Exception:
            raise HTTPException(400, "Quelle bereits vorhanden") from None
        return RedirectResponse("/sources", status_code=303)

    @app.post("/sources/{source_id}")
    async def source_edit(source_id: int, request: Request):
        form = await request.form()
        source = db.source(source_id)
        if not source:
            raise HTTPException(404)
        config = json.loads(source["config"])
        try:
            config["max_pages"] = max(1, min(200, int(form.get("max_pages", 30))))
            config["max_analysis_documents"] = max(1, min(30, int(form.get("max_analysis_documents", 3))))
            if source['kind'] == 'gitlab':
                if form.get('wsl_settings')=='1':
                    wsl_config=wsl_ssh_from_form(form)
                    if wsl_config:config['wsl_ssh']=wsl_config
                    else:config.pop('wsl_ssh',None)
                config['remote'] = remote_url(source['url'], str(form.get('remote', config.get('remote', ''))),wsl=bool(config.get('wsl_ssh')))
        except ValueError:
            raise HTTPException(400, "Abrufgrenze oder Git-Remote ungültig") from None
        db.execute("UPDATE sources SET active=?,config=? WHERE id=?", (int(form.get("active") == "on"), encode(config), source_id))
        if form.get("restart"):
            db.execute("UPDATE sources SET crawl_state='{}' WHERE id=?", (source_id,))
        return RedirectResponse("/sources", status_code=303)

    @app.get("/documents")
    async def documents(request: Request, all_versions: bool = False, include_inactive: bool = False,
                        q: str = '', course: str = '', state: str = '', page: int = 1):
        if state not in ('', 'pending', 'deferred', 'gaps', 'success', 'archived'):
            state = ''
        condition = "" if all_versions else "WHERE d.id=(SELECT d2.id FROM documents d2 WHERE d2.source_id=d.source_id AND d2.url=d.url ORDER BY fetched_at DESC,id DESC LIMIT 1)"
        rows = await asyncio.to_thread(db.rows, f"SELECT d.id,d.title,d.mime,d.fetched_at,d.analysis_state,d.processing_state,d.error,json_array_length(d.pages) page_count,s.title source_title,s.kind source_kind,s.url source_url,s.active source_active,c.title course_title,c.active course_active,c.id course_id FROM documents d JOIN sources s ON s.id=d.source_id LEFT JOIN courses c ON c.id=s.course_id {condition} ORDER BY d.fetched_at DESC,d.id DESC")
        query = q.strip().casefold()
        rows = [d for d in rows if (include_inactive or (d['source_active'] and d['course_active'] != 0))
                and (not course or str(d['course_id']) == course)
                and (not query or query in ' '.join([d['title'], d['course_title'] or '', d['source_title']]).casefold())]
        counts = {'pending':0,'deferred':0,'gaps':0,'success':0,'archived':0}
        for d in rows:
            deferred = (d['source_kind']=='public' and urlsplit(d['source_url']).hostname in ('apkpure.com','apkmirror.com','tube.tugraz.at','youtube.com','www.youtube.com','youtu.be')
                        and 'failed' not in (d['analysis_state'],d['processing_state'])
                        and 'pending' in (d['analysis_state'],d['processing_state']))
            d['display_state'] = ('archived' if d['analysis_state']=='archived' else
                'deferred' if deferred else
                'pending' if d['analysis_state'] in ('pending','failed') or d['processing_state'] in ('pending','failed') else
                'gaps' if d['analysis_state']=='partial' or d['processing_state']=='partial' else 'success')
            counts[d['display_state']] += 1
        rows = [d for d in rows if d['display_state'] == state] if state else [d for d in rows if d['display_state'] != 'archived']
        total = len(rows)
        page_count = max(1,(total+39)//40)
        page = max(1,min(page,page_count))
        return render(request, "documents.html", documents=rows[(page-1)*40:page*40], total=total,
                      counts=counts, page=page, page_count=page_count, state=state, q=q, course=course,
                      all_versions=all_versions, include_inactive=include_inactive,
                      courses=db.rows('SELECT * FROM courses ORDER BY active DESC,title'))

    @app.get("/documents/{doc_id}")
    async def document_detail(request: Request, doc_id: int, page: int = 1, back: str = '', reading: str = ''):
        document = db.one("SELECT d.*,c.title course_title FROM documents d LEFT JOIN sources s ON s.id=d.source_id LEFT JOIN courses c ON c.id=s.course_id WHERE d.id=?", (doc_id,))
        if not document:
            raise HTTPException(404)
        pages = json.loads(document["pages"])
        selected = next((p for p in pages if p["page"] == page), None)
        from .documents import reading_id
        current_page = selected
        from .questions import sensitive_mail_document
        if reading and selected and reading!=reading_id(selected):
            selected = next((p for p in selected.get('reading_history', []) if p['reading_id'] == reading), None)
            if not selected:
                raise HTTPException(404, 'Lesefassung nicht vorhanden')
        back_url,back_label=return_destination(back)
        page_numbers=[p['page'] for p in pages]
        index=page_numbers.index(page) if page in page_numbers else -1
        return render(request, "document.html", document=document, pages=pages, selected=selected, page=page, document_discussion_allowed=not sensitive_mail_document(document),
                      previous_page=page_numbers[index-1] if index>0 else None,
                      next_page=page_numbers[index+1] if index>=0 and index+1<len(page_numbers) else None,
                      back_url=back_url,back_label=back_label, current_page=current_page,
                      current_reading=reading_id(current_page) if current_page else '', historical_reading=bool(reading and current_page and reading!=reading_id(current_page)))

    @app.post('/documents/{doc_id}/pages/{page}/reread')
    async def reread_document_page(request: Request, doc_id: int, page: int):
        form = await request.form()
        option = str(form.get('ignore_grid', ''))
        if option not in ('on', 'off'):
            raise HTTPException(400, 'Verarbeitungsoption ungültig')
        try:
            await asyncio.to_thread(sync.store.reread_page, doc_id, page, option == 'on',
                                    expected_reading=str(form.get('reading_id', '')))
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        except (OSError, RuntimeError) as error:
            raise HTTPException(400, f'Seite nicht lesbar: {error}') from None
        await asyncio.to_thread(search.refresh)
        return RedirectResponse(f'/documents/{doc_id}?page={page}', status_code=303)

    @app.get("/documents/{doc_id}/original")
    async def original(doc_id: int):
        doc = db.one("SELECT * FROM documents WHERE id=?", (doc_id,))
        if not doc:
            raise HTTPException(404)
        path = Path(doc["original_path"])
        return FileResponse(path, filename="CampusBoard-" + str(doc_id) + path.suffix, media_type="application/octet-stream")

    @app.get("/documents/{doc_id}/pages/{page}.png")
    async def page_image(request: Request, doc_id: int, page: int):
        doc = db.one("SELECT mime,original_path FROM documents WHERE id=?", (doc_id,))
        if not doc or doc["mime"] != "application/pdf":
            raise HTTPException(404)
        try:
            png, etag = await asyncio.to_thread(page_images.image, doc["original_path"], page)
        except FileNotFoundError:
            raise HTTPException(404, 'Originaldatei nicht verfügbar') from None
        if png is None:
            raise HTTPException(404)
        headers = {'ETag': etag, 'Cache-Control': 'private, max-age=0, must-revalidate'}
        validators = [value.strip().removeprefix('W/') for value in request.headers.get('if-none-match', '').split(',')]
        if etag in validators or '*' in validators:
            return Response(status_code=304, headers=headers)
        return Response(png, media_type="image/png", headers=headers)

    @app.post("/import")
    async def import_document(request: Request):
        form = await request.form()
        text = str(form.get("text", ""))
        link = str(form.get("url", "")).strip()
        if link:
            try:
                link = safe_url(link)
            except ValueError:
                raise HTTPException(400, "Quelllink ungültig") from None
        published = str(form.get("published_at", "")) or None
        if published:
            try:date.fromisoformat(published)
            except ValueError:raise HTTPException(400, "Publikationsdatum ungültig") from None
        discord_message = bool(link and urlsplit(link).hostname in ('discord.com','discordapp.com') and urlsplit(link).path.startswith('/channels/'))
        if discord_message and not published and re.search(r'\b(?:heute|morgen|gestern|übermorgen|nächste[rsnm]?|today|tomorrow|yesterday|next)\b', text, re.I):
            raise HTTPException(400, "Bei relativen Angaben bitte das Datum der Discord-Nachricht angeben; der Importtag ist kein Nachrichtenzeitpunkt.")
        course_id = int(form["course_id"]) if form.get("course_id") else None
        uploaded = form.get("file")
        if uploaded and getattr(uploaded, "filename", ""):
            raw = await uploaded.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES or not raw.startswith(b"%PDF-"):
                raise HTTPException(400, "Nur PDF-Dateien bis 32 MiB")
            mime = "application/pdf"
            title = str(form.get("title", "")) or uploaded.filename
        elif text.strip():
            raw, mime = text.encode(), "text/plain"
            title = str(form.get("title", "")) or "Übernommener Text"
        else:
            raise HTTPException(400, "Text oder PDF fehlt")
        if discord_message:title = 'Übernommene Discord-Nachricht · '+title
        import hashlib
        url = link or "import:" + hashlib.sha256(raw).hexdigest()
        existing = db.one("SELECT * FROM sources WHERE kind='manual' AND url=?", (url,))
        source_id = existing["id"] if existing else db.execute("INSERT INTO sources(course_id,kind,title,url) VALUES(?,?,?,?)", (course_id, "manual", title, url))
        doc_id = await asyncio.to_thread(sync.store.save, source_id, url, title, mime, raw, published)
        await sync.imported()
        return RedirectResponse(f"/documents/{doc_id}", status_code=303)

    @app.post("/notes")
    async def add_note(request: Request):
        form = await request.form()
        title = str(form.get("title", "")).strip()
        if not title:
            raise HTTPException(400, "Titel fehlt")
        link = str(form.get("url", "")).strip()
        if link:
            try:
                link = safe_url(link)
            except ValueError:
                raise HTTPException(400, "Link ungültig") from None
        from .models import Candidate
        request_id=str(form['request_id']) if 'request_id' in form else secrets.token_hex(16)
        if not re.fullmatch(r'[0-9a-fA-F]{32}',request_id):
            raise HTTPException(400,'Ungültige Post-it-Anfrage-ID.')
        identity='note:manual:'+request_id.lower()
        course_value=str(form.get('course_id','')).strip()
        if course_value and not re.fullmatch(r'[1-9][0-9]*',course_value):
            raise HTTPException(400,'Ungültiger Kurs.')
        if len(course_value)>19 or course_value and int(course_value)>9223372036854775807:
            raise HTTPException(400,'Ungültiger Kurs.')
        course_id=int(course_value) if course_value else None
        date_role=str(form.get('date_role','deadline'))
        if date_role not in ('deadline','planned'):
            raise HTTPException(400,'Datum muss eine Frist oder eigene Planung sein.')
        try:
            due = date.fromisoformat(str(form['date'])) if form.get('date') else None
            clock=time.fromisoformat(str(form['time'])) if form.get('time') else None
            note = Candidate(title=title, kind="task", date=due, time=clock,
                             date_meaning=('start' if date_role=='planned' else 'deadline') if due else 'none',
                             description=str(form.get("text", "")) + ("\n"+link if link else ""),
                             quote="Eigene Notiz", page=1, anchor="note", obligation="optional")
        except ValueError:
            raise HTTPException(400, 'Datum, lokale Uhrzeit oder Notiz ungültig') from None
        with db.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            if course_id is not None:
                course=con.execute('SELECT semester FROM courses WHERE id=?',(course_id,)).fetchone()
                if course is None:raise HTTPException(400,'Gespeicherten Kurs wählen.')
                note=note.model_copy(update={'semester':course['semester']})
            existing=con.execute('SELECT id,course_id,payload FROM items WHERE identity=?',(identity,)).fetchone()
            if existing:
                if existing['course_id']!=course_id or json.loads(existing['payload'])!=note.model_dump(mode='json'):
                    raise HTTPException(409,'Diese Anfrage-ID gehört bereits zu einem anderen Post-it.')
                item_id=existing['id']
            else:
                item_id=con.execute('INSERT INTO items(course_id,identity,payload,pinned,created_at,changed_at) VALUES(?,?,?,1,?,?)',
                                    (course_id,identity,note.model_dump_json(),now(),now())).lastrowid
        url=f'/items/{item_id}'
        return {'url':url} if request.headers.get('x-requested-with')=='fetch' else RedirectResponse(url,status_code=303)

    @app.post("/api/sync")
    async def start_sync(source_id: int | None = None, analysis_only: bool = False):
        if analysis_only and source_id is None:
            raise HTTPException(400, 'Quelle für die gespeicherte Auswertung wählen')
        if source_id is not None:
            source = db.source(source_id)
            if not source or not source['active']:
                raise HTTPException(400,'Aktive Quelle wählen')
        return {"run_id": await sync.start(kind='analysis' if analysis_only else 'manual', source_id=source_id)}

    @app.get('/api/session')
    async def page_session(request: Request):
        # Same-origin script access only: the custom header requires a CORS
        # preflight for foreign sites, which this local app does not allow.
        origin = request.headers.get('origin')
        if (request.headers.get('x-requested-with') != 'fetch'
                or origin and origin != 'null' and urlsplit(origin).netloc != request.headers.get('host')):
            raise HTTPException(403, 'Fremder Ursprung abgewiesen')
        return JSONResponse({'csrf': csrf}, headers={'Cache-Control': 'no-store'})

    @app.get('/api/notifications')
    async def notifications(unread: bool = False, page: int = Query(1,ge=1,le=1000)):
        return await asyncio.to_thread(reminders.inbox,unread=unread,page=page)

    @app.post('/api/notifications/read')
    async def read_notifications(request: Request):
        try:
            body=await request.json()
            await asyncio.to_thread(reminders.read_notifications,body.get('ids'))
        except (ValueError,AttributeError) as error:
            raise HTTPException(400,'Ungültige Meldungsauswahl') from error
        return {'saved':True}

    @app.get("/api/status")
    async def status():
        last = db.one("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1")
        if last:
            last["results"] = json.loads(last["results"])
        return {"active": sync.active, "stage": sync.stage, "progress": sync.progress if sync.active else None, "run": last, "login_mode": browser.login_mode, "instance_id":app.state.instance_id,
                "class_completion_revision":class_completion.revision,"class_completion_error":class_completion.error,
                "model_ready": settings.model_ready, "ocr_ready": bool(settings.tesseract), "sources": db.sources()}

    @app.post("/api/login")
    async def login(service: str | None = None):
        if service not in (None, 'teachcenter','tugrazonline'):
            raise HTTPException(400, 'Unbekannter TU-Dienst')
        if sync.active:
            raise HTTPException(409, "Bitte den laufenden Sync beenden lassen und danach die Anmeldung öffnen.")
        try:
            from .browser import SERVICE_URLS
            checked = await browser.open_login(SERVICE_URLS[service] if service else None)
        except BrowserUnavailable as error:
            raise HTTPException(503, str(error)) from None
        return {'sessions':checked, 'message': 'Vorhandene Sitzungen geprüft. Gültige Sitzung wird verwendet; nur bei echter Loginseite wird Anmeldung geöffnet.'}

    def model_idle():
        if sync.active:
            raise HTTPException(409, "Bitte den laufenden Sync oder die Live-Probe beenden lassen.")

    def model_error(error):
        sync.auth.message = str(error) if isinstance(error, ChatGPTError) else f"ChatGPT-Verbindung fehlgeschlagen ({type(error).__name__})."

    @app.get("/model")
    async def model_configuration(request: Request):
        try:
            status = sync.auth.status()
            if status["connected"] and status["plan_enabled"] and not sync.auth.models:
                await sync.auth.list_models()
            status = sync.auth.status()
        except Exception as error:
            model_error(error)
            status = {"connected": False, "plan_enabled": False, "profiles": [], "models": [], "message": sync.auth.message}
        documents = db.rows("SELECT * FROM documents WHERE mime IN ('text/html','application/pdf') ORDER BY id")
        small = [d for d in documents if len(json.loads(d["pages"])) <= 40 and sum(len(p["text"]) for p in json.loads(d["pages"])) <= 100000]
        report = settings.data_dir / "model-probe.json"
        from .gotchas import SCOPES
        return render(request, "model.html", connection=status, documents=small, report=json.loads(report.read_text(encoding="utf-8")) if report.exists() else None,
                      gotchas=sync.model.gotchas.entries(),gotcha_scopes=SCOPES,courses=db.rows('SELECT id,title FROM courses ORDER BY title'),assistant_mode=assistant.mode())

    @app.post('/model/assistant-mode')
    async def assistant_mode(mode:str=Form(...)):
        if mode not in ('preview','direct'):raise HTTPException(400,'Vorschau oder Direktmodus auswählen.')
        db.execute("INSERT INTO app_preferences(name,value) VALUES('assistant_mode',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(encode({'mode':mode}),))
        return {'mode':mode,'message':'Direktmodus aktiv: eindeutige persönliche Einzelaktionen werden ausgeführt; unklare oder umfangreiche Änderungen behalten Rückfrage/Vorschau.' if mode=='direct' else 'Vorschau aktiv: Änderungen erst nach Übernehmen.'}

    @app.get('/model/gotchas.md')
    async def gotcha_markdown():
        return Response(sync.model.gotchas.markdown(),media_type='text/markdown; charset=utf-8',
                        headers={'Content-Disposition':'attachment; filename="STUDI_AGENTS.md"','Cache-Control':'no-store'})

    @app.post('/model/gotchas')
    async def gotcha_save(request:Request):
        form=await request.form()
        try:
            course_id=int(form['course_id']) if form.get('course_id') else None
            sync.model.gotchas.edit(identifier=str(form.get('id','')),title=str(form.get('title','')),
                lesson=str(form.get('lesson','')),scope=str(form.get('scope','all')),course_id=course_id,active=form.get('active')=='on')
        except ValueError as error:raise HTTPException(400,str(error)) from None
        return RedirectResponse('/model#gotchas',status_code=303)

    @app.post('/model/gotchas/{identifier}/toggle')
    async def gotcha_toggle(identifier:str,active:bool=Form(False)):
        try:sync.model.gotchas.toggle(identifier,active)
        except ValueError as error:raise HTTPException(400,str(error)) from None
        return RedirectResponse('/model#gotchas',status_code=303)

    @app.post("/model/connect")
    async def model_connect(request: Request):
        model_idle()
        form = await request.form()
        port = request.url.port or (443 if request.url.scheme == 'https' else 80)
        try:
            url = await sync.auth.begin(f"http://127.0.0.1:{port}/auth/callback", str(form.get("account", "")))
            opened = await asyncio.to_thread(webbrowser.open, url, new=2)
            if not opened:
                raise ChatGPTError("system_browser_unavailable")
            return {"message": "ChatGPT-Anmeldung im Systembrowser geöffnet. Dort anmelden und zustimmen; danach diese Seite aktualisieren."}
        except Exception as error:
            model_error(error)
            raise HTTPException(400, sync.auth.message) from None

    @app.get("/auth/callback")
    async def model_callback(request: Request):
        try:
            model_idle()
            await sync.auth.callback(dict(request.query_params))
            settings.oauth_probe_passed = False
            settings.oauth_model = ""
            settings.model_provider = "chatgpt"
            settings.save()  # Never fall back to the API-key provider after OAuth failure.
            if sync.auth.status()["plan_enabled"]:
                models = await sync.auth.list_models()
                if any(m["slug"] == "gpt-6-luna" for m in models):
                    settings.oauth_model = "gpt-6-luna"
                    settings.save()
        except Exception as error:
            model_error(error)
        return RedirectResponse("/model", status_code=303)

    @app.post("/model/configure")
    async def model_configure(request: Request):
        model_idle()
        form = await request.form()
        provider, model = str(form.get("provider", "")), str(form.get("model", ""))
        if provider not in ("chatgpt", "chat-completions"):
            raise HTTPException(400, "Modellanbieter unbekannt")
        if provider == "chatgpt":
            try:
                models = await sync.auth.list_models()
                if model not in {m["slug"] for m in models}:
                    raise ChatGPTError("model_not_in_account_catalog")
            except Exception as error:
                model_error(error)
                return RedirectResponse("/model", status_code=303)
        elif not settings.llm_base_url or not settings.llm_model:
            raise HTTPException(400, "Chat Completions zuerst unter Einrichtung ausdrücklich konfigurieren.")
        if provider != settings.model_provider or model != settings.oauth_model:
            settings.oauth_probe_passed = False
        settings.model_provider, settings.oauth_model = provider, model if provider == "chatgpt" else ""
        settings.save()
        return RedirectResponse("/model", status_code=303)

    @app.post("/model/disconnect")
    async def model_disconnect():
        model_idle()
        try:
            await sync.auth.logout()
        except Exception as error:
            model_error(error)
        settings.oauth_probe_passed = False
        settings.save()
        return RedirectResponse("/model", status_code=303)

    @app.post("/model/probe")
    async def model_probe(request: Request):
        model_idle()
        form = await request.form()
        try:
            page = db.one("SELECT * FROM documents WHERE id=? AND mime='text/html'", (int(form["page_id"]),))
            pdf = db.one("SELECT * FROM documents WHERE id=? AND mime='application/pdf'", (int(form["pdf_id"]),))
            if not page or not pdf or page["source_id"] != pdf["source_id"]:
                raise ValueError()
            for document in (page, pdf):
                pages = json.loads(document["pages"])
                if not pages or len(pages) > 40 or sum(len(p["text"]) for p in pages) > 100000:
                    raise ValueError()
            if settings.model_provider != "chatgpt" or not sync.auth.status()["plan_enabled"] or not settings.oauth_model:
                raise ChatGPTError("chatgpt_model_configuration_required")
        except (ValueError, KeyError):
            raise HTTPException(400, "Eine Kursseite und eine PDF derselben Quelle wählen (maximal 40 Seiten / 100.000 Zeichen je Dokument).") from None
        except ChatGPTError as error:
            raise HTTPException(400, str(error)) from None
        async def probe():
            report = {"at": now(), "model": settings.oauth_model, "documents": [page["id"], pdf["id"]], "state": "failed"}
            try:
                sync.stage = "Kleine ChatGPT-Verbindungsprobe"
                text = await sync.model.responses("Antworte exakt mit CAMPUSBOARD_OK.", "Verbindungsprobe.")
                if text.strip() != "CAMPUSBOARD_OK":
                    raise ChatGPTError("smoke_response_unexpected")
                report["smoke"] = "response.completed"
                for document in (page, pdf):
                    sync.stage = f"Live-Probe: {document['title']}"
                    db.execute("UPDATE documents SET progress='{}',analysis_state='pending' WHERE id=?", (document["id"],))
                    await sync.model.analyze(db.one("SELECT * FROM documents WHERE id=?", (document["id"],)), allow_probe=True)
                    checked = db.one("SELECT * FROM documents WHERE id=?", (document["id"],))
                    if checked["analysis_state"] not in ("success", "partial"):
                        raise ChatGPTError("extraction_failed: " + checked["error"][-240:])
                # Resume through the same publishing path, retaining bound identities and user status.
                count = db.one("SELECT count(*) n FROM items")["n"]
                for doc_id in report["documents"]:
                    db.execute("UPDATE documents SET analysis_state='pending' WHERE id=?", (doc_id,))
                    await sync.model.analyze(db.one("SELECT * FROM documents WHERE id=?", (doc_id,)), allow_probe=True)
                if count != db.one("SELECT count(*) n FROM items")["n"]:
                    raise ChatGPTError("duplicate_on_replay")
                items = [i for i in db.items() if not i["ignored"] and db.one("SELECT id FROM item_evidence WHERE item_id=? AND document_id IN (?,?)", (i["id"], page["id"], pdf["id"]))]
                report.update(state="completed", entries=len(items), calendar_entries=len(calendar_events(items)), duplicate_replay="passed")
                if not items:
                    raise ChatGPTError("no_organizational_entries_found_review_source")
                settings.oauth_probe_passed = True
                settings.save()
                sync.auth.message = "Live-Probe abgeschlossen. Fundstellen in Timeline/Board prüfen; normale Auswertung ist freigegeben."
            except Exception as error:
                report["state"] = "failed"
                model_error(error)
                report["error"] = sync.auth.message
            finally:
                (settings.data_dir / "model-probe.json").write_text(encode(report), encoding="utf-8")
                sync.stage = "Bereit"
        # Share the existing job slot so manual sync cannot race this bounded probe.
        async with sync.gate:
            model_idle()
            settings.oauth_probe_passed = False
            settings.save()
            sync.task = asyncio.create_task(probe())
        return RedirectResponse("/model", status_code=303)

    return app

