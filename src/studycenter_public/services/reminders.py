"""Local reminder queue with quiet hours and bounded missed-notification handling."""
import json
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ..storage.db import encode, now as db_now

DEFAULTS = {'enabled':False,'timezone':'Europe/Vienna','quiet_start':'22:00','quiet_end':'07:00','offsets':{'exam':[60,30],'exercise':[30],'lecture':[30],'task':[1440,120],'registration':[10,0]},'exceptions':{}}


def validate_personal_reminder(value):
    if not isinstance(value,dict) or set(value)-{'enabled','offsets'}:
        raise ValueError('Invalid personal reminder exception')
    if 'enabled' in value and not isinstance(value['enabled'],bool):
        raise ValueError('Invalid reminder exception enabled state')
    if 'offsets' in value and (not isinstance(value['offsets'],list) or len(value['offsets'])>5 or any(type(offset) is not int or not 0<=offset<=43_200 for offset in value['offsets'])):
        raise ValueError('Personal reminder offsets require at most five bounded integer minutes')
    return value


def personal_reminder_state(db,connection,item_id):
    row=db.one("SELECT value FROM settings WHERE name='reminders'",connection=connection)
    exceptions=json.loads(row['value']).get('exceptions',{}) if row else {}
    key=str(item_id)
    return {'present':key in exceptions,'value':exceptions.get(key)}


