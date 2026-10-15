"""Server-rendered views share the current factual projection."""
from collections import defaultdict
from datetime import date,timedelta
import calendar
from pathlib import Path
from fastapi import APIRouter,Query,Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import PlainTextResponse
router=APIRouter()
templates=Jinja2Templates(directory=Path(__file__).parent/'templates')
NAV=[('/overview','Übersicht','◉'),('/board','Aufgaben & Notizen','✓'),('/pins','Pins','◇'),('/timeline','Timeline','↗'),('/calendar','Kalender','▦'),('/archive','Archiv','□'),('/sources','Kurse & Quellen','⊞'),('/documents','Dokumente','≡'),('/assistant','Assistent','◎'),('/mail','TU-Mail','✉'),('/model','KI-Verbindung','⌘'),('/settings','Einstellungen','⚙')]

def render(r,page,title,**extra):
    s=r.app.state
    response=templates.TemplateResponse(request=r,name='page.html',context={'request':r,'page':page,'title':title,'nav':NAV,'csrf':s.csrf,'settings':s.settings.values,'data_dir':str(s.paths.data),'secret_storage':s.secrets.status,'items':s.db.items(),'sources':s.db.sources(),'documents':s.db.documents(),'model_status':s.profiles.status(),'today':date.today().isoformat(),**extra})
    response.set_cookie('scpublic_session',s.session,httponly=True,samesite='strict')
    return response

@router.get('/')
@router.get('/overview')
def overview(r:Request):
    return render(r,'overview','Übersicht',active=[i for i in r.app.state.db.items() if i['status'] not in ('done','ignored')])

@router.get('/board')
def board(r:Request):return render(r,'board','Aufgaben & Notizen',active=[i for i in r.app.state.db.items() if i['status'] not in ('done','ignored')])

@router.get('/pins')
def pins(r:Request):return render(r,'board','Pins',active=[i for i in r.app.state.db.items() if i.get('pinned') and i['status'] not in ('done','ignored')])

@router.get('/archive')
def archive(r:Request):return render(r,'archive','Archiv',active=[i for i in r.app.state.db.items() if i['status'] in ('done','ignored')])

@router.get('/timeline')
def timeline(r:Request,start:str='',end:str=''):
    groups=defaultdict(list)
    for item in r.app.state.db.items():
        day=item.get('end_date') if item.get('date_meaning') in ('deadline','window') and item.get('end_date') else item.get('date')
        day=day or 'Ohne belegtes Datum'
        if start and day<start:continue
        if end and day>end and day!='Ohne belegtes Datum':continue
        groups[day].append(item)
    return render(r,'timeline','Timeline',groups=sorted(groups.items()),start=start,end=end)

@router.get('/calendar')
def cal(r:Request,month:str='',view:str='month',anchor:str=Query('',alias='date')):
    from ..config import VIENNA
    from datetime import datetime
    selected=date.fromisoformat(anchor) if anchor else (date.fromisoformat(month+'-01') if month else datetime.now(VIENNA).date())
    day=date.fromisoformat(month+'-01') if month else selected.replace(day=1)
    week_start=selected-timedelta(days=selected.weekday())
    week_days=[week_start+timedelta(days=n) for n in range(7)]
    rows=calendar.Calendar(firstweekday=0).monthdatescalendar(day.year,day.month)
    groups=defaultdict(list)
    for item in r.app.state.db.items():
        effective=item.get('end_date') if item.get('date_meaning') in ('deadline','window') and item.get('end_date') else item.get('date')
        if effective:groups[effective].append(item)
    previous=(day-timedelta(days=1)).strftime('%Y-%m');next_month=(day+timedelta(days=32)).strftime('%Y-%m')
    days=[day+timedelta(days=n) for n in range(calendar.monthrange(day.year,day.month)[1])]
    lanes=[]
    for item in r.app.state.db.items():
        if not item.get('date') and not item.get('end_date'):continue
        first=date.fromisoformat(item.get('date') or item['end_date'])
        last=date.fromisoformat(item.get('end_date') or first.isoformat())
        if last<days[0] or first>days[-1]:continue
        left=(max(first,days[0])-days[0]).days
        span=(min(last,days[-1])-max(first,days[0])).days+1
        lanes.append({'item':item,'left':left/len(days)*100,'width':span/len(days)*100,'point':first==last,'before':first<days[0],'after':last>days[-1]})
    return render(r,'calendar','Kalender',weeks=rows,week_days=week_days,selected_date=selected.isoformat(),previous_week=(week_start-timedelta(days=7)).isoformat(),next_week=(week_start+timedelta(days=7)).isoformat(),groups=groups,gantt_days=days,gantt_lanes=lanes,month=day.strftime('%Y-%m'),previous=previous,next_month=next_month,view=view)

@router.get('/items/{item_id}')
def item(r:Request,item_id:int):
    entry=r.app.state.db.item(item_id)
    if not entry:raise KeyError(item_id)
    return render(r,'item',entry['title'],entry=entry)

@router.get('/items/{item_id}/compare')
def compare(r:Request,item_id:int):
    entry=r.app.state.db.item(item_id)
    if not entry:raise KeyError(item_id)
    return render(r,'compare','Quellenvergleich',entry=entry)

@router.get('/documents')
def docs(r:Request):return render(r,'documents','Dokumente')

@router.get('/documents/{document_id}')
def reader(r:Request,document_id:int,page:int=1):
    doc=r.app.state.db.document(document_id);pages=r.app.state.db.pages(document_id)
    selected=r.app.state.documents.page(document_id,page)
    if not doc or not selected:raise KeyError(document_id)
    return render(r,'reader',doc['title'],document=doc,pages=pages,selected=selected)

@router.get('/search')
def search(r:Request,q:str=''):
    if len(q)>240:raise ValueError('Suchbegriff zu lang')
    return render(r,'search','Suche',q=q,results=r.app.state.db.items(query=q) if q else [],document_results=r.app.state.documents.search(q) if q else [])

@router.get('/sources')
def sources(r:Request):return render(r,'sources','Kurse & Quellen')

@router.get('/assistant')
def assistant(r:Request):return render(r,'assistant','StudyCenter-Assistent')

@router.get('/model')
def model(r:Request):return render(r,'model','KI-Verbindung',oauth_accounts=r.app.state.oauth.accounts())

@router.get('/mail')
def mail(r:Request):return render(r,'mail','TU-Mail',mail_status=r.app.state.sync.mail_status())

@router.get('/settings')
def settings(r:Request):return render(r,'settings','Einstellungen',reminders=r.app.state.reminders.settings(),notifications=r.app.state.reminders.notifications())

@router.get('/setup')
def setup(r:Request,step:int=1):return render(r,'setup','StudyCenter Public einrichten',step=max(1,min(step,5)))

@router.get('/help')
def help_page(r:Request):
    path=Path(__file__).resolve().parents[1]/'resources'/'QUICKSTART.md'
    return render(r,'help','Kurzanleitung',help_text=path.read_text(encoding='utf-8') if path.exists() else 'Die Kurzanleitung wird mit dem geprüften Paket ausgeliefert.')

@router.get('/license')
def license_text(r:Request):
    path=Path(__file__).resolve().parents[1]/'resources'/'LICENSE'
    return PlainTextResponse(path.read_text(encoding='utf-8') if path.exists() else 'AGPL-3.0-only; https://www.gnu.org/licenses/agpl-3.0.html')
