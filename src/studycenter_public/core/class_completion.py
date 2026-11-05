"""Complete past, clearly identified course sessions using local data only."""
import asyncio
import json
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .presentation import personal_state
from .async_utils import finish_thread

VIENNA = ZoneInfo('Europe/Vienna')
EXCLUDED = re.compile(r'prüfung|pruefung|klausur|\bexam\b|\bquiz\b|testat|anmeld|registr|abmeld|vorbereit|preparation|\bdeadline\b|abgabe|hausübung|hausuebung|hausaufgabe|sprechstunde|\bnotice\b|\bentfällt\b|\babgesagt\b|\bcancelled\b', re.I)
SESSION = re.compile(r'vorlesung|übung(?:seinheit)?|uebung(?:seinheit)?|lehrveranstaltung|\b(?:VO|VU|UE)\b|\blecture\b|\bexercise\b', re.I)
LAB = re.compile(r'\b(?:LU|lab|labor(?:übung)?)\b', re.I)
GENERIC = re.compile(r'(?:^|[·:–-]\s*)(?:lehrveranstaltung|lv)\s*$', re.I)


def session_end(item, course_types):
    """Return the last certain local date for an eligible class, otherwise None."""
    value = item['data']
    if (value.get('kind') != 'event' or item.get('ignored') or not item.get('course_id')
            or value.get('source_state') != 'active' or value.get('date_meaning') != 'start'
            or item.get('date_conflict') or item.get('calendar_duplicate_of')):
        return None
    title = value.get('title') or ''
    if EXCLUDED.search(title):
        return None
    uncertainty = value.get('uncertainty') or ''
    if re.search(r'(?:datum|termin|zeitpunkt).{0,35}(?:unklar|unsicher|ungewiss|nicht eindeutig)|(?:unclear|uncertain).{0,35}(?:date|time)', uncertainty, re.I):
        return None
    if personal_state(item)['offered']:
        return None
    group = value.get('group')
    if group and group != 'Standardgruppe' and group not in item.get('confirmed_groups', []):
        return None
    personal = bool(item.get('personal_event') or item.get('booked_event'))
    if LAB.search(title) and not item.get('booked_event'):
        return None
    if GENERIC.search(title):
        if not personal or len(course_types) != 1 or next(iter(course_types)) not in {'VO','VU','UE','LU'}:
            return None
        if 'LU' in course_types and not item.get('booked_event'):
            return None
    elif not SESSION.search(title) and not (LAB.search(title) and item.get('booked_event')):
        return None
    try:
        start = date.fromisoformat(value['date'])
        end = date.fromisoformat(value['end_date']) if value.get('end_date') else start
        if end < start:
            return None
        # Missing end date plus an earlier end time could represent an overnight event.
        if not value.get('end_date') and value.get('time') and value.get('end_time') and value['end_time'] < value['time']:
            return None
        return end
    except (ValueError, TypeError, KeyError):
        return None


class ClassCompletionService:
    def __init__(self, db):
        self.db = db
        self.stop = asyncio.Event()
        self.running = False
        self.busy = False
        self.error = ''
        self.revision = 0

    async def run(self, can_run=None):
        self.running = True
        try:
            while not self.stop.is_set():
                if can_run is None or can_run():
                    try:
                        self.busy = True
                        await finish_thread(self.tick)
                        self.error = ''
                    except Exception as exc:
                        self.error = str(exc)
                    finally:
                        self.busy = False
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=30)
                except TimeoutError:
                    pass
        finally:
            self.running = False

    def tick(self, current=None):
        current = current or datetime.now(VIENNA)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError('An aware datetime is required')
        today = current.astimezone(VIENNA).date()
        stamp = current.astimezone(VIENNA).isoformat()
        changed = []
        with self.db.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            items = self.db._items(False, con, display=False)
            types = {}
            for source in self.db.rows("SELECT course_id,config FROM sources WHERE active=1", connection=con):
                try:
                    name = json.loads(source['config'] or '{}').get('course_type', '').upper()
                except (ValueError, AttributeError, TypeError):
                    continue
                if name:
                    types.setdefault(source['course_id'], set()).add(name)
            ledger = {r['item_id']: r for r in self.db.rows('SELECT * FROM class_completions', connection=con)}
            for item in items:
                end = session_end(item, types.get(item['course_id'], set()))
                entry = ledger.get(item['id'])
                if entry and entry['state'] == 'automatic' and item['status'] == 'done' and end and end >= today:
                    # The source date moved into the future. Only our own status is undone.
                    con.execute("UPDATE class_completions SET state='reverted' WHERE item_id=?", (item['id'],))
                    con.execute('UPDATE items SET status=? WHERE id=?', (entry['previous_status'], item['id']))
                    changed.append(item['id'])
                elif end and end < today and item['status'] in ('open', 'doing') and (not entry or entry['state'] == 'reverted'):
                    con.execute('''INSERT INTO class_completions(item_id,completed_at,end_date,previous_status,state)
                        VALUES(?,?,?,?, 'automatic') ON CONFLICT(item_id) DO UPDATE SET completed_at=excluded.completed_at,
                        end_date=excluded.end_date,previous_status=excluded.previous_status,state='automatic' ''',
                        (item['id'], stamp, end.isoformat(), item['status']))
                    con.execute("UPDATE items SET status='done' WHERE id=?", (item['id'],))
                    changed.append(item['id'])
        if changed:
            self.revision += 1
        return changed
