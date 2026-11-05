import json
from types import SimpleNamespace

import pytest

from studycenter_public.core.llm import ModelAdapter
from studycenter_public.core.models import Candidate
from studycenter_public.core.source_validity import historical_semester_candidate, validate_source_dates


def candidate(quote, date=None, **kwargs):
    return Candidate(title='Abgabe', kind='task', quote=quote, page=1,
                     anchor='abgabe', date=date, **kwargs)


@pytest.mark.parametrize('quote', ['Abgabe 16.10.2025', 'Abgabe 2025-10-16', 'Abgabe 16/10/2025',
                                 'Schließt: 16. Oktober 2025', 'Closes: October 16, 2025'])
def test_rejects_year_relabel_of_explicit_date(quote):
    with pytest.raises(ValueError, match='Datumsjahr widerspricht'):
        validate_source_dates(candidate(quote, '2026-10-16'))


def test_supported_dates_undated_rules_and_corrections_are_not_blocked():
    for item in [candidate('Abgabe 16.10.2025', '2025-10-16'),
                 candidate('Abgabe 16.10.2026', '2026-10-16'),
                 candidate('Jede Woche ein Quiz'),
                 candidate('Alt 16.10.2025, neu 16.10.2026', '2026-10-16'),
                 candidate('Abgabe 16.10.2025, neue Frist 17.10.', '2026-10-17')]:
        validate_source_dates(item)


@pytest.mark.asyncio
async def test_extraction_retries_contradiction_then_accepts_supported_evidence():
    model = ModelAdapter(None, SimpleNamespace(model_provider='local'))
    class Gotchas:
        def guidance(self, *args): return ''
        def record(self, *args, **kwargs): pass
    model.gotchas = Gotchas()
    instructions = []
    async def completion(instruction, payload):
        instructions.append(instruction)
        year = 2026 if len(instructions) == 1 else 2025
        return json.dumps({'items': [candidate('Abgabe 16.10.2025', f'{year}-10-16').model_dump(mode='json')]})
    model.chat_completions = completion
    result = await model.extract([{'page': 1, 'anchor': 'a', 'text': 'Abgabe 16.10.2025', 'gaps': []}], {})
    assert result.items[0].date.isoformat() == '2025-10-16'
    assert len(instructions) == 2 and 'Datumsjahr widerspricht' in instructions[1]


@pytest.mark.asyncio
async def test_extraction_repeated_contradiction_is_visible_failure():
    model = ModelAdapter(None, SimpleNamespace(model_provider='local'))
    model.gotchas = SimpleNamespace(guidance=lambda *args: '', record=lambda *args, **kwargs: None)
    async def completion(*args):
        return json.dumps({'items': [candidate('Abgabe 16.10.2025', '2026-10-16').model_dump(mode='json')]})
    model.chat_completions = completion
    with pytest.raises(ValueError, match='Datumsjahr widerspricht'):
        await model.extract([{'page': 1, 'anchor': 'a', 'text': 'Abgabe 16.10.2025', 'gaps': []}], {})


@pytest.mark.asyncio
async def test_saved_partial_progress_is_checked_before_any_ingest():
    class DB:
        def __init__(self): self.writes, self.ingested = [], []
        def source(self, source_id):
            return {'course_id': 1, 'course_title': 'Kurs', 'semester': '2026W',
                    'group_name': '', 'config': '{}', 'kind': 'public'}
        def items(self): return []
        def rows(self, *args): return []
        def execute(self, *args): self.writes.append(args)
        def ingest(self, *args): self.ingested.append(args)
    good = candidate('Frist 17.10.2026', '2026-10-17')
    bad = candidate('Frist 16.10.2025', '2026-10-16')
    db = DB()
    model = ModelAdapter(db, SimpleNamespace(model_ready=True))
    document = {'id': 1, 'source_id': 1, 'analysis_state': 'pending', 'processing_state': 'success',
                'pages': json.dumps([{'page': 1, 'anchor': 'page', 'text': 'Frist 17.10.2026 und Frist 16.10.2025', 'gaps': []}]),
                'progress': json.dumps({'0': {'items': [good.model_dump(mode='json'), bad.model_dump(mode='json')]}}),
                'url': 'https://example.org/page', 'title': 'Seite', 'published_at': None, 'error': ''}
    await model.analyze(document)
    assert not db.ingested
    assert 'analysis_state=\'failed\'' in db.writes[-1][0]
    assert 'Datumsjahr widerspricht' in db.writes[-1][1][0]


def test_explicit_old_semester_is_isolated_after_namespace_binding():
    old = candidate('Im WS 2025/26 ist Aufgabe A1 abzugeben', identity_scope='course',
                    identity_key='A1', semester='2026W', obligation='required')
    result = historical_semester_candidate(old, {'semester': '2026W'})
    assert (result.semester, result.source_state, result.obligation) == ('2025W', 'unclear', 'unclear')
    assert result.identity_scope == 'document' and result.identity_key is None
    assert 'Gültigkeit' in result.uncertainty
    for quote in ('Im WS 2026/27 ist A1 abzugeben', 'Im SS 2026 ist A1 abzugeben im WS 2026/27',
                  'Das Buch erschien 2025; A1 ist abzugeben', 'Jede Woche ein Quiz',
                  'Seit dem WS 2025/26 gilt diese Regel weiterhin.', 'Ab SS 2025 gibt es wöchentliche Aufgaben.'):
        original = candidate(quote)
        assert historical_semester_candidate(original, {'semester': '2026W'}) == original
