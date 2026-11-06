"""Shared action timing and personal state projections for stored entries."""
import math
import re
from datetime import date, datetime, time

from .config import VIENNA
from .presentation import personal_state

KINDS = {"task": "Aufgabe / Abgabe", "exam": "Prüfung", "event": "Veranstaltung", "registration": "Anmeldefenster", "notice": "Hinweis"}
COURSE_COLORS = ('#247b79', '#3c72b4', '#7954b2', '#ba4b80', '#288599', '#4c72bf', '#96559f', '#2f8b68')


def course_color(course_id):
    return COURSE_COLORS[int(course_id or 0) % len(COURSE_COLORS)]


def item_timing(value):
    """Display the closing date separately from the original, retained work window."""
    closing = (value['kind'] == 'task' and value.get('date_meaning') in ('deadline', 'window')
               or value['kind'] in ('registration', 'exam') and value.get('date_meaning') == 'window')
    if closing and value.get('end_date'):
        return value['end_date'], value.get('end_time'), True
    # A single registration date may be an opening, not a deadline.
    if value['kind'] == 'registration':
        context = ' '.join(value.get(k) or '' for k in ('quote', 'anchor'))
        closing = bool(re.search(r'schließt|schliesst|schliess|schließzeitpunkt|(?:an|ab)meldeschluss|closes|ends|until|deadline|frist|endet|bis zum|spätestens|\bmust\b.{0,60}\bbefore\b', context, re.I)) and not bool(re.search(r'öffnet|oeffnet|opens|opening',context,re.I))
    if value.get('date_meaning') == 'window' and not value.get('end_date') and value['kind'] != 'registration':
        closing = False
    return value.get('date'), value.get('time'), closing


def action_state(item, local_now):
    value = item['data']
    when, clock, closing = item_timing(value)
    today = local_now.date() if isinstance(local_now, datetime) else local_now
    remaining = (date.fromisoformat(when) - today).days if when else None
    if clock and isinstance(local_now, datetime):
        due = datetime.combine(date.fromisoformat(when), time.fromisoformat(clock), tzinfo=VIENNA)
        remaining = (due.timestamp()-local_now.timestamp())/86400
    # Never infer a personal booking from enrollment, a group name or a public offering.
    choice = personal_state(item)
    personal = choice['personal']
    mismatched_group = bool(value.get('group') and item.get('course_group') and value['group'] != item['course_group'])
    active = not item['ignored'] and value.get('source_state') == 'active' and not mismatched_group and not item.get('calendar_duplicate_of')
    required = value.get('obligation') == 'required'
    relevant = active and (required or personal or item.get('pinned') or item.get('identity','').startswith('note:'))
    actionable = relevant and item['status'] != 'done'
    offered = choice['offered']
    appointment = active and value['kind'] in ('event','exam') and value.get('date_meaning') == 'start' and not offered and (required or personal)
    planned_task = active and value['kind']=='task' and value.get('date_meaning')=='start' and item.get('identity','').startswith('note:')
    # A conflicted date is still shown for comparison, but is not a reliable
    # deadline for urgency, lists or an assistant answer.
    reliable_date = not item.get('date_conflict', item.get('conflict', False))
    deadline = closing and value['kind'] in ('task','registration','exam') and reliable_date
    urgent = (actionable and reliable_date and remaining is not None and remaining < 2
              and (deadline or remaining >= 0 and (appointment or planned_task)))
    if remaining is None:
        relative = 'Datum offen'
    elif remaining < 0:
        if value['kind']=='registration':
            relative = 'Anmeldefrist abgelaufen'
        elif deadline:
            relative = 'Frist abgelaufen' if clock and when == today.isoformat() else f'Seit {abs((date.fromisoformat(when)-today).days)} Tagen überfällig'
        elif planned_task and when == today.isoformat():
            relative = 'Für heute geplant'
        else:
            relative = 'Vergangen'
    elif clock and remaining < 1:
        hours = remaining * 24
        relative = 'In weniger als 1 Stunde' if hours < 1 else f'In {math.ceil(hours)} Stunden'
    else:
        days = (date.fromisoformat(when)-today).days
        relative = 'Heute' if days == 0 else 'Morgen' if days == 1 else f'In {days} Tagen'
    label = KINDS[value['kind']]
    if planned_task:
        label = 'Geplante Aufgabe'
    if closing:
        label = {'registration':'Anmeldeschluss','task':'Abgabe'}.get(value['kind'],'Fensterschluss')
        if value['kind']=='registration':
            context=' '.join(value.get(k) or '' for k in ('title','anchor'))
            if re.search(r'abmeldung|abmeldeschluss',context,re.I):
                both=re.search(r'\banmeldung|\banmeldeschluss|\ban[-/]\s*abmeldung',context,re.I)
                label='An-/Abmeldeschluss' if both else 'Abmeldeschluss'
    elif value['kind']=='registration':
        quote = value.get('quote') or ''
        opening = re.search(r'öffnet|oeffnet|opens|opening',quote,re.I) and not re.search(r'schließt|schliesst|schliess|closes|ends|until|deadline|frist|endet|bis zum|spätestens',quote,re.I)
        label = 'Anmeldeöffnung' if opening else 'Anmeldung · Datum unklar'
    return {'action_date':when, 'action_time':clock, 'closing':closing, 'remaining':remaining,
            'relative':relative, 'actionable':actionable, 'appointment':appointment, 'planned_task':planned_task,
            'action_deadline':deadline, 'relevant':relevant, 'offered':offered, 'personal':personal,
            'urgent':urgent, 'course_color':course_color(item.get('course_id')),
            'action_label':label, 'booking_proven':choice['booking_proven'], 'personally_planned':choice['personally_planned']}


