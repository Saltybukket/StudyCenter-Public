from copy import deepcopy
from studycenter_public.core.task_presentation import attach_task_displays, task_display
from studycenter_public.core.presentation import item_tags


def row(id, title, *, kind='task', course=1, identity='source', meaning='none', date=None, end=None, time=None, end_time=None, description='', conflict=False):
    return {'id': id, 'identity': identity, 'course_id': course, 'course_title': 'Messtechnik, Übung (WS)', 'date_conflict': conflict,
            'data': {'title': title, 'kind': kind, 'date_meaning': meaning, 'date': date, 'end_date': end,
                     'time': time, 'end_time': end_time, 'description': description, 'source_state': 'active'}}


def test_personal_task_has_source_closing_and_retains_instructions():
    note = row(921, 'Messtechnik AB1 vorbereiten und Lösungs-PDF prüfen', identity='note:voice:1', meaning='start', date='2026-10-21',
               description='Persönlich vorgeschlagener Vorbereitungstag, keine neue Hochschulfrist. Originalabgabe: 2026-10-27 23:59; Originaleintrag http://127.0.0.1:8767/items/643. Alle bearbeiteten Beispiele ankreuzen und eine lesbare Gesamt-PDF vorbereiten.')
    source = row(643, 'Arbeitsblatt 1 bearbeiten und einreichen', meaning='window', date='2026-10-12', end='2026-10-27', end_time='23:59:00')
    original = deepcopy(note['data'])
    result = attach_task_displays([note, source])[0]['task_display']
    assert result['title'] == 'Arbeitsblatt 1 vorbereiten und Lösungs-PDF prüfen'
    assert result['summary'] == 'Alle bearbeiteten Beispiele ankreuzen und eine lesbare Gesamt-PDF vorbereiten.'
    assert (result['planned_date'], result['deadline_date'], result['deadline_time'], result['deadline_source_id']) == ('2026-10-21', '2026-10-27', '23:59:00', 643)
    assert note['data'] == original


def test_batch_lookup_matches_direct_lookup_and_refreshes_between_calls():
    note = row(1, 'Quiz 4 vorbereiten', identity='note:1', meaning='start', date='2026-10-21',
               description='Originaleintrag http://localhost/items/2')
    source = row(2, 'Quiz 4 bearbeiten', kind='exam', meaning='window', date='2026-10-20',
                 end='2026-10-27', time='08:00', end_time='12:00')
    duplicate = row(2, 'Quiz 4 bearbeiten', kind='exam', meaning='window', date='2026-10-20',
                    end='2026-10-30', time='08:00', end_time='12:00')
    rows = [note, source, duplicate]
    expected = task_display(note, rows)
    attach_task_displays(rows)
    assert note['task_display'] == expected
    assert expected['deadline_date'] == '2026-10-27'
    source['data']['end_date'] = '2026-10-28'
    attach_task_displays(rows)
    assert note['task_display'] == task_display(note, rows)
    assert note['task_display']['deadline_date'] == '2026-10-28'


def test_closing_is_never_opening_and_conflicts_are_not_a_deadline():
    task = row(1, 'Arbeitsblatt 1 bearbeiten', meaning='window', date='2026-10-12', time='08:00')
    assert task_display(task)['deadline_date'] is None
    task['data'].update(end_date='2026-10-27', end_time='12:00')
    assert task_display(task)['deadline_date'] == '2026-10-27'
    task['date_conflict'] = True
    assert task_display(task)['deadline_date'] is None


def test_unrelated_or_ambiguous_or_conflicting_original_is_not_a_deadline():
    note = row(1, 'AB1 vorbereiten', identity='note:1', meaning='start', date='2026-10-21', description='Originaleintrag http://127.0.0.1:8767/items/2')
    source = row(2, 'Arbeitsblatt 2 bearbeiten', meaning='deadline', date='2026-11-10')
    assert task_display(note, [source])['deadline_date'] is None
    source['data']['title'] = 'Arbeitsblatt 1 bearbeiten'
    source['course_id'] = 2
    assert task_display(note, [source])['deadline_date'] is None
    source['course_id'] = 1
    source['date_conflict'] = True
    assert task_display(note, [source])['deadline_date'] is None
    source['date_conflict'] = False
    note['data']['description'] += '; Originaleintrag http://localhost:8767/items/3'
    assert task_display(note, [source])['deadline_date'] is None


