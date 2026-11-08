import asyncio
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright, expect

from studycenter_public.core.app import create_app
from .reference_settings import Settings
from studycenter_public.core.db import encode, now


async def test_review_deep_links_require_click_for_new_suggestion(tmp_path,monkeypatch):
    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH',str(Path('.local/browsers').resolve()))
    app=create_app(Settings(data_dir=tmp_path));client=TestClient(app)
    quest={'id':'q','title':'Lernweg','state':'active','planning':'checklist','sources_changed':True,
           'sources':[],'steps':[],'version':1,'pending_revision':None}
    requests=[]
    async with async_playwright() as p:
        if not Path(p.chromium.executable_path).exists():pytest.skip('Project browser unavailable')
        browser=await p.chromium.launch(headless=True);page=await browser.new_page()
        async def serve(route):
            path=urlsplit(route.request.url).path
            if path=='/api/quests':
                await route.fulfill(json={'today':'2030-04-03','quests':[quest]})
            elif path=='/api/quests/q/suggest':
                requests.append(('suggest',route.request.method))
                await route.fulfill(json={'revision':{'id':'r','kind':'source','version':1,'summary':'Geprüft','changes':[]}})
            elif path=='/api/quests/q/revision':
                requests.append(('revision',route.request.method))
                await route.fulfill(json={'revision':{'id':'r','kind':'source','version':1,'summary':'Gespeicherter Vorschlag','changes':[]}})
            else:
                response=client.get(path)
                await route.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','application/octet-stream'))
        await page.route('**/*',serve)
        await page.goto('http://studycenter_public.core.test/quests?quest=q&review=sources',wait_until='networkidle')
        dialog=page.locator('#quest-revision-dialog')
        await expect(dialog.get_by_role('button',name='Mit Studi prüfen')).to_be_visible()
        assert not requests
        await dialog.get_by_role('button',name='Mit Studi prüfen').click()
        await expect(dialog.get_by_text('Keine fachliche Anpassung vorgeschlagen. Dein Plan kann so bleiben.')).to_be_visible()
        assert requests==[('suggest','POST')]
        quest['pending_revision']={'id':'r','kind':'source'};requests.clear()
        await page.goto('http://studycenter_public.core.test/quests?quest=q&review=revision',wait_until='networkidle')
        await expect(dialog.get_by_text('Keine fachliche Anpassung vorgeschlagen. Dein Plan kann so bleiben.')).to_be_visible()
        assert requests==[('revision','GET')]
        await browser.close()


async def test_revision_preview_select_apply_undo_and_active_edit(tmp_path,monkeypatch):
    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH',str(Path('.local/browsers').resolve()))
    app=create_app(Settings(data_dir=tmp_path));client=TestClient(app);db=app.state.db
    steps=[dict(id=f's{i}',title=f'OPV Schritt {i}',detail='Spannungsverstärkung berechnen und Ergebnis prüfen.',
        topic='Gegenkopplung',minutes=20,item_id=None,day_offset=i,done=i==0,completed_at=now() if i==0 else None,edited_fields=[]) for i in range(3)]
    db.execute('''INSERT INTO quests(id,request_id,input_hash,title,mode,horizon,daily_minutes,start_date,end_date,state,steps,created_at,updated_at)
        VALUES('q','request','hash','Operationsverstärker verstehen','custom',7,30,'2020-01-01','2020-01-07','active',?,?,?)''',(encode(steps),now(),now()))
    release=asyncio.Event();started=asyncio.Event();delay=True
    async with async_playwright() as p:
        if not Path(p.chromium.executable_path).exists():pytest.skip('Project browser unavailable')
        browser=await p.chromium.launch(headless=True)
        page=await browser.new_page(viewport={'width':390,'height':844})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        async def serve(route):
            nonlocal delay
            url=urlsplit(route.request.url)
            assert url.hostname=='studycenter_public.core.test'
            if url.path.endswith('/apply-revision') and delay:
                started.set();await release.wait();delay=False
            response=client.request(route.request.method,url.path+('?' + url.query if url.query else ''),
                content=route.request.post_data,headers={'X-CSRF-Token':app.state.csrf,'Content-Type':'application/json'})
            await route.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','application/octet-stream'))
        await page.route('**/*',serve)
        await page.goto('http://studycenter_public.core.test/quests',wait_until='networkidle')
        await page.get_by_role('button',name='Ab heute weiterplanen',exact=True).click()
        dialog=page.locator('#quest-revision-dialog')
        await expect(dialog.locator('input[name=revision-choice]')).to_have_count(2)
        assert db.one("SELECT version FROM quests WHERE id='q'")['version']==1
        await dialog.get_by_role('button',name='Auswahl übernehmen').click()
        await asyncio.wait_for(started.wait(),5)
        await page.keyboard.press('Escape')
        await expect(dialog).to_be_visible()  # do not abandon an in-flight mutation
        release.set()
        await expect(dialog.get_by_text('Anpassung gespeichert.',exact=True)).to_be_visible()
        assert db.one("SELECT version FROM quests WHERE id='q'")['version']==2
        await dialog.get_by_role('button',name='Rückgängig',exact=True).click()
        await expect(dialog.get_by_text('Der vorherige Plan ist wiederhergestellt.')).to_be_visible()
        await dialog.locator('#quest-revision-actions').get_by_role('button',name='Schließen',exact=True).click()
        await page.get_by_role('button',name='Lernweg öffnen',exact=True).click()
        path=page.locator('#quest-path-dialog')
        await path.get_by_role('button',name='Bearbeiten',exact=True).nth(1).click()
        edit=page.locator('#quest-step-dialog')
        await edit.locator('[name=detail]').fill('Meine eigene Aufgabe: Verstärkung 10 selbst herleiten.')
        await edit.get_by_role('button',name='Schritt speichern').click()
        await expect(edit).not_to_be_visible()
        await expect(path.get_by_text('Meine eigene Aufgabe: Verstärkung 10 selbst herleiten.',exact=True)).to_be_visible()
        q=app.state.quests.dashboard()['quests'][0]
        assert q['steps'][0]['done'] and q['steps'][0]['completed_at']==steps[0]['completed_at']
        assert 'detail' in q['steps'][1]['edited_fields']
        for width in (320,390,1440):
            await page.set_viewport_size({'width':width,'height':900})
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert await path.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
        assert not errors
        await browser.close()