def pin_state(item, local_now):
    value = item['data']
    choice = personal_state(item)
    if isinstance(local_now, datetime):
        local_now = local_now.astimezone(VIENNA)
    today = local_now.date() if isinstance(local_now, datetime) else local_now
    actionable = item['status'] != 'done' and not item['ignored'] and value.get('source_state') == 'active'
    due_date, due_time, closing = item_timing(value)
    deadline = value['kind'] in ('task','registration','exam') and closing and not choice['offered']
    remaining = (date.fromisoformat(due_date) - today).days if due_date else None
    if due_time and isinstance(local_now, datetime):
        due = datetime.combine(date.fromisoformat(due_date),time.fromisoformat(due_time),tzinfo=VIENNA)
        remaining = (due.timestamp()-local_now.timestamp())/86400
    exam = actionable and value['kind'] == 'exam' and value.get('date_meaning')=='start' and (value.get('obligation') not in ('optional','recommended') or choice['personal']) and not choice['offered'] and remaining is not None and remaining >= 0 and not item.get('date_conflict',item['conflict'])
    reliable_date = not item.get('date_conflict', item.get('conflict', False))
    soon = actionable and deadline and reliable_date and remaining is not None and remaining < 4
    expired = actionable and deadline and reliable_date and remaining is not None and remaining < 0
    imminent = actionable and deadline and reliable_date and bool(due_time) and isinstance(local_now, datetime) and remaining is not None and 0 <= remaining <= .25
    urgent = actionable and reliable_date and remaining is not None and 0 <= remaining < 2 and (deadline or exam)
    color = 'orange' if expired else 'red' if imminent else 'yellow' if urgent else 'blue' if value['kind'] in ('exam','notice') else 'green'
    return {'auto_pin': exam or soon, 'urgent': urgent,
            'expired': expired, 'imminent': imminent,
            'pin_label': 'Frist abgelaufen' if expired else 'Schluss in höchstens 6 Stunden' if imminent else 'Fällig in weniger als 2 Tagen' if urgent and deadline else 'Termin in weniger als 2 Tagen' if urgent else '',
            'pin_reason': ('Persönlich geplante Prüfung' if choice['personally_planned'] and not choice['booking_proven'] else 'Fixierter Prüfungstermin') if exam else 'Frist abgelaufen' if expired else 'In weniger als 4 Tagen fällig' if soon else '',
            'note_color': color}


def available_later(item, local_now=None):
    local_now=local_now or datetime.now(VIENNA)
    display=item.get('task_display',{})
    day=display.get('available_from');clock=display.get('available_time')
    return bool(day and (day>local_now.date().isoformat() or day==local_now.date().isoformat() and clock and clock>local_now.strftime('%H:%M:%S')))


def _with_display_deadline(item, display):
    if item['data']['kind'] == 'exam':
        # A quiz remains an exam window: its start is availability and the
        # displayed deadline is the window's closing boundary.
        value=item['data'] | {'end_date':display['deadline_date'], 'end_time':display['deadline_time'],
                              'date_meaning':'window'}
    else:
        value=item['data'] | {'date':display['deadline_date'], 'time':display['deadline_time'],
                              'end_date':None, 'end_time':None, 'date_meaning':'deadline'}
    return item | {'data':value}


def view_action_state(item, local_now):
    display=item.get('task_display',{})
    if display.get('deadline_date'):
        state=action_state(_with_display_deadline(item,display),local_now)
        state['action_label']=display['deadline_label']
    else:
        state=action_state(item,local_now)
    state['available_later']=available_later(item,local_now)
    if state['available_later']:
        state['actionable']=False
        state['urgent']=False
    return state


def view_pin_state(item, local_now):
    display=item.get('task_display',{})
    if display.get('deadline_date'):
        return pin_state(_with_display_deadline(item,display),local_now)
    return pin_state(item,local_now)
