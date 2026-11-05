"""RFC 5545 export of known dates and local times without inventing duration."""
from datetime import date, datetime, time, timedelta, timezone

from ..config import VIENNA


def _escape(value):
    return str(value).replace('\\', '\\\\').replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def _offset(value):
    minutes = int(value.total_seconds() / 60)
    return ('+' if minutes >= 0 else '-') + f'{abs(minutes)//60:02}{abs(minutes)%60:02}'


def _timezone_lines(years):
    lines = ['BEGIN:VTIMEZONE', 'TZID:Europe/Vienna']
    # Only selected years are scanned: far-apart or erroneous dates must not
    # turn an export into a multi-million-day loop.
    for year in sorted(set(years)):
        current = datetime(year, 1, 1, tzinfo=timezone.utc)
        finish = datetime(year, 12, 30, tzinfo=timezone.utc)
        initial = current.astimezone(VIENNA)
        lines += ['BEGIN:STANDARD', 'DTSTART:'+initial.strftime('%Y%m%dT%H%M%S'),
                  'TZOFFSETFROM:'+_offset(initial.utcoffset()), 'TZOFFSETTO:'+_offset(initial.utcoffset()), 'END:STANDARD']
        while current < finish:
            following = current+timedelta(days=1)
            before, after = current.astimezone(VIENNA), following.astimezone(VIENNA)
            if before.utcoffset() != after.utcoffset():
                low, high = current, following
                while (high-low).total_seconds() > 1:
                    middle = low+timedelta(seconds=int((high-low).total_seconds()//2))
                    if middle.astimezone(VIENNA).utcoffset() == before.utcoffset(): low = middle
                    else: high = middle
                kind = 'DAYLIGHT' if after.dst() else 'STANDARD'
                onset = high.replace(tzinfo=None)+before.utcoffset()
                lines += ['BEGIN:'+kind, 'DTSTART:'+onset.strftime('%Y%m%dT%H%M%S'),
                          'TZOFFSETFROM:'+_offset(before.utcoffset()), 'TZOFFSETTO:'+_offset(after.utcoffset()), 'END:'+kind]
            current = following
    return lines+['END:VTIMEZONE']


def export_calendar(items, instance_id):
    events = []
    for item in items:
        if item.get('date_conflict') or item.get('linked_deadline_unavailable') or item.get('source_state') in ('cancelled', 'unclear'):
            continue
        meaning = item.get('date_meaning')
        linked=item.get('linked_deadline')
        closing = bool(linked) or (meaning in ('deadline', 'window') and item.get('kind') != 'registration')
        day = linked['date'] if linked else (item.get('end_date') or (item.get('date') if meaning == 'deadline' else None)) if closing else item.get('date')
        clock = linked.get('time') if linked else (item.get('end_time') if item.get('end_date') else item.get('time')) if closing else item.get('time')
        if not day:
            continue
        first = date.fromisoformat(day)
        event = {'item':item, 'date':first, 'time':clock}
        if not closing and item.get('end_date'):
            event['end_date'] = date.fromisoformat(item['end_date'])
            event['end_time'] = item.get('end_time')
        elif not closing and clock and item.get('end_time') and time.fromisoformat(item['end_time']) > time.fromisoformat(clock):
            # A later end clock with no end date denotes the same day; an
            # earlier or equal clock does not establish a next-day end.
            event['end_date'] = first
            event['end_time'] = item['end_time']
        events.append(event)
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//StudyCenter Public//Personal planning//DE', 'CALSCALE:GREGORIAN']
    timed = [e for e in events if e['time']]
    if timed:
        years = [e[key].year for e in timed for key in ('date', 'end_date') if key in e]
        lines += _timezone_lines(years)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    for event in events:
        item = event['item']
        lines += ['BEGIN:VEVENT', f"UID:{instance_id}-{item['id']}@studycenter-public", 'DTSTAMP:'+stamp,
                  'CLASS:PRIVATE', 'SUMMARY:'+_escape(item['title'])]
        if event['time']:
            start = datetime.fromisoformat(event['date'].isoformat()+'T'+event['time'])
            lines.append('DTSTART;TZID=Europe/Vienna:'+start.strftime('%Y%m%dT%H%M%S'))
            if event.get('end_date') and event.get('end_time'):
                end = datetime.fromisoformat(event['end_date'].isoformat()+'T'+event['end_time'])
                lines.append('DTEND;TZID=Europe/Vienna:'+end.strftime('%Y%m%dT%H%M%S'))
        else:
            lines.append('DTSTART;VALUE=DATE:'+event['date'].strftime('%Y%m%d'))
            if event.get('end_date') and event['end_date'] < date.max and not event.get('end_time'):
                lines.append('DTEND;VALUE=DATE:'+(event['end_date']+timedelta(days=1)).strftime('%Y%m%d'))
        lines += ['DESCRIPTION:Persönliche Planung. Originalquelle in StudyCenter prüfen.', 'END:VEVENT']
    lines.append('END:VCALENDAR')
    folded = []
    for line in lines:
        part = ''
        for char in line:
            if len((part+char).encode('utf-8')) > 75:
                folded.append(part)
                part = ' '
            part += char
        folded.append(part)
    return '\r\n'.join(folded)+'\r\n'
