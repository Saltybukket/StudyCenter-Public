from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright, expect

from studycenter_public.core.app import create_app
from .reference_settings import Settings


async def test_navigation_reflows_and_settings_history_preserves_reminder_values(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    if (root / '.local/browsers').exists():
        monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(root / '.local/browsers'))
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    async with async_playwright() as driver:
        if not Path(driver.chromium.executable_path).exists():
            pytest.skip('Project Chromium not installed')
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        async def serve(route):
            url = urlsplit(route.request.url)
            assert url.hostname == 'campusboard.test'
            assert route.request.method == 'GET'  # UI checks must not save settings.
            response = client.get(url.path + ('?' + url.query if url.query else ''))
            await route.fulfill(status=response.status_code, body=response.content,
                                content_type=response.headers.get('content-type', 'application/octet-stream'))

        await page.route('**/*', serve)
        await page.goto('http://campusboard.test/overview')
        assert await page.locator('nav[aria-label="Hauptnavigation"] a').count() == 5
        assert await page.locator('nav[aria-label="Hauptnavigation"] a[href="/quests"]').count() == 1
        for width in (1440, 820, 390):
            await page.set_viewport_size({'width': width, 'height': 1000})
            bounds = await page.evaluate('''() => ({
                header:document.querySelector('.masthead').getBoundingClientRect().bottom,
                search:document.querySelector('.header-search').getBoundingClientRect().bottom,
                width:innerWidth, scroll:document.documentElement.scrollWidth})''')
            assert bounds['search'] <= bounds['header']
            assert bounds['scroll'] <= bounds['width']
        assert await page.locator('.header-search').is_hidden()
        assert (await page.locator('#main').bounding_box())['y']<160
        await page.locator('#mobile-search-toggle').click()
        await expect(page.locator('#global-search')).to_be_focused()
        await page.locator('#global-search').fill('Messtechnik')
        await page.locator('#global-search').press('Escape')
        assert await page.locator('.header-search').is_hidden()
        await page.locator('#mobile-search-toggle').click()
        assert await page.locator('#global-search').input_value()=='Messtechnik'
        await page.locator('#global-search').press('Escape')
        await page.locator('.login-menu>summary').click()
        assert await page.locator('#sync-button').is_visible()
        assert await page.locator('#login-button').is_visible()
        assert await page.locator('#voice-open').is_visible()
        await page.goto('http://campusboard.test/settings?section=notifications')
        group = page.locator('[data-reminder-preset]').first
        original = await group.locator('input').input_value()
        await group.locator('.reminder-choice select').select_option('1440, 120')
        assert await group.locator('input').input_value() == '1440, 120'
        await group.locator('.reminder-choice select').select_option('custom')
        await group.locator('input').fill('75, 25')
        assert await group.locator('.reminder-choice select').input_value() == 'custom'
        await page.locator('[data-settings-nav] a[href="#display"]').click()
        await page.go_back()
        assert 'section=notifications' in page.url
        assert await group.locator('input').input_value() == '75, 25'
        await page.reload()
        assert await group.locator('input').input_value() == original
        advanced=page.locator('details').filter(has=page.locator('input[name=previous_day]')).last
        assert not await advanced.get_attribute('open')
        await page.locator('input[name=previous_day]').evaluate("e=>e.value=''")
        await page.locator('form[action="/settings/notifications"]').evaluate('form=>form.reportValidity()')
        assert await advanced.get_attribute('open') is not None
        await page.locator('input[name=previous_day]').fill('09:00')
        data=await page.locator('form[action="/settings/notifications"]').evaluate('form=>Object.fromEntries(new FormData(form))')
        assert all(name in data for name in ('previous_day','evening','quiet_start','quiet_end','exam_minutes'))
        await page.goto('http://campusboard.test/settings?section=google')
        assert await page.locator('[data-google-action=connect]').is_visible()
        assert await page.locator('[data-google-action=sync]').is_hidden()
        await page.locator('.google-more-actions summary').click()
        assert await page.locator('[data-google-action=disconnect]').is_visible()
        await page.route('**/api/desktop/status',lambda route:route.fulfill(json={'google':{'connected':True,'enabled':False,'error':'Anmeldung erneuern','conflicts':[]}}))
        await page.evaluate("dispatchEvent(new Event('focus'))")
        await expect(page.locator('[data-google-action=connect]')).to_have_text('Erneut anmelden')
        assert await page.locator('[data-google-action=sync]').is_visible()
        conflict={'payload':'{"summary":"Synthetic event"}','error':'Zeitkonflikt','identity':'test','role':'deadline'}
        await page.route('**/api/desktop/status',lambda route:route.fulfill(json={'google':{'connected':True,'enabled':False,'conflicts':[conflict]}}))
        await page.evaluate("dispatchEvent(new Event('focus'))")
        conflict_button=page.locator('#google-conflicts button').first
        await expect(conflict_button).to_have_count(1)
        await page.locator('#google-conflicts').evaluate("e=>e.closest('details').open=true")
        await conflict_button.focus()
        async with page.expect_response('**/api/desktop/status'):
            await page.evaluate("dispatchEvent(new Event('focus'))")
        await expect(conflict_button).to_be_focused()
        assert not errors
        await browser.close()


async def test_sync_summary_excludes_deferred_media_but_keeps_real_errors(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(root / '.local/browsers'))
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    state = {'active': True, 'stage': 'Quellen prüfen', 'sources': [], 'run': {'state': 'running'}}
    async with async_playwright() as driver:
        if not Path(driver.chromium.executable_path).exists():
            pytest.skip('Project Chromium not installed')
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page()

        async def serve(route):
            url = urlsplit(route.request.url)
            assert url.hostname == 'campusboard.test' and route.request.method == 'GET'
            if url.path == '/api/status':
                await route.fulfill(json=state)
            else:
                response = client.get(url.path)
                await route.fulfill(status=response.status_code, body=response.content,
                                    content_type=response.headers.get('content-type', 'application/octet-stream'))

        await page.route('**/*', serve)
        await page.goto('http://campusboard.test/calendar', wait_until='networkidle')
        state.update(active=False, sources=[{'active': True, 'course_active': 1, 'state': 'success',
                      'analysis_pending': 85, 'deferred_analysis': 85, 'reading_gaps': 0}], run={'state': 'success'})
        await page.evaluate('poll(true)')
        text = await page.locator('#notice').inner_text()
        assert 'Quellen aktualisiert.' in text and 'Auswertungen offen' not in text
        state.update(active=True)
        await page.evaluate('poll(true)')
        state['sources'][0].update(state='failed', analysis_pending=86)
        state.update(active=False, run={'state': 'partial'})
        await page.evaluate('poll(true)')
        text = await page.locator('#notice').inner_text()
        assert '1 Auswertung offen.' in text and '1 Quellenzugang prüfen.' in text
        assert await page.locator('#notice a[href="/sources"]').count() == 1
        await browser.close()


def test_polish_excludes_postit_routes_and_keeps_no_js_search(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        for route in ('/board','/pins','/archive'):
            html=client.get(route).text
            assert 'polish.css' not in html and 'polish.js' not in html
            assert 'mobile-search-toggle' not in html
        for route in ('/overview','/quests','/settings','/sources','/documents','/calendar','/search'):
            html=client.get(route).text
            assert 'polish.css' in html and 'id="header-search-form"' in html
            assert 'class="header-search" id="header-search-form" role="search"' in html
        assert 'data-settings-advanced open' in client.get('/settings').text
