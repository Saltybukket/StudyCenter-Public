from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studycenter_public.core.app import create_app
from .reference_settings import Settings
from studycenter_public.core.documents import DocumentStore
from studycenter_public.core.models import Candidate


async def test_wall_status_actions_refill_page_and_reopen(tmp_path, monkeypatch):
    from playwright.async_api import async_playwright, expect

    root = Path(__file__).resolve().parents[2]
    if (root / '.local/browsers').exists():
        monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(root / '.local/browsers'))
    app = create_app(Settings(data_dir=tmp_path))
    db = app.state.db
    cid = db.execute("INSERT INTO courses(title,semester) VALUES('Browserkurs','2030W')")
    sid = db.execute("INSERT INTO sources(course_id,kind,title,url) VALUES(?,'public','Quelle','https://example.org')", (cid,))
    document = DocumentStore(db, app.state.settings).save(sid, 'https://example.org/a', 'Plan', 'text/plain', b'Plan')
    doc = db.one('SELECT * FROM documents WHERE id=?', (document,))
    ids = [db.ingest(doc, Candidate(title=f'Browserprobe {n:02}', kind='task', quote='Beleg',
        page=1, anchor=str(n), identity_key=str(n), identity_scope='course')) for n in range(25)]
    client = TestClient(app, base_url='http://127.0.0.1')
    posts = []
    async with async_playwright() as driver:
        if not Path(driver.chromium.executable_path).exists():
            pytest.skip('Project Chromium not installed')
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page()
        async def routes(route):
            request = route.request
            path = request.url.split('127.0.0.1', 1)[1]
            if path.startswith('/static/'):
                asset = root / 'src/studycenter_public/core/static' / path.split('?', 1)[0].split('/')[-1]
                await route.fulfill(content_type={'.js':'text/javascript','.css':'text/css','.webp':'image/webp'}.get(asset.suffix,'application/octet-stream'), body=asset.read_bytes())
            else:
                response = client.request(request.method, path, content=request.post_data_buffer,
                                          headers=request.headers, follow_redirects=True)
                if request.method == 'POST' and path == f'/items/{ids[0]}':
                    posts.append(response.status_code)
                await route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)
        await page.route('**/*', routes)
        await page.goto(f'http://127.0.0.1/board?course={cid}&q=Browserprobe')
        cards = page.locator('.postit-board [data-item-id]')
        await expect(cards).to_have_count(24)
        assert not await page.locator(f'.postit-board [data-item-id="{ids[24]}"]').count()
        card = page.locator(f'.postit-board [data-item-id="{ids[0]}"]')
        for pinned in (True, False):
            async with page.expect_navigation():
                await card.locator('.postit-pin').click()
            await expect(card.locator('.postit-pin')).to_have_attribute('aria-pressed', str(pinned).lower())
            assert db.one('SELECT pinned FROM items WHERE id=?', (ids[0],))['pinned'] == int(pinned)
            if pinned:
                await expect(page.locator('[data-work-status=pinned] [data-item-id]').first).to_have_attribute('data-item-id', str(ids[0]))
        async with page.expect_navigation():
            await card.locator('[data-status-choice=doing]').click()
        await expect(card.locator('[data-status-choice=doing]')).to_have_attribute('aria-pressed','true')
        assert db.one('SELECT status FROM items WHERE id=?', (ids[0],))['status'] == 'doing'
        assert f'course={cid}' in page.url and 'q=Browserprobe' in page.url
        async with page.expect_navigation():
            await card.get_by_role('button', name='Erledigt: Browserprobe 00').click()
        await expect(page.locator(f'.postit-board [data-item-id="{ids[0]}"]')).to_have_count(0)
        await expect(page.locator(f'.postit-board [data-item-id="{ids[24]}"]')).to_have_count(1)
        await expect(cards).to_have_count(24)
        assert db.one('SELECT status FROM items WHERE id=?', (ids[0],))['status'] == 'done'
        await page.locator('.wall-views a[href*="/archive"]').click()
        archived = page.locator(f'.entries-list [data-item-id="{ids[0]}"]')
        await expect(archived).to_be_visible()
        async with page.expect_navigation():
            await archived.get_by_role('button', name='Wieder öffnen').click()
        await expect(archived).to_have_count(0)
        assert db.one('SELECT status FROM items WHERE id=?', (ids[0],))['status'] == 'open'
        assert posts == [200, 200, 200, 200, 200]
        await browser.close()
