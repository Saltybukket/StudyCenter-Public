"""Display-only task text and source-bound closing dates.

attach_task_displays adds ``task_display`` without changing stored data. A
linked closing date is used only for an explicitly labelled local original,
in the same course, with a matching task identifier and unconflicted date.
"""
import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from .source_validity import in_course_period

_LINK = re.compile(r'(?:Originaleintrag\s+|Abgabe\s+laut\s+)(https?://[^\s;]+)', re.I)
_IDENTIFIER = re.compile(r'\b(arbeitsblatt|ab|quiz|einheit|lab|assignment)\s*[-–:]?\s*(\d+)\b', re.I)
_ACTION = re.compile(r'\b(?:bearbeiten|vorbereiten|üben|prüfen|einreichen|abgeben|lesen|lernen|lösen|implementieren|konfigurieren|ankreuzen|hochladen|erstellen|durchführen|teilnehmen|wiederholen|ansehen)\b', re.I)


def _identifiers(text):
    result = set()
    for kind, number in _IDENTIFIER.findall(text):
        kind = kind.lower()
        result.add(('ab' if kind in ('arbeitsblatt', 'ab') else kind, int(number)))
    return result


def _matching_identifiers(left, right):
    """Require a shared identifier and reject conflicting numbers of the same kind."""
    left, right = _identifiers(left), _identifiers(right)
    if not left & right:
        return False
    for kind in {kind for kind, _ in left} & {kind for kind, _ in right}:
        if {number for k, number in left if k == kind} != {number for k, number in right if k == kind}:
            return False
    return True


def _title(item):
    value = item.get('data', {})
    title = (value.get('title') or '').strip()
    course = (item.get('course_title') or '').strip()
    # Only remove a whole matching course prefix or its unambiguous acronym.
    if course and title.lower().startswith(course.lower() + ' '):
        title = title[len(course):].lstrip(' :–-·') or title
    else:
        first = re.split(r'[,\[(]', course, 1)[0].strip()
        if first and title.lower().startswith(first.lower() + ' '):
            title = title[len(first):].lstrip(' :–-·') or title
        elif len(first.split()) > 1:
            acronym = ''.join(w[0] for w in first.split() if w.lower() not in ('and', 'und', 'für', 'for'))
            if len(acronym) >= 2 and title.startswith(acronym + ' '):
                title = title[len(acronym):].lstrip(' :–-·') or title
        # The leading course word is safe if it is a whole, distinctive word.
        word = first.split(' ')[0] if first else ''
        if len(word) >= 8 and title.lower().startswith(word.lower() + ' '):
            title = title[len(word):].lstrip(' :–-·') or title
    if value.get('kind') == 'task' or (value.get('kind') == 'exam' and value.get('date_meaning') == 'window' and re.search(r'\bquiz\b', title, re.I)):
        title = re.sub(r'^AB\s*(\d+)\b', r'Arbeitsblatt \1', title, flags=re.I)
        title = re.sub(r'^Quiz\s*(?:[–-]\s*)?Einheit\s*(\d+)\b', r'Quiz zur Einheit \1', title, flags=re.I)
        if re.match(r'^(?:Quiz|Arbeitsblatt|Aufgabenblatt|Hausübung|HÜ|Assignment|Task|Programmieraufgabe|AB\s*\d)\b', title, re.I) and not _ACTION.search(title):
            title += ' bearbeiten'
    return title