def test_instruction_negation_and_optional_meaning_survive():
    task = row(1, 'Vorbereitung auf Lab 1', description='Die Aufgaben sind freiwillig und werden nicht bewertet. Nicht abgeben; unabhängig bearbeiten.')
    assert task_display(task)['summary'] == task['data']['description']
    quiz = row(2, 'Quiz – Einheit 4')
    assert task_display(quiz)['title'] == 'Quiz zur Einheit 4 bearbeiten'


def test_linked_quiz_window_closes_but_exam_start_does_not():
    note = row(930, 'Wahrscheinlichkeitstheorie Quiz Einheit 4 bearbeiten', identity='note:voice:930', meaning='start', date='2026-11-01', description='Persönlicher Bearbeitungstag innerhalb des gespeicherten Quizfensters. Originaleintrag http://127.0.0.1:8767/items/339; Fenster schließt am 2026-11-02 um 12:00 Europe/Vienna. Keine Behauptung einer bisherigen Teilnahme.')
    note['course_title'] = 'Wahrscheinlichkeitstheorie für Informatikstudien'
    quiz = row(339, 'Quiz – Einheit 4', kind='exam', meaning='window', date='2026-10-27', end='2026-11-02', end_time='12:00:00')
    display = task_display(note, [quiz])
    assert display['title'] == 'Quiz zur Einheit 4 bearbeiten'
    assert display['summary'] == 'Beantworte die Fragen zu Einheit 4.'
    assert (display['deadline_date'], display['deadline_time'], display['deadline_label'], display['deadline_source_id'], display['planned_date']) == ('2026-11-02', '12:00:00', 'Quiz schließt', 339, '2026-11-01')
    quiz['data']['date_meaning'] = 'start'
    assert task_display(note, [quiz])['deadline_date'] is None
    quiz['data'].update(date_meaning='window', title='Prüfung Einheit 4')
    assert task_display(note, [quiz])['deadline_date'] is None


def test_attach_quiz_window_but_not_regular_exam():
    quiz = row(339, 'Quiz – Einheit 4', kind='exam', meaning='window', date='2026-10-27', end='2026-11-02')
    exam = row(340, 'Prüfung Einheit 4', kind='exam', meaning='start', date='2026-11-02')
    attach_task_displays([quiz, exam])
    assert quiz['task_display']['title'] == 'Quiz zur Einheit 4 bearbeiten'
    assert quiz['task_display']['deadline_label'] == 'Quiz schließt'
    assert 'task_display' not in exam


def test_real_note_summaries_keep_actions_and_relevant_qualifications():
    con = row(936, 'CON Lab 1 - State Machines und SystemVerilog üben', identity='note:voice:936', description='Persönlicher Vorbereitungstag für das Lab am 30.10.2026. Freiwillige Vorbereitungstasks: http://127.0.0.1:8767/items/827 und Dokument 798 Seite 3. DEV-Container zuerst prüfen (eigener Eintrag 870). Konkreter Labslot/Raum bleibt unbestätigt.')
    con['course_title'] = 'Computer Organization and Networks'
    summary = task_display(con)['summary']
    assert 'freiwillig' in summary and 'DEV-Container zuerst prüfen' in summary
    assert 'Dokument 798' not in summary and 'eigener Eintrag 870' not in summary
    ab = row(921, 'Messtechnik AB1 vorbereiten', description='Alle bearbeiteten Beispiele ankreuzen und eine lesbare Gesamt-PDF vorbereiten. Für AB2-5 ist keine Abgabeuhrzeit belegt.')
    assert task_display(ab)['summary'] == 'Alle bearbeiteten Beispiele ankreuzen und eine lesbare Gesamt-PDF vorbereiten.'
    assert task_display(row(2, 'Arbeitsblatt 1'))['title'] == 'Arbeitsblatt 1 bearbeiten'
    assert task_display(row(3, 'Arbeitsblatt 1 nicht abgeben'))['title'] == 'Arbeitsblatt 1 nicht abgeben'


