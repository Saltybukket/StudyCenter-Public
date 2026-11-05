"""Complete only clearly identified past course sessions, using local data."""
import json
import re
import threading
from datetime import date, datetime

from ..config import VIENNA
from ..storage.db import now

_EXCLUDED = re.compile(r'prüfung|pruefung|klausur|\bexam\b|\bquiz\b|testat|anmeld|registr|abmeld|vorbereit|preparation|\bdeadline\b|abgabe|hausübung|hausuebung|hausaufgabe|sprechstunde|\bnotice\b|entfällt|abgesagt|cancelled', re.I)
_SESSION = re.compile(r'vorlesung|übung(?:seinheit)?|uebung(?:seinheit)?|lehrveranstaltung|\b(?:VO|VU|UE)\b|\blecture\b|\bexercise\b', re.I)
_LAB = re.compile(r'\b(?:LU|lab|labor(?:übung)?)\b', re.I)
_GENERIC = re.compile(r'(?:^|[·:–-]\s*)(?:lehrveranstaltung|lv)\s*$', re.I)


def session_end(item, course):
    data = item['data']
    if (data.get('kind') != 'event' or not item.get('course_id') or not any(proof.get('current') for proof in item.get('evidence', []))
        or data.get('source_state') != 'active' or data.get('date_meaning') != 'start'
        or data.get('obligation') != 'required' or item.get('date_conflict') or data.get('ignored')):
        return None
    title = data.get('title') or ''
    if _EXCLUDED.search(title): return None
    if not item.get('personal_event') and re.search(r'keine? persönliche.*(?:buchung|zuteilung)|kein.*persönlicher slot|no personal.*(?:slot|assignment)|alternative termine', data.get('uncertainty') or '', re.I): return None
    if re.search(r'(?:datum|termin|zeitpunkt).{0,35}(?:unklar|unsicher|ungewiss|nicht eindeutig)|(?:unclear|uncertain).{0,35}(?:date|time)', data.get('uncertainty') or '', re.I): return None
    group = data.get('group')
    if group and group != 'Standardgruppe' and not (course.get('confirmed') is True and group == course.get('group')): return None
    if _LAB.search(title) and not item.get('booked_event'): return None
    if _GENERIC.search(title):
        if not item.get('personal_event') or course.get('course_type') not in {'VO','VU','UE','LU'}: return None
        if course.get('course_type') == 'LU' and not item.get('booked_event'): return None
    elif not _SESSION.search(title) and not (_LAB.search(title) and item.get('booked_event')):
        return None
    try:
        start = date.fromisoformat(data['date'])
        end = date.fromisoformat(data['end_date']) if data.get('end_date') else start
        if end < start or (not data.get('end_date') and data.get('time') and data.get('end_time') and data['end_time'] < data['time']): return None
        return end
    except (ValueError, TypeError, KeyError):
        return None


def _legacy_status_changed(con,item_id):
    """Honor recorded pre-ledger status changes after an application upgrade."""
    path=f'$."{item_id}".row.status'
    ledger_path=f'$."{item_id}".completion'
    item_path=f'$."{item_id}"'
    return bool(con.execute("""SELECT 1 FROM action_receipts
        WHERE json_type(before_state,?) IS NULL AND json_type(before_state,?)='text' AND json_type(after_state,?)='text'
          AND (json_extract(before_state,?) IS NOT json_extract(after_state,?) OR
               json_remove(json_extract(before_state,?),'$.row.version','$.row.updated_at')=
               json_remove(json_extract(after_state,?),'$.row.version','$.row.updated_at')) LIMIT 1""",
        (ledger_path,path,path,path,path,item_path,item_path)).fetchone())


class ClassCompletionService:
    def __init__(self, db):
        self.db = db
        self.error = ''
        self._day = None
        self._versions = {}
        self._course_signature = ()
        self._generation = 0
        self._cache_lock = threading.Lock()

    def invalidate(self):
        with self._cache_lock:
            self._day = None
            self._versions = {}
            self._course_signature = ()
            self._generation += 1

    def tick(self, current=None):
        current = current or datetime.now(VIENNA)
        if current.tzinfo is None or current.utcoffset() is None: raise ValueError('Aware datetime required')
        today = current.astimezone(VIENNA).date()
        changed = []
        with self._cache_lock:
            generation, previous_day, previous_versions, previous_courses = self._generation, self._day, self._versions.copy(), self._course_signature
        with self.db.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            course_rows = self.db.rows('SELECT id,metadata FROM courses ORDER BY id', connection=con)
            courses = {row['id']:json.loads(row['metadata']) for row in course_rows}
            course_signature = tuple((row['id'],row['metadata']) for row in course_rows)
            ledger = {row['item_id']:row for row in self.db.rows('SELECT * FROM class_completions', connection=con)}
            ids = self.db.rows('SELECT id,version FROM items WHERE deleted=0', connection=con)
            pending = [row for row in ids if previous_day != today or previous_courses != course_signature or previous_versions.get(row['id']) != row['version']]
            for row in pending:
                item = self.db._item(row['id'], con)
                entry = ledger.get(item['id'])
                check_item=item
                if entry and entry['state']=='automatic' and entry['previous_status']=='planned':
                    # An older automatic completion may itself have hidden the
                    # personal-planning signal needed by a generic class title.
                    check_item=item | {'personal_event':True}
                end = session_end(check_item, courses.get(item.get('course_id'), {}))
                if entry and entry['state'] == 'automatic' and item['status'] == 'done' and (end is None or end >= today):
                    con.execute("UPDATE class_completions SET state='reverted' WHERE item_id=?", (item['id'],))
                    con.execute('UPDATE items SET status=?,version=version+1,updated_at=? WHERE id=?', (entry['previous_status'], now(), item['id']))
                    changed.append(item['id'])
                elif end and end < today and item['status'] in ('open','planned') and (not entry or entry['state'] == 'reverted'):
                    if not entry and (item['status']=='planned' or _legacy_status_changed(con,item['id'])):
                        con.execute("INSERT INTO class_completions VALUES(?,?,?,'manual')",(item['id'],item['status'],end.isoformat()))
                        continue
                    con.execute("INSERT INTO class_completions VALUES(?,?,?,'automatic') ON CONFLICT(item_id) DO UPDATE SET previous_status=excluded.previous_status,end_date=excluded.end_date,state='automatic'", (item['id'],item['status'],end.isoformat()))
                    con.execute("UPDATE items SET status='done',version=version+1,updated_at=? WHERE id=?", (now(),item['id']))
                    changed.append(item['id'])
            # Cache only after successful writes. Personal actions and new source
            # evidence increment versions; a new local day reevaluates all rows.
            versions = {row['id']:row['version'] for row in self.db.rows('SELECT id,version FROM items WHERE deleted=0', connection=con)}
        with self._cache_lock:
            if self._generation == generation:
                self._versions = versions
                self._day = today
                self._course_signature = course_signature
        return changed
