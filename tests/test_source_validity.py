"""Synthetic source admission regressions; no external provider required."""
import pytest

from studycenter_public.domain.source_validity import explicit_course_semester, historical_semester_candidate
from studycenter_public.storage.db import Database


@pytest.mark.parametrize('quote', ['Abgabe 16.10.2025', 'Abgabe 2025-10-16',
    'Abgabe 16/10/2025', 'Schließt 16. Oktober 2025', 'Closes October 16, 2025'])
def test_wrong_year_rejects_whole_batch(tmp_path, quote):
    db = Database(tmp_path/'test.sqlite3')
    doc = db.save_document({'title':'Synthetic'}, pages=[{'page':1,'text':'First entry. '+quote}])
    common = {'title':'Task','kind':'task','page':1,'anchor':'task'}
    good = common | {'quote':'First entry.'}
    bad = common | {'quote':quote,'date':'2026-10-16','date_meaning':'deadline'}
    with pytest.raises(ValueError, match='Datumsjahr widerspricht'):
        db.ingest_many(doc, [good, bad])
    assert db.items() == []


def test_explicit_correction_and_different_day_remain_allowed(tmp_path):
    db = Database(tmp_path/'test.sqlite3')
    text = 'Alt 16.10.2025, neu 16.10.2026. Neue Frist 17.10.'
    doc = db.save_document({'title':'Synthetic'}, pages=[{'page':1,'text':text}])
    common = {'title':'Task','kind':'task','page':1,'anchor':'task','quote':text,'date_meaning':'deadline'}
    db.ingest(doc, common | {'date':'2026-10-16'})
    db.ingest(doc, common | {'date':'2026-10-17'})
    assert db.items()


def test_old_semester_is_separate_from_current_course_identity(tmp_path):
    db = Database(tmp_path/'test.sqlite3')
    source = db.upsert_source(name='Synthetic', kind='public')
    course = db.upsert_course(source['id'], 'course', 'Synthetic', {'semester':'2026W'})
    text = 'Im WS 2025/26 ist A1 abzugeben. Im WS 2026/27 ist A1 abzugeben.'
    doc = db.save_document({'source_id':source['id'], 'url':'https://example.test/source',
                            'trusted_course_id':course['id']}, pages=[{'page':1,'text':text}])
    common = {'title':'A1','kind':'task','page':1,'anchor':'A1','identity_scope':'course',
              'identity_key':'A1','obligation':'required'}
    old = db.ingest(doc, common | {'quote':'Im WS 2025/26 ist A1 abzugeben.'})
    current = db.ingest(doc, common | {'quote':'Im WS 2026/27 ist A1 abzugeben.'})
    assert old['id'] != current['id']
    assert old['data']['semester'] == '2025W'
    assert old['data']['source_state'] == old['data']['obligation'] == 'unclear'
    assert current['data']['obligation'] == 'required'
    assert len(old['evidence']) == len(current['evidence']) == 1


@pytest.mark.parametrize('quote', ['Seit dem WS 2025/26 gilt diese Regel weiter.',
    'Ab SS 2025 gibt es Aufgaben.', 'Im SS 2026 und im WS 2026/27 gilt die Regel.'])
def test_continuing_or_ambiguous_rule_is_not_reclassified(quote):
    value = {'quote':quote, 'identity_scope':'course', 'identity_key':'A1'}
    assert historical_semester_candidate(value, '2026W') == value


@pytest.mark.parametrize('title,expected', [
    ('Synthetic course WS 2026/27','2026W'), ('Synthetic course 2026S','2026S'),
    ('Synthetic course SS 2026','2026S'), ('Synthetic course',''),
    ('Synthetic course WS 2025/26 and WS 2026/27',''), ('Synthetic course WS 2026/28',''),
])
def test_course_semester_requires_one_explicit_consistent_label(title,expected):
    assert (explicit_course_semester(title) or '') == expected


def test_selected_course_carries_only_discovered_semester(tmp_path):
    from types import SimpleNamespace
    from studycenter_public.services.sync import SyncService
    db=Database(tmp_path/'test.sqlite3')
    source=db.upsert_source(name='Synthetic',kind='teachcenter',metadata={'discovered_courses':[
        {'external_id':'1','title':'Synthetic WS 2026/27','url':'https://example.test/course','kind':'teachcenter'}]})
    service=SyncService(db,SimpleNamespace(),browser=object(),documents=object())
    service.select_courses(source['id'],[{'external_id':'1','semester':'2025W'}])
    assert db.courses()[0]['semester']=='2026W'
    db.upsert_source(source['id'],metadata={'discovered_courses':[
        {'external_id':'1','title':'Synthetic course','url':'https://example.test/course','kind':'teachcenter'}]})
    service.select_courses(source['id'],['1'])
    assert db.courses()[0]['semester'] is None
