from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'src/studycenter_public/core'


async def test_updates_ui_with_synthetic_local_api(monkeypatch):
    if (ROOT / '.local/browsers').exists():
        monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.local/browsers'))
    env = Environment(loader=FileSystemLoader(CORE / 'templates'), autoescape=True)
    html = env.from_string("{% macro setting_icon(kind) %}↻{% endmacro %}{% include 'public_updates.html' %}").render()
    js = (CORE / 'static/public-updates.js').read_text(encoding='utf-8')
    css = (CORE / 'static/public-updates.css').read_text(encoding='utf-8')
    async with async_playwright() as driver:
        if not Path(driver.chromium.executable_path).exists():
            pytest.skip('Project Chromium not installed')
        browser = await driver.chromium.launch()
        page = await browser.new_page(viewport={'width': 375, 'height': 812})
        await page.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body=html))
        await page.goto('http://studycenter.test/settings?section=updates')
        await page.add_style_tag(content=css)
        await page.evaluate('''() => {
          window.calls = []; window.failNext = false; window.offline = false;
          window.state = {current_version:'1.0', platform:'Windows', supported:true, auto_check:false, state:'idle', release:null, skipped_version:null, last_checked:'2026-11-08T12:30:00Z'};
          window.api = async (url, options={}) => {
            if (url === '/api/updates') { if (window.offline) throw new Error('offline'); return structuredClone(window.state); }
            const body = JSON.parse(options.body); window.calls.push({url, body});
            if (window.failNext) { window.failNext = false; throw new Error('Testfehler'); }
            if (url.endsWith('/preferences')) window.state.auto_check = body.auto_check;
            if (url.endsWith('/check')) { window.state.state='available'; window.state.release={version:'2.0', notes:'<img src=x onerror=alert(1)>', tag:'v2', url:'https://github.com/Saltybukket/StudyCenter-Public/releases/tag/v2'}; window.state.skipped_version=null; }
            if (url.endsWith('/skip')) window.state.skipped_version=body.version;
            if (url.endsWith('/download')) window.state.state='ready';
            if (url.endsWith('/install')) window.offline=true;
            return {};
          };
        }''')
        await page.add_script_tag(content=js)
        await expect(page.locator('#update-current')).to_contain_text('1.0')
        assert '2026-11-08T' not in await page.locator('#update-status').inner_text()
        assert '2026' in await page.locator('#update-status').inner_text()
        assert not await page.locator('#update-auto').is_checked()
        await page.locator('#update-auto').check()
        await expect(page.locator('#update-auto')).to_be_checked()
        await page.locator('#update-auto').uncheck()
        await expect(page.locator('#update-auto')).not_to_be_checked()
        await page.locator('#update-check').click()
        await expect(page.locator('#update-version')).to_have_text('Version 2.0')
        assert await page.locator('#update-notes img').count() == 0
        assert '<img' in await page.locator('#update-notes').inner_text()
        await expect(page.locator('#update-release-link')).to_be_visible()
        await page.evaluate("state.release.url='https://github.com/Saltybukket/StudyCenter-Public/releases/tag/../../../../evil'")
        await page.locator('#update-auto').check()
        await expect(page.locator('#update-release-link')).to_be_hidden()
        await page.locator('#update-skip').click()
        await expect(page.locator('#update-release')).to_be_visible()
        await expect(page.locator('#update-skipped')).to_be_visible()
        await expect(page.locator('#update-skip')).to_be_hidden()
        await page.evaluate('window.failNext = true')
        await page.locator('#update-download').click()
        await expect(page.locator('#update-error')).to_have_text('Testfehler')
        await page.locator('#update-download').click()
        await expect(page.locator('#update-install-area')).to_be_visible()
        assert await page.locator('#update-install').is_disabled()
        await page.locator('#update-confirm').check()
        await page.evaluate("state.release.version='2.1'; state.skipped_version=null")
        await page.locator('#update-auto').uncheck()
        await expect(page.locator('#update-version')).to_have_text('Version 2.1')
        assert not await page.locator('#update-confirm').is_checked()
        assert await page.locator('#update-install').is_disabled()
        await page.locator('#update-confirm').check()
        await page.locator('#update-install').click()
        await expect(page.locator('#update-status')).to_contain_text('neu gestartet')
        assert await page.locator('#update-error').inner_text() == ''
        assert await page.evaluate("calls.filter(c => c.url.endsWith('/install')).map(c => c.body)") == [{'version':'2.1', 'confirm':True}]
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.evaluate("offline=false; state.state='current'; state.current_version='2.1'; state.outcome={status:'installed', detail:''}")
        await expect(page.locator('#update-outcome')).to_have_text('Das letzte Update wurde installiert.', timeout=5000)
        await expect(page.locator('#update-check')).to_be_enabled()
        await page.locator('#update-check').click()
        await page.locator('#update-download').click()
        await page.locator('#update-confirm').check()
        await page.locator('#update-install').click()
        await expect(page.locator('#update-status')).to_contain_text('neu gestartet')
        await page.evaluate("offline=false; state.state='ready'; state.outcome={status:'cancelled_or_failed', detail:'Abgebrochen'}")
        await expect(page.locator('#update-outcome')).to_contain_text('abgebrochen oder ist fehlgeschlagen', timeout=5000)
        assert not await page.locator('#update-confirm').is_checked()
        assert await page.locator('#update-install').is_disabled()
        await browser.close()


async def test_skipped_release_hides_global_banner(monkeypatch):
    if (ROOT / '.local/browsers').exists():
        monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.local/browsers'))
    js = (CORE / 'static/public-updates.js').read_text(encoding='utf-8')
    async with async_playwright() as driver:
        if not Path(driver.chromium.executable_path).exists():
            pytest.skip('Project Chromium not installed')
        browser = await driver.chromium.launch()
        page = await browser.new_page()
        await page.set_content('<div id="update-banner" hidden><span id="update-banner-text"></span><button id="update-banner-dismiss">Später</button></div>')
        await page.evaluate("window.called = false; window.api = async () => { window.called = true; return {state:'available',release:{version:'2.0'},skipped_version:'2.0'}; }")
        await page.add_script_tag(content=js)
        await page.wait_for_function('window.called')
        await expect(page.locator('#update-banner')).to_be_hidden()
        await browser.close()
