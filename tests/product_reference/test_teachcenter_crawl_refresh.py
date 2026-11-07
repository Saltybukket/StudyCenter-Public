import json
from types import SimpleNamespace

import pytest

from studycenter_public.core.collectors import Fetched, crawl


ROOT = 'https://example.org/course/view.php?id=1'
OLD = 'https://example.org/pluginfile.php/1/material.bin'
QUIZ = 'https://example.org/mod/quiz/view.php?id=2'
PAGE = 'https://example.org/mod/page/view.php?id=3'


class DB:
    def __init__(self, source): self.source = source
    def execute(self, sql, args):
        if sql.startswith('UPDATE sources SET crawl_state='):
            self.source['crawl_state'] = args[0]
        elif sql.startswith('UPDATE sources SET state='):
            self.source['crawl_state'] = args[2]


class Collector:
    def __init__(self): self.calls = []
    async def fetch(self, url, source):
        self.calls.append(url)
        if url == OLD: raise ValueError('unsupported download')
        html = f'<html><main><a href="{QUIZ}">Quiz 2</a></main></html>' if url == ROOT else '<html><main>Activity</main></html>'
        return Fetched(url, html.encode(), 'text/html', 'Kurs')


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', [1, 2])
async def test_resume_refreshes_root_discovers_quiz_and_progresses_backlog(monkeypatch, limit):
    async def no_sleep(*args): pass
    monkeypatch.setattr('studycenter_public.core.collectors.asyncio.sleep', no_sleep)
    source = {'id': 1, 'course_id': 1, 'kind': 'teachcenter', 'title': 'Kurs', 'url': ROOT,
              'config': json.dumps({'max_pages': limit}),
              'crawl_state': json.dumps({'queue': [OLD, PAGE], 'visited': [ROOT]})}
    db, collector = DB(source), Collector()
    saved = []
    store = SimpleNamespace(save=lambda *args: saved.append(args))
    results = [await crawl(source, collector, db, store) for _ in range(6)]
    assert all(r['fetched'] <= limit for r in results)
    assert collector.calls[0] == ROOT
    assert collector.calls.index(QUIZ) < collector.calls.index(OLD)
    assert collector.calls.count(QUIZ) == 1
    assert PAGE in collector.calls and OLD in collector.calls
    state = json.loads(source['crawl_state'])
    assert OLD in state['queue'] and QUIZ in state['visited'] and PAGE in state['visited']
    assert len(state['queue']) == len(set(state['queue']))


@pytest.mark.asyncio
async def test_failing_new_activity_does_not_starve_backlog(monkeypatch):
    async def no_sleep(*args): pass
    monkeypatch.setattr('studycenter_public.core.collectors.asyncio.sleep', no_sleep)
    source = {'id': 1, 'course_id': 1, 'kind': 'teachcenter', 'title': 'Kurs', 'url': ROOT,
              'config': json.dumps({'max_pages': 2}),
              'crawl_state': json.dumps({'queue': [OLD, PAGE], 'visited': [ROOT]})}
    collector = Collector()
    original_fetch = collector.fetch
    async def fail_quiz(url, source):
        if url == QUIZ:
            collector.calls.append(url)
            raise ValueError('temporarily unavailable')
        return await original_fetch(url, source)
    collector.fetch = fail_quiz
    db = DB(source)
    for _ in range(3):
        await crawl(source, collector, db, SimpleNamespace(save=lambda *args: None))
    assert collector.calls[:2] == [ROOT, QUIZ]
    assert PAGE in collector.calls
    state = json.loads(source['crawl_state'])
    assert QUIZ in state['queue'] and PAGE in state['visited']


@pytest.mark.asyncio
async def test_budget_message_keeps_fetch_errors_visible(monkeypatch):
    async def no_sleep(*args): pass
    monkeypatch.setattr('studycenter_public.core.collectors.asyncio.sleep', no_sleep)
    source = {'id': 1, 'course_id': 1, 'kind': 'teachcenter', 'title': 'Kurs', 'url': ROOT,
              'config': json.dumps({'max_pages': 2}),
              'crawl_state': json.dumps({'queue': [OLD, PAGE], 'visited': [ROOT]})}

    class CollectorWithoutNewActivities(Collector):
        async def fetch(self, url, source):
            if url == ROOT:
                return Fetched(url, b'<html><main>Course</main></html>', 'text/html', 'Kurs')
            return await super().fetch(url, source)

    result = await crawl(source, CollectorWithoutNewActivities(), DB(source), SimpleNamespace(save=lambda *args: None))
    assert not result['complete']
    assert 'Abrufgrenze erreicht' in result['message']
    assert OLD in result['message'] and 'unsupported download' in result['message']
