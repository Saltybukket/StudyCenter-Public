"""Conservative admission checks for source dates and historical semesters."""
import re
from datetime import date

_DATES = re.compile(r'(?<!\d)(?:(?P<iy>20\d{2})-(?P<im>\d{1,2})-(?P<id>\d{1,2})|(?P<d>\d{1,2})\s*[./]\s*(?P<m>\d{1,2})\s*[./]\s*(?P<y>20\d{2}))(?!\d)')
_MONTHS = {name: number for number, names in enumerate((
    'januar january jänner', 'februar february', 'märz maerz march', 'april', 'mai may',
    'juni june', 'juli july', 'august', 'september', 'oktober october', 'november',
    'dezember december'), 1) for name in names.split()}
_MONTH_PATTERN = '|'.join(_MONTHS)
_NAMED = (re.compile(r'(?<!\d)(?P<d>\d{1,2})\.?\s+(?P<m>'+_MONTH_PATTERN+r')\s+(?P<y>20\d{2})(?!\d)', re.I),
          re.compile(r'\b(?P<m>'+_MONTH_PATTERN+r')\s+(?P<d>\d{1,2}),?\s+(?P<y>20\d{2})(?!\d)', re.I))
_SEMESTER = re.compile(r'(?i)\b(WS|SS|Wintersemester|Sommersemester)\s*(20\d{2})(?:\s*/\s*(\d{2}|20\d{2}))?\b')


def explicit_course_semester(title):
    """Use only a single explicit semester label in a discovered course title."""
    semesters = set(re.findall(r'\b(20\d{2}[WS])\b', title or ''))
    for match in _SEMESTER.finditer(title or ''):
        kind, year, suffix = match.groups()
        winter = kind.casefold() in ('ws', 'wintersemester')
        if suffix and (not winter or int(suffix) % 100 != (int(year)+1) % 100):
            continue
        semesters.add(year+('W' if winter else 'S'))
    return next(iter(semesters)) if len(semesters) == 1 else None


def explicit_dates(text):
    result = set()
    for match in _DATES.finditer(text):
        p = match.groupdict()
        try:
            result.add(date(int(p['iy'] or p['y']), int(p['im'] or p['m']), int(p['id'] or p['d'])))
        except ValueError:
            pass
    for pattern in _NAMED:
        for match in pattern.finditer(text):
            try:
                result.add(date(int(match['y']), _MONTHS[match['m'].casefold()], int(match['d'])))
            except ValueError:
                pass
    return result


def validate_source_dates(value):
    """Reject an invented year when the quote contains the same day and month."""
    quoted = explicit_dates(value.get('quote', ''))
    for key in ('date', 'end_date'):
        if not value.get(key):
            continue
        claimed = date.fromisoformat(str(value[key]))
        if claimed in quoted:
            continue
        originals = {d for d in quoted if (d.month, d.day) == (claimed.month, claimed.day)}
        if originals:
            raise ValueError('Datumsjahr widerspricht Fundstelle: '+claimed.isoformat()+' statt '+', '.join(sorted(d.isoformat() for d in originals)))


def historical_semester_candidate(value, current):
    """Keep an explicitly older claim separate from the trusted current course."""
    if not isinstance(current, str) or not re.fullmatch(r'20\d{2}[WS]', current):
        return value
    semesters = set()
    quote = value.get('quote', '')
    for match in _SEMESTER.finditer(quote):
        if re.search(r'(?i)\b(?:seit|ab|since|from)\s+(?:(?:dem|the)\s+)?$', quote[max(0, match.start()-24):match.start()]):
            return value
        kind, year, suffix = match.groups()
        winter = kind.casefold() in ('ws', 'wintersemester')
        if suffix and (not winter or int(suffix) % 100 != (int(year)+1) % 100):
            continue
        semesters.add(year+('W' if winter else 'S'))
    if len(semesters) != 1:
        return value
    old = next(iter(semesters))
    if (int(old[:4]), old[-1] == 'W') >= (int(current[:4]), current[-1] == 'W'):
        return value
    note = f'Fundstelle bezeichnet ausdrücklich {old}; Gültigkeit für {current} ist nicht belegt.'
    uncertainty = (value.get('uncertainty') or '').strip()
    if note not in uncertainty:
        uncertainty = (uncertainty+' '+note).strip()
    return value | {'semester':old, 'identity_scope':'document', 'identity_key':None,
                    'source_state':'unclear', 'obligation':'unclear', 'is_update':False,
                    'uncertainty':uncertainty}