def test_quiz_availability_and_distinct_unit_number():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    note = row(930, 'Quiz 2 bearbeiten', identity='note:voice:930', meaning='start', date='2026-11-01', description='Originaleintrag http://127.0.0.1:8767/items/339')
    quiz = row(339, 'Quiz 2', kind='exam', meaning='window', date='2026-10-27', time='18:00:00', end='2026-11-02', end_time='12:00:00')
    zone = ZoneInfo('Europe/Vienna')
    before = task_display(note, [quiz], now=datetime(2026, 10, 27, 17, tzinfo=zone))
    assert (before['available_from'], before['available_time'], before['available_now']) == ('2026-10-27', '18:00:00', False)
    assert before['summary'] == 'Beantworte die Fragen im Quiz.'
    assert task_display(note, [quiz], now=datetime(2026, 10, 27, 19, tzinfo=zone))['available_now'] is True
    assert task_display(quiz, [quiz], now=datetime(2026, 11, 3, tzinfo=zone))['available_now'] is False
    quiz['data']['date'] = None
    assert task_display(note, [quiz], now=datetime(2026, 10, 27, 19, tzinfo=zone))['available_now'] is None
    future = row(342, 'Quiz – Einheit 6', kind='exam', meaning='window')
    assert task_display(future)['title'] == 'Quiz zur Einheit 6 bearbeiten'


def test_summary_keeps_later_submission_step():
    item=row(77,'Arbeitsblatt 3 bearbeiten',description='Beispiel eins lösen. Beispiel zwei rechnen. Die Gesamt-PDF hochladen. Nicht per E-Mail abgeben.')
    summary=task_display(item)['summary']
    assert 'Gesamt-PDF hochladen' in summary and 'Nicht per E-Mail abgeben' in summary


def test_linked_quiz_and_unit_numbers_are_distinct_identifiers():
    note = row(930, 'Quiz 4 bearbeiten', identity='note:voice:930', meaning='start', date='2026-11-01', description='Originaleintrag http://127.0.0.1:8767/items/339')
    quiz = row(339, 'Quiz – Einheit 4', kind='exam', meaning='window', end='2026-11-02')
    assert task_display(note, [quiz])['deadline_date'] is None
    note['data']['title'] = 'Quiz Einheit 4 bearbeiten'
    assert task_display(note, [quiz])['deadline_date'] == '2026-11-02'
    note['data']['title'] = 'Quiz 2 bearbeiten'
    quiz['data']['title'] = 'Quiz 2'
    assert task_display(note, [quiz])['deadline_date'] == '2026-11-02'
    note['data']['title'] = 'Quiz 3 Einheit 4 bearbeiten'
    quiz['data']['title'] = 'Quiz 2 – Einheit 4'
    assert task_display(note, [quiz])['deadline_date'] is None


def test_submission_url_is_not_removed_as_provenance():
    task = row(1, 'Arbeitsblatt 1', description='Lösung auf https://example.org/submit hochladen.')
    assert task_display(task)['summary'] == task['data']['description']


def test_derived_deadline_explanation_keeps_rule_and_personal_date():
    task = row(694, 'Übungsbeispiele im Teachcenter ankreuzen', description='Abgeleitet: Regel #440 (Vortag um 22:00) + eigene Einheit #741 am 2026-11-17. Die Quelle nennt die Regel, nicht dieses errechnete Datum.')
    assert task_display(task)['summary'] == 'Die Frist ist aus der Regel „Vortag um 22:00“ und dem eigenen Termin am 2026-11-17 errechnet.'


def test_all_kinds_have_text_without_making_events_into_deadlines():
    event = row(1, 'Messtechnik · Gruppe 5', kind='event', meaning='start', date='2026-11-13', description='Ort: Seminarraum EMS. Der persönliche Slot ist nicht bestätigt.')
    notice = row(2, 'Messtechnik · Arbeitsblatt 4', kind='notice', description='Ein Hinweis zur Veranstaltung.')
    exam = row(3, 'Messtechnik · Prüfung', kind='exam', meaning='start', date='2026-12-01')
    items = [event, notice, exam]
    attach_task_displays(items)
    assert event['item_display'] == {'title': 'Gruppe 5', 'summary': event['data']['description']}
    assert notice['item_display']['title'] == 'Arbeitsblatt 4'
    assert exam['item_display'] == {'title': 'Prüfung', 'summary': ''}
    assert all('task_display' not in item for item in items)


def test_non_task_description_keeps_personal_and_slot_qualifications():
    event = row(1, 'Termin', kind='event', description='Persönlicher Vorbereitungstag. Raum und Slot sind noch unbestätigt. Mehr unter https://example.org/room.')
    attach_task_displays([event])
    assert event['item_display']['summary'] == event['data']['description']