def _summary(item, title, *, task=True, linked_deadline=False):
    description = (item.get('data', {}).get('description') or '').strip()
    # Remove known provenance scaffolding while retaining the user's instructions.
    description = re.sub(
        r'Abgeleitet: Regel #\d+ \(Vortag um (\d{2}:\d{2})\) \+ eigene Einheit #\d+ am (\d{4}-\d{2}-\d{2})\. Die Quelle nennt die Regel, nicht dieses errechnete Datum\.',
        r'Die Frist ist aus der Regel „Vortag um \1“ und dem eigenen Termin am \2 errechnet.',
        description, flags=re.I)
    description = re.sub(r'Freiwillige Vorbereitungstasks:\s*https?://[^\s;]+\s+und Dokument\s+\d+\s+Seite\s+\d+\.',
                         'Die Vorbereitungstasks sind freiwillig.', description, flags=re.I)
    # The effective linked item supplies its deadline separately; the quoted date
    # here can be stale and must never be presented as the current deadline.
    if linked_deadline:
        description = re.sub(r'Abgabe\s+laut\s+https?://(?:127\.0\.0\.1|localhost)(?::\d+)?/items/\d+:\s*\d{4}-\d{2}-\d{2}\s+um\s+\d{2}:\d{2}\s+Europe/Vienna\.\s*Originalfrist bleibt bestehen\.', '', description, flags=re.I)
    if task and re.search(r'^Persönlich vorgeschlagener Start mit einer Woche Puffer\.', description, re.I):
        description = re.sub(r'^Persönlich vorgeschlagener Start mit einer Woche Puffer\.', f'{title}. Geplant ist ein Start mit einer Woche Puffer.', description, flags=re.I)
    # Links can be submission instructions. Only remove explicitly marked provenance.
    description = re.sub(r'Originaleintrag\s+https?://[^\s;]+', '', description, flags=re.I)
    description = re.sub(r'\s*\(https?://(?:127\.0\.0\.1|localhost)(?::\d+)?/items/\d+\)', '', description, flags=re.I)
    description = re.sub(r'\blaut\s+https?://(?:127\.0\.0\.1|localhost)(?::\d+)?/documents/\d+\?page=\d+', 'laut Quelle', description, flags=re.I)
    description = re.sub(r'\bOriginalabgabe:\s*\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?\s*;?', '', description, flags=re.I)
    description = re.sub(r'\s*\(eigener Eintrag \d+\)', '', description, flags=re.I)
    sentences = re.split(r'(?<=[.!?])\s+|\n+', description)
    technical = re.compile(r'^(?:Persönlich(?:er| vorgeschlagener) (?:Vorbereitungs|Bearbeitungs)tag|Keine Behauptung einer bisherigen Teilnahme|Fenster schließt am \d{4}-\d{2}-\d{2})', re.I)
    own_ids = _identifiers(title)
    kept = []
    for sentence in sentences:
        sentence = sentence.strip(' ;')
        if not sentence or (task and technical.match(sentence)):
            continue
        # A note may include a separate fact about another worksheet series.
        other = _identifiers(sentence)
        if task and re.match(r'^Für\s+AB\s*\d+\s*[-–]\s*\d+\b', sentence, re.I) and own_ids and not own_ids & other:
            continue
        kept.append(sentence)
    # Retain every remaining instruction; provenance cleanup must not drop later steps.
    summary = ' '.join(kept).strip()
    if summary:
        return summary
    if not task:
        return ''
    match = re.search(r'\bQuiz\b.*?\bEinheit\s*(\d+)\b', title, re.I)
    if match:
        return f'Beantworte die Fragen zu Einheit {match.group(1)}.'
    if re.search(r'\bQuiz\b', title, re.I):
        return 'Beantworte die Fragen im Quiz.'
    return title


def _closing(item):
    data = item.get('data', {})
    if item.get('date_conflict', item.get('conflict', False)) or data.get('source_state') in ('cancelled', 'unclear'):
        return None, None
    meaning = data.get('date_meaning')
    if meaning == 'window':
        return data.get('end_date'), data.get('end_time') if data.get('end_date') else None
    if meaning == 'deadline':
        if data.get('end_date'):
            return data['end_date'], data.get('end_time')
        return data.get('date'), data.get('time') if data.get('date') else None
    return None, None


def _availability(source, now):
    """Availability is unknown if either boundary or its time is uncertain."""
    if not source:
        return None, None, None
    data = source['data']
    if source.get('date_conflict', source.get('conflict', False)) or data.get('source_state') in ('cancelled', 'unclear'):
        return None, None, None
    opening, opening_time = data.get('date'), data.get('time')
    closing, closing_time = _closing(source)
    if not (opening and opening_time and closing and closing_time):
        return opening, opening_time, None
    try:
        zone = ZoneInfo('Europe/Vienna')
        start = datetime.combine(date.fromisoformat(str(opening)), time.fromisoformat(str(opening_time)), zone)
        end = datetime.combine(date.fromisoformat(str(closing)), time.fromisoformat(str(closing_time)), zone)
        current = now or datetime.now(zone)
        if current.tzinfo is None:
            current = current.replace(tzinfo=zone)
        return opening, opening_time, start <= current.astimezone(zone) <= end
    except (ValueError, TypeError):
        return opening, opening_time, None


