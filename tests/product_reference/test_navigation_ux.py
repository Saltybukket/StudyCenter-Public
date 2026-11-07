from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

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
        await page.locator('.settings-section-select select').select_option('display')
        await page.go_back()
        assert 'section=notifications' in page.url
        assert await group.locator('input').input_value() == '75, 25'
        await page.reload()
        assert await group.locator('input').input_value() == original
        assert not errors
        await browser.close()


async def test_sync_summary_excludes_deferred_media_but_keeps_real_errors(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    if (root / '.local/browsers').exists():
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