def write_personal_reminder(db,connection,item_id,state):
    row=db.one("SELECT value FROM settings WHERE name='reminders'",connection=connection)
    stored=json.loads(row['value']) if row else {}
    exceptions=dict(stored.get('exceptions',{}))
    if state['present']:
        exceptions[str(item_id)]=validate_personal_reminder(state['value'])
    else:
        exceptions.pop(str(item_id),None)
    stored['exceptions']=exceptions
    connection.execute("INSERT INTO settings VALUES('reminders',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(encode(stored),))


class ReminderService:
    def __init__(self,db):
        self.db=db

    def settings(self):
        stored=self.db.setting('reminders',{})
        return self._normalized(stored)

    @staticmethod
    def _normalized(stored):
        return json.loads(encode(DEFAULTS | stored | {'offsets':DEFAULTS['offsets'] | stored.get('offsets',{})}))

    def configure(self,settings):
        if set(settings)-set(DEFAULTS):raise ValueError('Unknown reminder setting')
        with self.db.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            row=self.db.one("SELECT value FROM settings WHERE name='reminders'",connection=con)
            previous=self._normalized(json.loads(row['value']) if row else {})
            value=previous | settings
            value['offsets']=previous['offsets'] | settings.get('offsets',{})
            self._validate_settings(value)
            value['exceptions']={str(key):exception for key,exception in value['exceptions'].items()}
            old,new=previous['exceptions'],value['exceptions']
            missing=object()
            for key in old.keys()|new.keys():
                if old.get(key,missing)!=new.get(key,missing):
                    con.execute('UPDATE items SET version=version+1,updated_at=? WHERE id=? AND deleted=0',(db_now(),int(key)))
            con.execute("INSERT INTO settings VALUES('reminders',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(encode(value),))
            return value

    @staticmethod
    def _validate_settings(value):
        ZoneInfo(value['timezone'])
        time.fromisoformat(value['quiet_start']);time.fromisoformat(value['quiet_end'])
        if not isinstance(value['enabled'],bool) or set(value['offsets'])-set(DEFAULTS['offsets']):
            raise ValueError('Reminder offsets must be bounded minutes')
        for offsets in value['offsets'].values():
            if not isinstance(offsets,list) or len(offsets)>10 or any(type(n) is not int or n<0 or n>43_200 for n in offsets):raise ValueError('Reminder offsets must be bounded minutes')
        if not isinstance(value['exceptions'],dict):raise ValueError('Personal reminder exceptions must be a mapping')
        for key,exception in value['exceptions'].items():
            if not str(key).isdigit() or int(key)<1 or str(int(key))!=str(key):raise ValueError('Invalid personal reminder exception')
            validate_personal_reminder(exception)

    save_settings=configure

    def _instant(self,item,zone):
        value=item['data']
        if value.get('due_at'):
            result=datetime.fromisoformat(value['due_at'])
        else:
            if value.get('kind')=='registration':
                opening=re.compile(r'\b(?:Anmeldebeginn|Anmeldung\s+(?:beginnt|öffnet)|registration\s+(?:opens|starts|opening)|enrol(?:l)?ment\s+(?:opens|starts)|opens\s+(?:on|at|\d{1,4}))\b',re.I)
                closing_indicator=re.compile(r'\b(?:Anmeldeschluss|Anmeldeende|Anmeldung\s+endet|registration\s+(?:closes|ends|deadline)|enrol(?:l)?ment\s+(?:closes|ends|deadline))\b',re.I)
                current_quotes=[proof.get('quote','') for proof in item.get('evidence',[]) if proof.get('current')]
                proof_opening=any(opening.search(quote) for quote in current_quotes) and not any(closing_indicator.search(quote) for quote in current_quotes)
                personal_start=(item.get('identity','').startswith('personal:') or item.get('overrides',{}).get('date_meaning')=='start') and value.get('date_meaning')=='start'
                if value.get('date_meaning') not in ('window','start') or (not value.get('end_date') and not proof_opening and not personal_start):return None
            closing=value.get('date_meaning') in ('deadline','window')
            if value.get('kind')=='registration':closing=False
            day=value.get('end_date') if closing and value.get('end_date') else value.get('date')
            clock=value.get('end_time') if closing and value.get('end_date') else value.get('time')
            if not day or not clock:return None
            result=datetime.fromisoformat(day+'T'+clock)
        return result.replace(tzinfo=zone) if result.tzinfo is None else result

    def due(self,now=None):
        now=now or datetime.now(timezone.utc)
        if now.tzinfo is None:raise ValueError('Reminder evaluation requires an aware instant')
        settings=self.settings()
        if not settings['enabled']:return []
        zone=ZoneInfo(settings['timezone']);local=now.astimezone(zone).time().replace(tzinfo=None)
        start=time.fromisoformat(settings['quiet_start']);end=time.fromisoformat(settings['quiet_end'])
        quiet=(start<=local<end) if start<end else (local>=start or local<end) if start!=end else False
        items=self.db.items(active_only=True);emitted=[]
        confirmed_exams={i['data'].get('related_key') for i in items if i['booked_event'] and i['data'].get('kind')=='exam' and i['data'].get('related_key')}
        with self.db.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            for item in items:
                value=item['data']
                if value.get('source_state')=='cancelled' or value.get('ignored'):continue
                if value.get('kind') not in ('task','event','exam','registration'):continue
                if value.get('kind')=='exam' and not item['booked_event']:continue
                if value.get('kind')=='registration' and value.get('related_key') in confirmed_exams:continue
                if value.get('kind')=='event' and (not item['evidence'] or value.get('obligation')!='required'):continue
                category=value['kind']
                if category=='event':
                    title=value.get('title','').casefold()
                    category='lecture' if 'lecture' in title or 'vorlesung' in title else 'exercise'
                exception=settings['exceptions'].get(str(item['id']),{})
                if exception.get('enabled') is False:continue
                deadline=self._instant(item,zone)
                if deadline is None:continue
                for offset in exception.get('offsets',settings['offsets'].get(category,[])):
                    due=deadline-timedelta(minutes=offset)
                    key=f"{item['id']}:{due.isoformat()}:{offset}"
                    age=now-due
                    if age<timedelta(0):continue
                    state='skipped' if age>timedelta(minutes=2) else 'pending'
                    con.execute('INSERT OR IGNORE INTO reminder_jobs VALUES(?,?,?,?,?)',(key,item['id'],due.isoformat(),state,encode({'title':value['title'],'offset_minutes':offset})))
                    job=con.execute('SELECT state FROM reminder_jobs WHERE job_key=?',(key,)).fetchone()
                    if job['state']=='pending' and not quiet:
                        if age>timedelta(minutes=2):
                            con.execute("UPDATE reminder_jobs SET state='skipped' WHERE job_key=?",(key,));continue
                        if len(emitted)>=100:continue
                        con.execute("UPDATE reminder_jobs SET state='emitted' WHERE job_key=?",(key,))
                        emitted.append({'job_key':key,'item_id':item['id'],'title':value['title'],'due_at':due.isoformat(),'state':'emitted','channel':'local'})
            return emitted[:100]

    run_due=due

    def notifications(self):
        return [json.loads(row['metadata']) | row | {'id':row['job_key'],'read':bool(json.loads(row['metadata']).get('read'))} for row in self.db.rows("SELECT * FROM reminder_jobs WHERE state='emitted' ORDER BY due_at DESC LIMIT 200")]

    def mark_read(self,job_key):
        with self.db.connection() as con:
            row=self.db.one("SELECT * FROM reminder_jobs WHERE job_key=? AND state='emitted'",(job_key,),con)
            if not row:raise ValueError('Unknown notification')
            con.execute('UPDATE reminder_jobs SET metadata=? WHERE job_key=?',(encode(json.loads(row['metadata']) | {'read':True}),job_key))
        return {'id':job_key,'read':True}

    def jobs(self):
        return self.db.rows('SELECT * FROM reminder_jobs ORDER BY due_at DESC LIMIT 200')
