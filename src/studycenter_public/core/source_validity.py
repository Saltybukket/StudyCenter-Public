"""Conservative checks for dates that contradict their quoted source evidence."""

import re
import json
from datetime import date


_DATES = re.compile(
    r'(?<!\d)(?:(?P<iso_y>20\d{2})-(?P<iso_m>\d{1,2})-(?P<iso_d>\d{1,2})'
    r'|(?P<d>\d{1,2})\s*[./]\s*(?P<m>\d{1,2})\s*[./]\s*(?P<y>20\d{2}))(?!\d)'
)
_SEMESTER = re.compile(r'(?i)\b(WS|SS|Wintersemester|Sommersemester)\s*(20\d{2})(?:\s*/\s*(\d{2}|20\d{2}))?\b')
_MONTHS = {name: number for number, names in enumerate((
    'januar january jänner', 'februar february', 'märz maerz march', 'april', 'mai may',
    'juni june', 'juli july', 'august', 'september', 'oktober october', 'november',
    'dezember december'), 1) for name in names.split()}
_MONTH_PATTERN = '|'.join(_MONTHS)
_NAMED_DATES = (
    re.compile(r'(?<!\d)(?P<d>\d{1,2})\.?\s+(?P<m>' + _MONTH_PATTERN + r')\s+(?P<y>20\d{2})(?!\d)', re.I),
    re.compile(r'\b(?P<m>' + _MONTH_PATTERN + r')\s+(?P<d>\d{1,2}),?\s+(?P<y>20\d{2})(?!\d)', re.I),
)


def in_course_period(item):
    """Whether a source claim belongs in its course's active period."""
    semester = item.get('course_semester') or ''
    value = item.get('data', {})
    if semester and value.get('semester') and semester != value['semester']:
        return False
    if not re.fullmatch(r'\d{4}[WS]', semester):
        return True
    overrides = item.get('overrides') or {}
    if isinstance(overrides, str):
        overrides = json.loads(overrides)
    if {'date', 'end_date'} & overrides.keys():
        return True
    closing = value.get('end_date') or value.get('date')
    lower_bound = semester[:4] + ('-08-01' if semester.endswith('W') else '-02-01')
    return not closing or str(closing) >= lower_bound


def explicit_dates(text):
    result = set()
    for match in _DATES.finditer(text):
        parts = match.groupdict()
        try:
            result.add(date(int(parts['iso_y'] or parts['y']),
                            int(parts['iso_m'] or parts['m']),
                            int(parts['iso_d'] or parts['d'])))
        except ValueError:
            continue
    for pattern in _NAMED_DATES:
        for match in pattern.finditer(text):
            try:
                result.add(date(int(match['y']), _MONTHS[match['m'].casefold()], int(match['d'])))
            except ValueError:
                continue
    return result


def validate_source_dates(candidate):
    """Reject a changed year for the same quoted day, without guessing other dates.

    Quotes can include several dates (including corrections); an exact match is
    sufficient. Other ambiguities require the model and remain visible as such.
    """
    quoted = explicit_dates(candidate.quote)
    for claimed in (candidate.date, candidate.end_date):
        if not claimed or claimed in quoted:
            continue
        same_day = {d for d in quoted if (d.month, d.day) == (claimed.month, claimed.day)}
        if same_day:
            originals = ', '.join(sorted(d.isoformat() for d in same_day))
            raise ValueError(f'Datumsjahr widerspricht Fundstelle: {claimed.isoformat()} statt {originals}')


def historical_semester_candidate(candidate, context):
    """Isolate a claim explicitly labelled with a different semester in its quote."""
    current = context.get('semester') or ''
    if not re.fullmatch(r'20\d{2}[WS]', current):
        return candidate
    semesters = set()
    for match in _SEMESTER.finditer(candidate.quote):
        # "Seit WS 2025" describes a possible continuing rule, not a bounded
        # historical semester. Its present applicability needs semantic review.
        prefix = candidate.quote[max(0, match.start()-24):match.start()]
        if re.search(r'(?i)\b(?:seit|ab|since|from)\s+(?:(?:dem|the)\s+)?$', prefix):
            return candidate
        kind, year, suffix = match.groups()
        winter = kind.casefold() in ('ws', 'wintersemester')
        if suffix and (not winter or int(suffix) % 100 != (int(year) + 1) % 100):
            continue
        semesters.add(year + ('W' if winter else 'S'))
    if len(semesters) != 1:
        return candidate
    old = next(iter(semesters))
    if (int(old[:4]), old[-1] == 'W') >= (int(current[:4]), current[-1] == 'W'):
        return candidate
    note = f'Fundstelle bezeichnet ausdrücklich {old}; Gültigkeit für {current} ist nicht belegt.'
    uncertainty = (candidate.uncertainty or '').strip()
    if note not in uncertainty:
        uncertainty = (uncertainty + ' ' + note).strip()
    return candidate.model_copy(update={'semester': old, 'identity_scope': 'document', 'identity_key': None,
        'source_state': 'unclear', 'obligation': 'unclear', 'is_update': False,
        'uncertainty': uncertainty})