def task_display(item, items=(), now=None, *, _by_id=None):
    """Return title, summary, closing metadata, source id and personal date."""
    data = item.get('data', {})
    title = _title(item)
    date, clock = _closing(item)
    source_id = item.get('id') if date else None
    planned = data.get('date') if data.get('date_meaning') == 'start' and item.get('identity', '').startswith('note:') else None
    quiz_source = item if (data.get('kind') == 'exam' and data.get('date_meaning') == 'window' and re.search(r'\bquiz\b', data.get('title', ''), re.I)) else None
    if not date and planned and item.get('course_id') is not None:
        urls = _LINK.findall(data.get('description') or '')
        ids = set()
        for url in urls:
            parsed = urlsplit(url.rstrip('.:'))
            match = re.fullmatch(r'/items/(\d+)', parsed.path)
            if parsed.hostname in ('127.0.0.1', 'localhost') and not parsed.query and not parsed.fragment and match:
                ids.add(int(match.group(1)))
        if len(ids) == 1:
            source = (_by_id.get(next(iter(ids))) if _by_id is not None
                      else next((candidate for candidate in items if candidate.get('id') in ids), None))
            if (source and in_course_period(source) and source.get('id') != item.get('id') and source.get('course_id') == item.get('course_id')
                    and not source.get('identity', '').startswith('note:') and (source.get('data', {}).get('kind') == 'task'
                    or (source.get('data', {}).get('kind') == 'exam' and source.get('data', {}).get('date_meaning') == 'window'
                        and re.search(r'\bquiz\b', source.get('data', {}).get('title', ''), re.I)))
                    and _matching_identifiers(title, source.get('data', {}).get('title', ''))):
                date, clock = _closing(source)
                if source.get('data', {}).get('kind') == 'exam':
                    quiz_source = source
                if date:
                    source_id = source['id']
    is_quiz = (data.get('kind') == 'exam' and data.get('date_meaning') == 'window' and re.search(r'\bquiz\b', data.get('title', ''), re.I))
    # The first matching exam matters even if another row shares its ID.
    linked_quiz = source_id and (_by_id.get(('exam', source_id)) if _by_id is not None
                                 else next((i for i in items if i.get('id') == source_id and i.get('data', {}).get('kind') == 'exam'), None))
    label = ('Quiz schließt' if is_quiz or linked_quiz else 'Fällig') if date else None
    opening, opening_time, available = _availability(quiz_source, now)
    return {'title': title, 'summary': _summary(item, title, linked_deadline=bool(source_id and source_id != item.get('id'))), 'available_from': opening,
            'available_time': opening_time, 'available_now': available, 'deadline_date': date,
            'deadline_time': clock, 'deadline_label': label,
            'deadline_source_id': source_id, 'planned_date': planned}


def attach_task_displays(items, now=None):
    """Attach text to all rows; attach deadline metadata only to tasks and quizzes."""
    # Local to this batch: preserve first-match semantics for duplicate IDs and
    # never retain rows between requests.
    by_id = {}
    for item in items:
        identifier = item.get('id')
        by_id.setdefault(identifier, item)
        if item.get('data', {}).get('kind') == 'exam':
            by_id.setdefault(('exam', identifier), item)
    for item in items:
        data = item.get('data', {})
        if (data.get('kind') == 'task' or (data.get('kind') == 'exam' and data.get('date_meaning') == 'window'
                and re.search(r'\bquiz\b', data.get('title', ''), re.I))):
            item['task_display'] = task_display(item, items, now=now, _by_id=by_id)
            item['item_display'] = {key: item['task_display'][key] for key in ('title', 'summary')}
        else:
            title = _title(item)
            item['item_display'] = {'title': title, 'summary': _summary(item, title, task=False)}
    return items
