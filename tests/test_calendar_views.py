"""Synthetic calendar behavior through the normal local HTTP interface."""
from uuid import uuid4

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
import pytest

from studycenter_public.platform.paths import AppPaths
from studycenter_public.platform.secrets import SecretStore
from studycenter_public.web import create_app


@pytest.fixture
def calendar_client(tmp_path):
    paths = AppPaths.create(tmp_path / 'calendar-data')
    app = create_app(paths, SecretStore(paths.instance_id, session_only=True))
    app.state.endpoint = 'http://127.0.0.1:8767'
    with TestClient(app, base_url=app.state.endpoint) as client:
        csrf = client.get('/api/session').json()['csrf']
        client.headers.update({'Origin': app.state.endpoint, 'X-CSRF-Token': csrf})
        yield client


def test_week_shows_exact_monday_to_sunday_across_month_boundary(calendar_client):
    response = calendar_client.post('/api/actions', json={
        'request_id': str(uuid4()),
        'actions': [
            {'type': 'create_task', 'title': 'Synthetic Monday', 'date': '2026-09-28'},
            {'type': 'create_task', 'title': 'Synthetic Sunday', 'date': '2026-10-04'},
            {'type': 'create_task', 'title': 'Synthetic next week', 'date': '2026-10-05'},
        ],
    })
    assert response.status_code == 200

    page = calendar_client.get('/calendar?view=week&date=2026-10-01')
    assert page.status_code == 200
    soup = BeautifulSoup(page.text, 'html.parser')
    days = soup.select('.week-grid .calendar-day')
    assert [day['data-date'] for day in days] == [
        '2026-09-28', '2026-09-29', '2026-09-30', '2026-10-01',
        '2026-10-02', '2026-10-03', '2026-10-04',
    ]
    assert 'Synthetic Monday' in days[0].get_text()
    assert 'Synthetic Sunday' in days[6].get_text()
    assert 'Synthetic next week' not in soup.select_one('.week-grid').get_text()


def test_week_navigation_uses_dates_across_year_boundary(calendar_client):
    page = calendar_client.get('/calendar?view=week&date=2027-01-01')
    assert page.status_code == 200
    soup = BeautifulSoup(page.text, 'html.parser')
    navigation = soup.select_one('.calendar-navigation')
    previous = navigation.find('a', attrs={'aria-label': 'Vorherige Woche'})
    following = navigation.find('a', attrs={'aria-label': 'Nächste Woche'})
    assert previous is not None
    assert following is not None
    assert '28.12.2026' in navigation.get_text()
    assert '03.01.2027' in navigation.get_text()
    for link, first, last in [
        (previous, '2026-12-21', '2026-12-27'),
        (following, '2027-01-04', '2027-01-10'),
    ]:
        target = BeautifulSoup(calendar_client.get(link['href']).text, 'html.parser')
        dates = [day['data-date'] for day in target.select('.week-grid .calendar-day')]
        assert len(dates) == 7
        assert (dates[0], dates[-1]) == (first, last)
    picker = soup.select_one('form.calendar-date-picker')
    assert picker is not None
    assert picker.get('method', 'get').lower() == 'get'
    assert picker.select_one('input[name=view]')['value'] == 'week'
    assert picker.select_one('input[type=date][name=date]')['value'] == '2027-01-01'
    chosen = BeautifulSoup(calendar_client.get('/calendar?view=week&date=2027-02-17').text, 'html.parser')
    assert chosen.select_one('.week-grid .calendar-day')['data-date'] == '2027-02-15'


def test_month_and_gantt_keep_entries_and_month_navigation(calendar_client):
    response = calendar_client.post('/api/actions', json={
        'request_id': str(uuid4()),
        'actions': [{'type': 'create_task', 'title': 'Synthetic February task', 'date': '2027-02-17', 'time': '14:30'}],
    })
    assert response.status_code == 200
    for view, selector in [('month', '.month-grid'), ('gantt', '.gantt-grid')]:
        page = calendar_client.get(f'/calendar?view={view}&month=2027-02')
        assert page.status_code == 200
        soup = BeautifulSoup(page.text, 'html.parser')
        surface = soup.select_one(selector)
        assert surface is not None
        assert 'Synthetic February task' in surface.get_text()
        assert '14:30' in surface.get_text()
        assert soup.select_one('.week-grid') is None
        navigation = soup.select_one('.calendar-navigation')
        assert navigation.find('a', attrs={'aria-label': 'Vorheriger Monat'})['href'] == f'/calendar?month=2027-01&view={view}'
        assert navigation.find('a', attrs={'aria-label': 'Nächster Monat'})['href'] == f'/calendar?month=2027-03&view={view}'
        week_link = navigation.find('a', string='Woche')
        week = BeautifulSoup(calendar_client.get(week_link['href']).text, 'html.parser')
        assert len(week.select('.week-grid .calendar-day')) == 7
        assert week.select_one('.week-grid .calendar-day')['data-date'] == '2027-02-01'


def test_week_accepts_month_links_and_preserves_selected_month_for_other_views(calendar_client):
    page = calendar_client.get('/calendar?view=week&month=2027-02')
    assert page.status_code == 200
    soup = BeautifulSoup(page.text, 'html.parser')
    days = soup.select('.week-grid .calendar-day')
    assert [day['data-date'] for day in days] == [f'2027-02-{day:02d}' for day in range(1, 8)]
    assert [day.select_one('strong').get_text() for day in days] == ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
    assert [day.select_one('time')['datetime'] for day in days] == [day['data-date'] for day in days]
    navigation = soup.select_one('.calendar-navigation')
    assert navigation.find('a', string='Monat')['href'] == '/calendar?month=2027-02&view=month'
    assert navigation.find('a', string='Gantt')['href'] == '/calendar?month=2027-02&view=gantt'
    assert navigation.find('a', string='Woche')['aria-current'] == 'page'
    assert soup.select_one('.month-grid') is None
