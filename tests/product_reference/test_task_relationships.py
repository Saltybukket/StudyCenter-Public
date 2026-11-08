from copy import deepcopy

from studycenter_public.core.task_relationships import attach_task_relationships


def row(id, title, *, own=False, course=1, description='', status='open'):
    return {'id': id, 'course_id': course, 'identity': ('note:' if own else 'source:') + str(id),
            'status': status, 'overrides': '{"title":"personal title"}' if own else '{}',
            'data': {'title': title, 'description': description, 'kind': 'task', 'source_state': 'active'}}


def test_explicit_links_are_bidirectional_without_mutating_personal_work():
    source = row(1, 'Arbeitsblatt 2 einreichen')
    task = row(2, 'AB2 vorbereiten', own=True, status='done', description='Originaleintrag http://localhost:8767/items/1; Meine eigene Methode behalten.')
    before = deepcopy([source, task])
    attach_task_relationships([source, task])
    assert task['task_origin']['id'] == 1
    assert source['personal_tasks'] == [{'id': 2, 'title': 'AB2 vorbereiten', 'status': 'done'}]
    for value, old in zip([source, task], before):
        assert {k:v for k,v in value.items() if k not in ('task_origin','personal_tasks')} == old
    # Source state changes do not erase an explicit relationship or complete it.
    source['date_conflict'] = True
    attach_task_relationships([source, task])
    assert source['status'] == 'open' and task['status'] == 'done' and task['task_origin']['id'] == 1


def test_similar_titles_and_ambiguous_or_external_links_never_group_tasks():
    source = row(1, 'Arbeitsblatt 2 einreichen')
    for description, course, title in [
        ('',1,'AB2 vorbereiten'),
        ('Originaleintrag https://external.example/items/1',1,'AB2 vorbereiten'),
        ('Originaleintrag http://localhost/items/1',2,'AB2 vorbereiten'),
        ('Originaleintrag http://localhost/items/1',1,'AB3 vorbereiten'),
        ('Originaleintrag http://localhost/items/1; Originaleintrag http://localhost/items/4',1,'AB2 vorbereiten'),
    ]:
        task = row(2, title, own=True, course=course, description=description)
        attach_task_relationships([source, task])
        assert not task['task_origin'] and not source['personal_tasks']


def test_ignored_items_are_not_promoted_through_relationships():
    source = row(1, 'Arbeitsblatt 2 einreichen')
    task = row(2, 'AB2 vorbereiten', own=True, description='Originaleintrag http://localhost/items/1')
    task['ignored'] = 1
    attach_task_relationships([source, task])
    assert not source['personal_tasks']
    task['ignored'] = 0
    source['ignored'] = 1
    attach_task_relationships([source, task])
    assert not task['task_origin']


def test_malformed_original_links_do_not_break_task_views_or_invent_deadlines():
    from studycenter_public.core.task_presentation import attach_task_displays

    source = row(1, 'Arbeitsblatt 2 einreichen')
    source['data'].update(date='2035-11-10', time='23:59', date_meaning='deadline')
    for url in ('http://[localhost/items/1', 'http://localhost:broken/items/1',
                'http://localhost:99999/items/1', 'http://user@localhost/items/1'):
        task = row(2, 'AB2 vorbereiten', own=True, description=f'Originaleintrag {url}')
        task['data'].update(date='2035-11-09', date_meaning='start')
        before = deepcopy(task['data'])
        attach_task_displays([source, task])
        attach_task_relationships([source, task])
        assert task['task_display']['deadline_source_id'] is None
        assert task['task_origin'] is None and task['data'] == before
    task['data']['description'] = 'Originaleintrag http://localhost:8767/items/1'
    attach_task_displays([source, task])
    attach_task_relationships([source, task])
    assert task['task_display']['deadline_source_id'] == 1
    assert task['task_origin']['id'] == 1
