"""Bounded, typed domain values shared by personal actions and source admission."""
import re
from datetime import date, datetime, time

TEXT_BOUNDS={'title':240,'body':10_000,'description':5000,'uncertainty':1500,'quote':1500,'anchor':240,'identity_key':200,'related_key':200,'semester':30,'group':100}
ENUMS={'kind':{'note','task','event','exam','registration','notice'},'date_meaning':{'deadline','start','window','recommended','none','unclear'},'obligation':{'required','recommended','optional','unclear'},'source_state':{'active','cancelled','unclear'},'identity_scope':{'course','document'}}
FIELDS=set(TEXT_BOUNDS)|set(ENUMS)|{'date','time','end_date','end_time','due_at','pinned','ignored','page','course_id','is_update'}


def validate_values(value):
    if not isinstance(value,dict) or set(value)-FIELDS:raise ValueError('Unknown domain fields')
    for key,bound in TEXT_BOUNDS.items():
        if key in value and value[key] is not None and (not isinstance(value[key],str) or len(value[key])>bound):raise ValueError('Invalid bounded text: '+key)
    for key,options in ENUMS.items():
        if key in value and value[key] not in options:raise ValueError('Invalid domain value: '+key)
    for key in ('date','end_date'):
        if value.get(key) is not None:
            if not isinstance(value[key],str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value[key]):raise ValueError('Date requires ISO YYYY-MM-DD')
            date.fromisoformat(value[key])
    for key in ('time','end_time'):
        if value.get(key) is not None:
            if not isinstance(value[key],str) or not re.fullmatch(r'\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?',value[key]):raise ValueError('Time requires a precise local clock')
            time.fromisoformat(value[key])
    if value.get('due_at') is not None:
        if not isinstance(value['due_at'],str) or 'T' not in value['due_at']:raise ValueError('Due instant requires ISO datetime')
        datetime.fromisoformat(value['due_at'])
    for key in ('pinned','ignored','is_update'):
        if key in value and not isinstance(value[key],bool):raise ValueError('Boolean field required: '+key)
    for key in ('page','course_id'):
        if key in value and value[key] is not None and (type(value[key]) is not int or value[key]<1):raise ValueError('Positive integer required: '+key)
    if value.get('end_date') and value.get('date') and value['end_date']<value['date']:raise ValueError('End date precedes start')
    if value.get('end_date')==value.get('date') and value.get('time') and value.get('end_time') and value['end_time']<value['time']:raise ValueError('End time precedes start')
    return value