def test_assignment_closing_comes_from_matching_current_source():
    note = row(926, 'Assignment 1 bearbeiten und testen', identity='note:voice:926', meaning='start', date='2026-11-05', description='Persönlich vorgeschlagener Start mit einer Woche Puffer. Abgabe laut http://127.0.0.1:8767/items/355: 2026-11-12 um 08:00 Europe/Vienna. Originalfrist bleibt bestehen.')
    source = row(355, 'Assignment 1', meaning='deadline', date='2026-11-13', time='09:00')
    display = task_display(note, [source])
    assert (display['deadline_date'], display['deadline_time']) == ('2026-11-13', '09:00')
    assert display['summary'] == 'Assignment 1 bearbeiten und testen. Geplant ist ein Start mit einer Woche Puffer.'
    source['data']['title'] = 'Assignment 2'
    wrong = task_display(note, [source])
    assert wrong['deadline_date'] is None
    assert '2026-11-12' in wrong['summary']
    source['data']['title'] = 'Quiz 1'
    assert task_display(note, [source])['deadline_date'] is None


def test_conflict_note_retains_priority_and_times_without_local_source_urls():
    note = row(937, 'Vorlesungsbeginn nacharbeiten', description='Persönlicher Plan zur Terminkollision: EVE UE Gruppe A 08:45-10:45 (http://127.0.0.1:8767/items/785) hat Vorrang, da die Übungen laut http://127.0.0.1:8767/documents/362?page=5 verpflichtend sind. ES2 VO beginnt um 10:15.')
    result = task_display(note)['summary']
    assert 'Gruppe A 08:45-10:45 hat Vorrang' in result
    assert 'laut Quelle verpflichtend' in result and '10:15' in result
    assert 'http://' not in result


def test_personal_plan_does_not_promote_a_previous_semester_deadline():
    note = row(10, 'Assignment 1 bearbeiten', identity='note:plan', meaning='start', date='2026-11-01',
               description='Abgabe laut http://127.0.0.1:8767/items/20: 2025-11-12 um 08:00 Europe/Vienna.')
    source = row(20, 'Assignment 1', meaning='deadline', date='2025-11-12')
    source['course_semester'] = '2026W'
    source['data']['semester'] = '2026W'
    assert task_display(note, [source])['deadline_date'] is None
    source['data']['date'] = '2026-11-12'
    assert task_display(note, [source])['deadline_date'] == '2026-11-12'
    source['data']['semester'] = '2025W'
    assert task_display(note, [source])['deadline_date'] is None


def test_linked_deadline_and_planned_day_have_distinct_time_labels():
    note = row(1, 'AB2 vorbereiten', identity='note:1', meaning='start', date='2026-11-01',
               description='Originaleintrag http://localhost/items/2. Für AB2-5 ist keine Abgabeuhrzeit belegt.')
    source = row(2, 'Arbeitsblatt 2', meaning='deadline', date='2026-11-10', time='23:59')
    original = deepcopy(note['data'])
    attach_task_displays([note, source])
    tags = {tag['code']: tag['label'] for tag in item_tags(note)}
    assert tags['planned-time'] == 'Planung ohne Uhrzeit'
    assert 'time' not in tags
    summary = note['task_display']['summary']
    assert 'Deine Notiz hält fest' in summary and 'keine Abgabeuhrzeit belegt' in summary
    assert 'verknüpfte Frist für diese Aufgabe nennt eine Uhrzeit' in summary
    assert note['data'] == original

    # A personal correction already applied to the source is the effective value.
    source['data'].update(date='2026-11-11', time=None)
    attach_task_displays([note, source])
    assert note['task_display']['deadline_date'] == '2026-11-11'
    tags = {tag['code']: tag['label'] for tag in item_tags(note)}
    assert tags['time'] == 'Frist ohne Uhrzeit' and tags['planned-time'] == 'Planung ohne Uhrzeit'
    assert 'verknüpfte Frist für diese Aufgabe nennt eine Uhrzeit' not in note['task_display']['summary']
    source['date_conflict'] = True
    attach_task_displays([note, source])
    assert note['task_display']['deadline_date'] is None
    assert 'time' in {tag['code'] for tag in item_tags(note)}
