"""Display helpers: never change stored source facts or personal decisions."""
import re

_ACTION = re.compile(r'\b(?:bearbeiten|vorbereiten|üben|prüfen|einreichen|abgeben|lesen|lernen|lösen|implementieren|konfigurieren|ankreuzen|hochladen|erstellen|durchführen|teilnehmen|wiederholen|ansehen)\b', re.I)
_IDENTIFIER = re.compile(r'\b(arbeitsblatt|ab|quiz|einheit|lab|assignment)\s*[-–:]?\s*(\d+)\b',re.I)


def _identifiers(title):
    return {('ab' if kind.casefold() in ('arbeitsblatt','ab') else kind.casefold(),int(number)) for kind,number in _IDENTIFIER.findall(title or '')}


def _matching_identifiers(left,right):
    left,right=_identifiers(left),_identifiers(right)
    if not left & right:return False
    for kind in {k for k,_ in left} & {k for k,_ in right}:
        if {n for k,n in left if k==kind}!={n for k,n in right if k==kind}:return False
    return True


def attach_linked_deadlines(items,courses):
    """Project a selected same-course source's closing, preserving every fact."""
    by_id={item['id']:item for item in items}
    for item in items:
        linked=item.get('linked_source_id')
        if not linked or item.get('kind')!='task' or not item.get('identity','').startswith('personal:'):continue
        item['linked_deadline_unavailable']=True
        if item.get('date_meaning')!='start':continue
        source=by_id.get(linked)
        if (not source or not item.get('course_id') or source.get('course_id')!=item['course_id']
            or source.get('identity','').startswith('personal:') or source.get('date_conflict')
            or source.get('source_state') in ('cancelled','unclear')
            or not any(proof.get('current') for proof in source.get('evidence',[]))
            or not _matching_identifiers(item.get('title'),source.get('title'))):continue
        current_titles=[claim.get('title') for claim in source.get('source_claims',[]) if claim.get('title')]
        if (not any(_matching_identifiers(item.get('title'),title) for title in current_titles)
            or any(_identifiers(title) and not _matching_identifiers(item.get('title'),title) for title in current_titles)):continue
        source_kind=source.get('kind');meaning=source.get('date_meaning')
        if source_kind!='task' and not (source_kind=='exam' and meaning=='window' and re.search(r'\bquiz\b',source.get('title') or '',re.I)):continue
        if meaning=='window':closing=source.get('end_date');clock=source.get('end_time')
        elif meaning=='deadline':
            closing=source.get('end_date') or source.get('date')
            clock=source.get('end_time') if source.get('end_date') else source.get('time')
        else:continue
        semester=(courses.get(item['course_id']) or {}).get('semester') or ''
        if semester and source.get('semester') and semester!=source['semester']:continue
        claimed_semesters={claim.get('semester') for claim in source.get('source_claims',[]) if claim.get('semester')}
        if semester and any(claim!=semester for claim in claimed_semesters):continue
        if re.fullmatch(r'\d{4}[WS]',semester) and not ({'date','end_date'} & set(source.get('overrides',{}))):
            lower=semester[:4]+('-08-01' if semester.endswith('W') else '-02-01')
            if closing and closing<lower:continue
        if closing:
            item['linked_deadline']={'date':closing,'time':clock,'source_id':source['id']}
            item['linked_deadline_unavailable']=False
    return items


def display_title(item):
    title = (item.get('title') or '').strip()
    is_task = item.get('kind') == 'task'
    is_quiz_window = item.get('kind') == 'exam' and item.get('date_meaning') == 'window' and re.search(r'\bquiz\b', title, re.I)
    if is_task or is_quiz_window:
        title = re.sub(r'^AB\s*(\d+)\b', r'Arbeitsblatt \1', title, flags=re.I)
        title = re.sub(r'^Quiz\s*(?:[–-]\s*)?Einheit\s*(\d+)\b', r'Quiz zur Einheit \1', title, flags=re.I)
        if re.match(r'^(?:Quiz|Arbeitsblatt|Aufgabenblatt|Hausübung|HÜ|Assignment|Task|Programmieraufgabe)\b', title, re.I) and not _ACTION.search(title):
            title += ' bearbeiten'
    return title


def overview_date(item):
    """A task window's opening is not its closing date."""
    if item.get('linked_deadline'):return item['linked_deadline']['date']
    if item.get('linked_deadline_unavailable'):return None
    if (item.get('kind') == 'task' or (item.get('kind') == 'exam' and re.search(r'\bquiz\b', item.get('title') or '', re.I))) and item.get('date_meaning') == 'window':
        return item.get('end_date')
    if item.get('date_meaning') in ('deadline', 'window'):
        return item.get('end_date') or item.get('date')
    return item.get('date')


def calendar_clock(item):
    if item.get('linked_deadline'):return item['linked_deadline'].get('time') or ''
    if item.get('end_date') and item.get('date_meaning') in ('deadline', 'window'):
        return item.get('end_time') or ''
    return item.get('time') or ''
