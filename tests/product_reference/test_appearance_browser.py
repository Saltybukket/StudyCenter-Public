from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright, expect

from studycenter_public.core.app import create_app
from .reference_settings import Settings


async def test_appearance_survives_navigation_without_changing_board_material(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    if (root / '.local/browsers').exists():
        monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(root / '.local/browsers'))
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        async with async_playwright() as driver:
            if not Path(driver.chromium.executable_path).exists():
                pytest.skip('Project Chromium not installed')
            browser = await driver.chromium.launch()
            page = await browser.new_page(viewport={'width': 1920, 'height': 1000})
            async def serve(route):
                url = urlsplit(route.request.url)
                assert url.hostname == 'studycenter_public.core.test'
                assert route.request.method == 'GET'
                response = client.get(url.path + ('?' + url.query if url.query else ''))
                await route.fulfill(status=response.status_code, body=response.content,
                                    content_type=response.headers.get('content-type', 'application/octet-stream'))
            await page.route('**/*', serve)
            for theme in ('sand', 'sage', 'lavender', 'dark', 'light'):
                await page.goto('http://studycenter_public.core.test/settings?section=display')
                await page.locator(f'[data-theme-choice][value={theme}]').check()
                await page.goto('http://studycenter_public.core.test/overview')
                await expect(page.locator('html')).to_have_attribute('data-theme', theme)
                assert (await page.locator('.layout > aside').bounding_box())['x'] == 0
            await page.goto('http://studycenter_public.core.test/settings?section=display')
            await page.locator('[data-theme-choice][value=system]').check()
            await page.emulate_media(color_scheme='dark')
            await expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
            # Material swatches and wall colors must retain the selected material.
            await page.goto('http://studycenter_public.core.test/board')
            wall = page.locator('.postit-board,.magnet-wall').first
            # Empty boards do not render a wall: use the same production class.
            if not await wall.count():
                await page.locator('main').evaluate("el=>{const wall=document.createElement('div');wall.className='postit-board';el.append(wall)}")
            for material in ('cork', 'slate', 'plain'):
                values = []
                for theme in ('light', 'dark'):
                    await page.evaluate('([material,theme])=>{document.documentElement.dataset.wallStyle=material;document.documentElement.dataset.theme=theme}', [material, theme])
                    values.append(await wall.evaluate('(el)=>{const s=getComputedStyle(el);return [s.backgroundImage,s.backgroundColor,s.borderTopWidth,s.borderTopColor]}'))
                if material == 'plain':
                    assert values[0][1] != values[1][1]
                    assert all(value[2] == '2px' for value in values)
                else:
                    assert values[0] == values[1]
            await page.goto('http://studycenter_public.core.test/settings?section=display')
            await page.locator('[data-chat-layout-choice][value=sidebar]').check()
            for width in (1920, 390):
                await page.set_viewport_size({'width': width, 'height': 844})
                await page.locator('#voice-open').click()
                await page.wait_for_function("document.querySelector('#voice-panel').getBoundingClientRect().top >= document.querySelector('.masthead').getBoundingClientRect().bottom")
                assert (await page.locator('.masthead').bounding_box())['width'] == width
                chat = await page.locator('#voice-panel').bounding_box()
                assert chat['y'] + chat['height'] <= 845
                await page.locator('#voice-close').click()
            await browser.close()
