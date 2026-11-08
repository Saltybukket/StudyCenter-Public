from pathlib import Path
import re
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

from studycenter_public.core.app import create_app
from .reference_settings import Settings


async def test_calendar_keeps_selected_day_across_views_and_mobile_default(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    if (root / '.local/browsers').exists():
        monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(root / '.local/browsers'))
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    async with async_playwright() as driver:
        if not Path(driver.chromium.executable_path).exists():
            pytest.skip('Project Chromium not installed')
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 1280, 'height': 900}, timezone_id='Europe/Vienna')
        async def serve(route):
            url = urlsplit(route.request.url)
            assert url.hostname == 'campusboard.test'
            response = client.get(url.path + ('?' + url.query if url.query else ''))
            await route.fulfill(status=response.status_code, body=response.content,
                                content_type=response.headers.get('content-type', 'application/octet-stream'))
        await page.route('**/*', serve)
        await page.goto('http://campusboard.test/calendar?date=2026-11-02')
        date = page.locator('#calendar-date')
        assert await date.input_value() == '2026-11-02'
        for view in ('dayGridMonth', 'listWeek', 'timeGridWeek'):
            await page.locator(f'[data-calendar-view="{view}"]').click()
            assert await date.input_value() == '2026-11-02'
        await page.locator('.calendar-more-views summary').click()
        await page.locator('[data-calendar-view="gantt"]').click()
        assert await date.input_value() == '2026-11-02'
        await page.locator('[data-calendar-view="timeGridWeek"]').click()
        await page.locator('#calendar-next').click()
        assert await date.input_value() == '2026-11-09'
        await page.locator('#calendar-prev').click()
        assert await date.input_value() == '2026-11-02'
        await page.locator('#calendar-today').click()
        today = await page.evaluate("new Intl.DateTimeFormat('sv-SE', {timeZone:'Europe/Vienna'}).format(new Date())")
        assert await date.input_value() == today
        await date.fill('2026-11-18')
        await page.locator('[data-calendar-view="dayGridMonth"]').click()
        await page.locator('[data-calendar-view="listWeek"]').click()
        assert await date.input_value() == '2026-11-18'
        for selected in ('2026-03-29', '2026-10-25'):
            await date.fill(selected)
            for view in ('dayGridMonth', 'listWeek', 'timeGridWeek'):
                await page.locator(f'[data-calendar-view="{view}"]').click()
                assert await date.input_value() == selected
        await page.locator('.calendar-more-views summary').click()
        assert await page.get_by_role('link', name='Zeitleiste').get_attribute('href') == '/'
        await page.locator('.calendar-more-views summary').click()
        await page.evaluate("localStorage.removeItem('campusboard.calendar.view')")
        await page.set_viewport_size({'width': 390, 'height': 900})
        await page.goto('http://campusboard.test/calendar?date=2026-11-02')
        assert await page.locator('[data-calendar-view="listWeek"]').get_attribute('aria-pressed') == 'true'
        assert await page.evaluate("localStorage.getItem('campusboard.calendar.view')") is None
        await page.locator('[data-calendar-view="dayGridMonth"]').click()
        await page.reload()
        assert await page.locator('[data-calendar-view="dayGridMonth"]').get_attribute('aria-pressed') == 'true'
        await browser.close()


async def test_registration_windows_do_not_fill_calendar_days(tmp_path, monkeypatch):
    from playwright.async_api import expect
    root = Path(__file__).resolve().parents[2]
    if (root / '.local/browsers').exists():
        monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(root / '.local/browsers'))
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    def event(identifier, title, start, end=None, closing=None, kind='registration'):
        props = dict(itemKind=kind, kind=kind, dateMeaning='window' if kind=='registration' else 'start',
                     sourceDate=start, sourceTime=None, sourceEndDate=end, sourceEndTime=None,
                     closingDate=closing, closingTime=None, course='Testkurs', status='open')
        row = dict(id=str(identifier), title=title, start=start, allDay=True, url=f'/items/{identifier}',
                   extendedProps=props, classNames=['kind-'+kind])
        if end: row['end']=end
        return row
    fixtures = [event(1,'Anmeldung bis Juni','2026-10-13','2027-06-24','2027-06-24'),
                event(2,'Anmeldung diese Woche','2026-10-13','2026-11-05','2026-11-05'),
                event(3,'Anmeldeschluss unbekannt','2026-11-02'),
                event(4,'Persönlicher Termin','2026-11-04',kind='event')]
    fixtures[-1]['extendedProps']['status'] = 'done'
    async with async_playwright() as driver:
        if not Path(driver.chromium.executable_path).exists():
            pytest.skip('Project Chromium not installed')
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width':1280,'height':900})
        async def serve(route):
            url = urlsplit(route.request.url)
            assert url.hostname == 'campusboard.test'
            if url.path == '/api/calendar':
                await route.fulfill(json=fixtures)
            else:
                response = client.get(url.path + ('?' + url.query if url.query else ''))
                await route.fulfill(status=response.status_code, body=response.content,
                                    content_type=response.headers.get('content-type','application/octet-stream'))
        await page.route('**/*',serve)
        await page.goto('http://campusboard.test/calendar?date=2026-11-02')
        for view in ('timeGridWeek','dayGridMonth','listWeek'):
            await page.locator(f'[data-calendar-view="{view}"]').click()
            await expect(page.locator('#calendar .calendar-entry')).to_have_count(2)
            assert await page.locator('#calendar .calendar-entry').filter(has_text='Anmeldung diese Woche').count()==1
            assert await page.locator('#calendar .calendar-entry').filter(has_text='Persönlicher Termin').count()==1
            completed = page.locator('#calendar .calendar-entry').filter(has_text='Persönlicher Termin')
            assert 'Termin · Erledigt' in await completed.inner_text()
            assert await completed.locator('strong').evaluate('(node)=>getComputedStyle(node).textDecorationLine') == 'none'
            assert 'Erledigt' in await completed.get_attribute('aria-label')
            await expect(page.locator('#calendar-window-list')).to_be_visible()
            await page.locator('#calendar-window-list').evaluate('(e)=>e.open=true')
            assert 'Anmeldung bis Juni' in await page.locator('#calendar-window-items').inner_text()
            assert 'Anmeldeschluss unbekannt' in await page.locator('#calendar-window-items').inner_text()
        await page.locator('.calendar-more-views summary').click()
        await page.locator('[data-calendar-view="gantt"]').click()
        await expect(page.locator('#gantt .gantt-bar')).to_have_count(2)
        assert 'Anmeldung bis Juni' in await page.locator('#gantt').inner_text()
        await browser.close()


