from datetime import datetime

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from studycenter_public.core.app import create_app
from .reference_settings import Settings
from studycenter_public.core.models import Candidate
from studycenter_public.core.config import VIENNA


def test_today_renders_real_quest_appointments_and_uncertain_deadlines(tmp_path, monkeypatch):
    from studycenter_public.core import app as app_module

    fixed = datetime(2030, 10, 26, 9, tzinfo=VIENNA)
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(fixed.timestamp(), tz)
    monkeypatch.setattr(app_module, 'datetime', FixedDatetime)
    app = create_app(Settings(data_dir=tmp_path))

    def item(identifier, title, **changes):
        data = Candidate(title=title, kind='event', date='2030-10-26', time='10:00',
                         date_meaning='start', quote='Beleg', page=1, anchor='test').model_dump(mode='json') | changes
        return {'id': identifier, 'identity': f'fixture:{identifier}', 'data': data, 'status': 'open',
                'ignored': 0, 'conflict': 0, 'date_conflict': False, 'pinned': 0, 'personal_event': True,
                'seen': 1, 'changed_at': '2030-10-25T08:00:00+00:00', 'history': []}

    appointments = [item(i, f'Termin {i}') for i in range(1, 16)]
    overdue = item(16, 'Überfällige Aufgabe', kind='task', date='2030-10-25', time=None,
                   date_meaning='deadline', obligation='required')
    conflict = item(17, 'Strittige Frist', kind='task', date='2030-10-26', time=None,
                    date_meaning='deadline', obligation='required')
    conflict.update(conflict=True, date_conflict=True)
    future = item(18, 'Nächste Abgabe', kind='task', date='2030-10-27', time=None,
                  date_meaning='deadline', obligation='required')
    rows = appointments + [overdue, conflict, future]
    app.state.db.items = lambda active_only=False: rows
    quest = {'id':'q', 'title':'Eigene Quest', 'state':'active', 'planning':'paced',
             'steps':[{'title':'Ein echter Schritt', 'done':False, 'scheduled_date':'2030-10-26', 'minutes':15}],
             'total_count':2, 'completed_count':1, 'warnings':['Quelle bitte prüfen.']}
    later = quest | {'id':'future', 'title':'Später', 'steps':[quest['steps'][0] | {'scheduled_date':'2030-11-01'}]}
    app.state.quests.dashboard = lambda: {'quests':[later, quest]}
    page = BeautifulSoup(TestClient(app).get('/overview').text, 'html.parser')
    assert 'Eigene Quest' in page.select_one('.today-quest').get_text(' ', strip=True)
    assert 'Ein echter Schritt' in page.select_one('.today-quest').get_text(' ', strip=True)
    assert 'Quelle bitte prüfen.' in page.select_one('.today-quest').get_text(' ', strip=True)
    assert page.select_one('.today-quest a')['href'] == '/quests?quest=q'
    assert len(page.select('#today-calendar li')) == 15
    assert 'Strittige Frist' in page.select_one('#dated-checks').get_text(' ', strip=True)
    assert 'Überfällige Aufgabe' in page.select_one('#actions').get_text(' ', strip=True)
    deadline = page.select_one('.today-deadline').get_text(' ', strip=True)
    assert 'Nächste Abgabe' in deadline and 'Ohne Uhrzeit' in deadline
    assert 'Strittige Frist' not in deadline and 'Überfällige Aufgabe' not in deadline
    assert all(row['status'] == 'open' for row in rows)


def test_routine_appointments_do_not_displace_next_task(tmp_path, monkeypatch):
    from studycenter_public.core import app as app_module
    fixed = datetime(2030, 10, 26, 9, tzinfo=VIENNA)
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(fixed.timestamp(), tz)
    monkeypatch.setattr(app_module, 'datetime', FixedDatetime)
    app = create_app(Settings(data_dir=tmp_path))
    def item(identifier, **changes):
        data = Candidate(title='Pflichttermin', kind='event', date='2030-10-30', time='10:00',
                         date_meaning='start', obligation='required', quote='Beleg', page=1,
                         anchor='test').model_dump(mode='json') | changes
        return {'id': identifier, 'identity': f'fixture:{identifier}', 'data':data, 'status':'open',
                'ignored':0, 'conflict':0, 'date_conflict':False, 'pinned':0, 'personal_event':True,
                'seen':1, 'changed_at':'2030-10-25T08:00:00+00:00', 'history':[]}
    rows = [item(i) for i in range(1, 8)]
    rows.append(item(8, title='Spätere Aufgabe', kind='task', date='2030-11-03', date_meaning='deadline'))
    app.state.db.items = lambda active_only=False: rows
    page = BeautifulSoup(TestClient(app).get('/overview').text, 'html.parser')
    assert 'Spätere Aufgabe' in page.select_one('#actions').get_text(' ', strip=True)
    assert not page.select('#actions a[href^="/items/1"]')
    assert 'Pflichttermin' in page.select_one('#week').get_text(' ', strip=True)


def test_today_empty_state_does_not_invent_content(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    page = BeautifulSoup(TestClient(app).get('/overview').text, 'html.parser')
    assert 'Noch kein offener Schritt' in page.select_one('.today-quest').get_text(' ', strip=True)
    assert 'Keine belegten persönlichen Termine' in page.select_one('#today-calendar').get_text(' ', strip=True)
    assert not page.select_one('.today-deadline')

async def test_today_action_changes_only_selected_task(tmp_path, monkeypatch):
    from pathlib import Path
    from playwright.async_api import async_playwright, expect

    root = Path(__file__).resolve().parents[2]
    if (root / '.local/browsers').exists():
        monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(root / '.local/browsers'))
    app = create_app(Settings(data_dir=tmp_path))
    db = app.state.db
    value = Candidate(title='Gezielte Aufgabe', kind='task', date=datetime.now(VIENNA).date(),
                      date_meaning='deadline', quote='Eigener Eintrag', page=1, anchor='test')
    identifier = db.execute("INSERT INTO items(identity,payload,created_at,changed_at) VALUES(?,?,'2030-01-01','2030-01-01')",
                            ('note:today-test', value.model_dump_json()))
    client = TestClient(app, base_url='http://127.0.0.1')
    async with async_playwright() as driver:
        if not Path(driver.chromium.executable_path).exists():
            import pytest
            pytest.skip('Project Chromium not installed')
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page()
        async def serve(route):
            request = route.request
            path = request.url.split('127.0.0.1', 1)[1]
            response = client.request(request.method, path, content=request.post_data_buffer,
                                      headers=request.headers, follow_redirects=True)
            await route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)
        await page.route('**/*', serve)
        await page.goto('http://127.0.0.1/overview')
        action = page.get_by_role('button', name='Erledigt: Gezielte Aufgabe')
        await expect(action).to_be_visible()
        assert db.one('SELECT status FROM items WHERE id=?', (identifier,))['status'] == 'open'
        async with page.expect_navigation():
            await action.click()
        assert db.one('SELECT status FROM items WHERE id=?', (identifier,))['status'] == 'done'
        await expect(page.get_by_role('button', name='Erledigt: Gezielte Aufgabe')).to_have_count(0)
        await browser.close()
