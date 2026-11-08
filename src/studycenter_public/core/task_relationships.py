"""Read-only links between explicitly referenced personal work and its original."""

from .source_validity import in_course_period
from .task_presentation import original_item_ids, _matching_identifiers


def attach_task_relationships(items):
    by_id = {item['id']: item for item in items}
    for item in items:
        item['task_origin'] = None
        item['personal_tasks'] = []
    for item in items:
        data = item.get('data', {})
        if not item.get('identity', '').startswith('note:') or data.get('kind') != 'task' or item.get('ignored'):
            continue
        ids = original_item_ids(data.get('description'))
        if len(ids) != 1:
            continue
        source = by_id.get(next(iter(ids)))
        if (not source or source['id'] == item['id'] or source.get('ignored')
                or source.get('identity', '').startswith('note:')
                or item.get('course_id') is None or item.get('course_id') != source.get('course_id')
                or not in_course_period(source) or not in_course_period(item)
                or source.get('data', {}).get('kind') not in ('task', 'exam')
                or not _matching_identifiers(data.get('title', ''), source['data'].get('title', ''))):
            continue
        def summary(row):
            return {'id': row['id'], 'title': row.get('item_display', {}).get('title') or row['data']['title'],
                    'status': row.get('status', 'open')}
        item['task_origin'] = summary(source)
        source['personal_tasks'].append(summary(item))
    return items