async def test_long_day_entries_are_compact_and_accessible_on_mobile(tmp_path, monkeypatch):
    from playwright.async_api import expect
    root = Path(__file__).resolve().parents[2]
    if (root / '.local/browsers').exists():
        monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(root / '.local/browsers'))
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    titles = [f'{n} Sehr langer Veranstaltungstitel mit vielen zusätzlichen Wörtern für den Kalender' for n in range(5)]
    fixtures = [dict(id=str(n), title=title, start='2026-11-04', allDay=True,
                     url=f'/items/{n}', classNames=['kind-event'],
                     extendedProps=dict(itemKind='event', kind='Termin', dateMeaning='start',
                                        sourceDate='2026-11-04', sourceTime=None, sourceEndDate=None,
                                        course='Testkurs', status='open')) for n, title in enumerate(titles)]
    fixtures.append(dict(id='timed', title='Lange Lehrveranstaltung mit Uhrzeit und umfangreichem Titel',
                         start='2026-11-05T10:00:00', end='2026-11-05T11:30:00', allDay=False,
                         url='/items/timed', classNames=['kind-event'],
                         extendedProps=dict(itemKind='event', kind='Termin', dateMeaning='start',
                                            sourceDate='2026-11-05', sourceTime='10:00:00',
                                            sourceEndDate='2026-11-05', sourceEndTime='11:30:00',
                                            course='Testkurs', status='open')))
    async with async_playwright() as driver:
        if not Path(driver.chromium.executable_path).exists():
            pytest.skip('Project Chromium not installed')
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 390, 'height': 900})
        async def serve(route):
            url = urlsplit(route.request.url)
            assert url.hostname == 'studycenter_public.core.test'
            if url.path == '/api/calendar':
                await route.fulfill(json=fixtures)
            else:
                response = client.get(url.path + ('?' + url.query if url.query else ''))
                await route.fulfill(status=response.status_code, body=response.content,
                                    content_type=response.headers.get('content-type', 'application/octet-stream'))
        await page.route('**/*', serve)
        await page.goto('http://studycenter_public.core.test/calendar?date=2026-11-04&view=timeGridWeek')
        more = page.get_by_role('button', name=re.compile('weitere'))
        await expect(more).to_be_visible()
        assert await more.evaluate('(e) => e.closest("[role=row]").getBoundingClientRect().height') < 160
        await more.click()
        await page.locator('.calendar-entry').filter(has_text=titles[4][:20]).click(timeout=3000)
        await expect(page.locator('#calendar-event-title')).to_have_text(titles[4])
        await expect(page.locator('#calendar-event-when')).to_contain_text('04.11.2026 · ohne Uhrzeit')
        await page.locator('#calendar-event-close').click()
        await page.locator('[data-calendar-view="dayGridMonth"]').click()
        more = page.get_by_role('button', name='+2', exact=True)
        await expect(more).to_be_visible()
        timed = page.locator('#calendar .calendar-entry[href="/items/timed"]')
        await expect(timed).to_be_visible()
        assert await timed.locator('.schedule-time').evaluate('(e) => getComputedStyle(e).position') == 'absolute'
        assert await timed.locator('strong').evaluate('(e) => getComputedStyle(e).whiteSpace') == 'nowrap'
        assert await page.locator('#calendar-surface').evaluate('(e) => e.scrollWidth <= e.clientWidth + 1')
        await more.click()
        await page.locator('.calendar-entry').filter(has_text=titles[4][:20]).click(timeout=3000)
        await expect(page.locator('#calendar-event-title')).to_have_text(titles[4])
        await page.locator('#calendar-event-close').click()
        await timed.click()
        await expect(page.locator('#calendar-event-when')).to_contain_text('10:00')
        await page.locator('#calendar-event-close').click()
        await page.set_viewport_size({'width': 1280, 'height': 900})
        await page.locator('[data-calendar-view="timeGridWeek"]').click()
        await expect(timed).to_be_visible()
        assert await timed.locator('.schedule-time').evaluate('(e) => getComputedStyle(e).position') != 'absolute'
        await page.locator('[data-calendar-view="dayGridMonth"]').click()
        await expect(timed).to_be_visible()
        assert await timed.locator('.schedule-time').evaluate('(e) => getComputedStyle(e).position') == 'absolute'
        assert await timed.locator('strong').evaluate('(e) => getComputedStyle(e).whiteSpace') == 'nowrap'
        await browser.close()
