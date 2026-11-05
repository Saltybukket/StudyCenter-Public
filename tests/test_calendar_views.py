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


def test_export_keeps_real_times_dates_and_window_closing(calendar_client):
    response=calendar_client.post('/api/actions',json={'request_id':str(uuid4()),'actions':[
        {'type':'create_event','title':'Synthetic timed','date':'2026-10-25','time':'10:30',
         'end_date':'2026-10-25','end_time':'11:15','date_meaning':'start'},
        {'type':'create_event','title':'Synthetic all-day','date':'2026-10-24',
         'end_date':'2026-10-25','date_meaning':'start'},
        {'type':'create_task','title':'Synthetic deadline','date':'2026-10-20','time':'08:00',
         'end_date':'2026-10-27','end_time':'17:30','date_meaning':'window'},
        {'type':'create_task','title':'Unknown closing','date':'2026-10-20','date_meaning':'window'},
        {'type':'create_event','title':'No duration','date':'2026-10-24','time':'09:00','date_meaning':'start'},
    ]})
    assert response.status_code==200
    result=calendar_client.get('/api/calendar/export')
    assert result.status_code==200
    body=result.text
    assert 'BEGIN:VTIMEZONE' in body and 'TZID:Europe/Vienna' in body
    assert 'DTSTART;TZID=Europe/Vienna:20261025T103000' in body
    assert 'DTEND;TZID=Europe/Vienna:20261025T111500' in body
    assert 'DTSTART;VALUE=DATE:20261024' in body and 'DTEND;VALUE=DATE:20261026' in body
    assert 'DTSTART;TZID=Europe/Vienna:20261027T173000' in body
    assert 'Unknown closing' not in body
    point=next(e for e in body.split('BEGIN:VEVENT') if 'SUMMARY:No duration' in e)
    assert 'DTEND' not in point
    assert all(len(line.encode('utf-8'))<=75 for line in body.split('\r\n'))


def test_calendar_shows_closing_time_with_closing_date(calendar_client):
    calendar_client.post('/api/actions',json={'request_id':str(uuid4()),'actions':[
        {'type':'create_task','title':'Synthetic window','date':'2026-10-20','time':'08:00',
         'end_date':'2026-10-27','end_time':'17:30','date_meaning':'window'},
        {'type':'create_task','title':'Synthetic day only','date':'2026-10-20','time':'08:00',
         'end_date':'2026-10-27','date_meaning':'window'},
    ]})
    soup=BeautifulSoup(calendar_client.get('/calendar?view=week&date=2026-10-27').text,'html.parser')
    target=soup.select_one('[data-date="2026-10-27"]')
    assert '17:30 Synthetic window' in target.get_text(' ',strip=True)
    assert '08:00' not in target.get_text(' ',strip=True)


def test_export_skips_conflicted_or_unclear_source_dates():
    from studycenter_public.services.calendar_export import export_calendar
    common={'id':1,'kind':'task','title':'Synthetic','date':'2026-10-20','date_meaning':'deadline'}
    assert 'BEGIN:VEVENT' not in export_calendar([common|{'date_conflict':True}], 'test')
    assert 'BEGIN:VEVENT' not in export_calendar([common|{'source_state':'unclear'}], 'test')
    assert 'BEGIN:VEVENT' in export_calendar([common], 'test')


def test_export_personal_same_day_end_without_end_date(calendar_client):
    result=calendar_client.post('/api/actions',json={'request_id':str(uuid4()),'actions':[
        {'type':'create_event','title':'Same day','date':'2026-11-10','time':'09:00',
         'end_time':'10:30','date_meaning':'start'},
        {'type':'create_event','title':'Ambiguous end','date':'2026-11-10','time':'22:00',
         'end_time':'01:00','date_meaning':'start'},
    ]})
    assert result.status_code==200
    events=calendar_client.get('/api/calendar/export').text.split('BEGIN:VEVENT')
    same=next(event for event in events if 'SUMMARY:Same day' in event)
    ambiguous=next(event for event in events if 'SUMMARY:Ambiguous end' in event)
    assert 'DTEND;TZID=Europe/Vienna:20261110T103000' in same
    assert 'DTEND' not in ambiguous


def test_timeline_lookback_keeps_completed_past_and_filters(calendar_client):
    from datetime import datetime,timedelta
    from studycenter_public.config import VIENNA
    today=datetime.now(VIENNA).date()
    response=calendar_client.post('/api/actions',json={'request_id':str(uuid4()),'actions':[
        {'type':'create_task','title':'Synthetic past','date':str(today-timedelta(days=5))},
        {'type':'create_task','title':'Synthetic future','date':str(today+timedelta(days=2))},
        {'type':'create_note','title':'Synthetic other type','date':str(today+timedelta(days=2))},
    ]})
    ids=response.json()['item_ids']
    calendar_client.post('/api/actions',json={'request_id':str(uuid4()),'actions':[
        {'type':'set_status','item_id':ids[0],'status':'done'}]})
    page=calendar_client.get('/timeline?past=7&days=14&kind=task')
    assert page.status_code==200
    assert 'Synthetic past' in page.text and 'Synthetic future' in page.text
    assert 'Synthetic other type' not in page.text
    soup=BeautifulSoup(page.text,'html.parser')
    assert soup.select_one(f'[data-day="{today}"]') is not None
    past=soup.select_one(f'[data-day="{today-timedelta(days=5)}"]')
    assert 'Erledigt' in past.get_text() and past.select_one('.item-status') is None
    assert 'past=7' in soup.find('a',string='30 Tage')['href']
    assert 'Synthetic past' not in calendar_client.get('/timeline').text


def test_timeline_limits_lookback_even_for_explicit_dates(calendar_client):
    from datetime import datetime,timedelta
    from studycenter_public.config import VIENNA
    today=datetime.now(VIENNA).date()
    calendar_client.post('/api/actions',json={'request_id':str(uuid4()),'actions':[
        {'type':'create_task','title':'Synthetic 31 days ago','date':str(today-timedelta(days=31))},
        {'type':'create_task','title':'Synthetic 30 days ago','date':str(today-timedelta(days=30))},
    ]})
    page=calendar_client.get('/timeline',params={'start':str(today-timedelta(days=100)),'end':str(today)})
    assert page.status_code==200 and 'Synthetic 30 days ago' in page.text
    assert 'Synthetic 31 days ago' not in page.text
