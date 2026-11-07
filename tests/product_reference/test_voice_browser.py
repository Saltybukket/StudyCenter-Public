"""Browser checks use fake capture and responses; no microphone or personal writes."""
import os
from pathlib import Path

import pytest
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
FAKE_CAPTURE = """() => {
  window.capture={stopped:0,requests:[]};
  Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:async()=>({getTracks:()=>[{stop:()=>capture.stopped++}]})}});
  window.MediaRecorder=class {
    static isTypeSupported(){return true;}
    constructor(){this.state='inactive';}
    start(){this.state='recording';}
    stop(){this.state='inactive';this.ondataavailable?.({data:new Blob(['fake audio'])});queueMicrotask(()=>this.onstop?.());}
  };
  const original=window.fetch;
  window.fetch=(url,options)=>url==='/api/voice/transcribe'?new Promise(resolve=>capture.requests.push(data=>resolve(new Response(JSON.stringify(data),{status:200})))):original(url,options);
}"""

@pytest.fixture
async def voice_page(monkeypatch):
    browsers=ROOT/'.local'/'browsers'
    if browsers.exists():monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH',str(browsers))
    async with async_playwright() as driver:
        if not Path(driver.chromium.executable_path).exists():pytest.skip('Project Playwright Chromium not installed')
        browser=await driver.chromium.launch(headless=True)
        page=await browser.new_page()
        html='<meta charset="utf-8"><meta name="csrf-token" content="fixture"><button id="voice-open">Assistant</button>'+(ROOT/'src/studycenter_public/core/templates/voice.html').read_text(encoding='utf-8')+'<script src="/voice.js" defer></script>'
        await page.route('https://campusboard.test/',lambda route:route.fulfill(content_type='text/html',body=html))
        await page.route('**/api/voice/status',lambda route:route.fulfill(json={'engine':'whisper','model':'small','device':'cpu','local_model':True,'connected':False}))
        await page.route('**/voice.js',lambda route:route.fulfill(content_type='text/javascript',body=(ROOT/'src/studycenter_public/core/static/voice.js').read_text(encoding='utf-8')))
        await page.add_init_script("("+FAKE_CAPTURE+")()")
        await page.goto('https://campusboard.test/')
        await page.locator('#voice-open').click()
        yield page
        await browser.close()

async def record(page):
    await page.locator('#voice-record').click()
    await expect(page.locator('#voice-record')).to_have_text('Aufnahme beenden')
    await page.locator('#voice-record').click()
    await expect(page.locator('#voice-record')).to_be_disabled()


async def test_preview_undo_and_more_buttons_send_explicit_requests(voice_page):
    page=voice_page;requests=[]
    async def command(route):
        data=route.request.post_data_json;requests.append(data)
        assert data['preview'] is True
        if data.get('apply_preview'):
            result={'request_id':data['request_id'],'changed':True,'undoable':True,'item_id':1,'message':'Geändert.'}
        elif data.get('more'):
            result={'request_id':data['request_id'],'changed':False,'has_more':False,'message':'Treffer 7–8 von 8.','items':[{'id':7,'title':'Treffer sieben','url':'/items/7'}]}
        elif data['text']=='Suchen':
            result={'request_id':data['request_id'],'changed':False,'has_more':True,'message':'Treffer 1–6 von 8.','items':[]}
        else:
            result={'request_id':data['request_id'],'changed':False,'message':'Noch nichts geändert.',
                    'preview':{'title':'Lerntermin','date':'2030-10-23','time':None,'changes':[{'label':'Datum','before':'2030-10-22','after':'2030-10-23'}]},
                    'dialog':{'waiting':True,'action':{'title':'Lerntermin','date':'2030-10-23'},'options':[]}}
        await route.fulfill(json=result)
    undo=[]
    async def reverse(route):
        undo.append(route.request.post_data_json)
        await route.fulfill(json={'message':'Änderung rückgängig.','request_id':undo[-1]['request_id'],'changed':True,'undone':True})
    await page.route('**/api/voice/command',command)
    await page.route('**/api/voice/undo',reverse)
    await page.locator('#voice-transcript').fill('Verschieben')
    await page.locator('#voice-send').click()
    await expect(page.locator('.voice-preview')).to_contain_text('22.10.2030 → 23.10.2030')
    await expect(page.locator('.voice-preview')).to_contain_text('Ohne Uhrzeit')
    preview_id=requests[0]['request_id']
    await page.get_by_role('button',name='Übernehmen',exact=True).click()
    await expect(page.get_by_role('button',name='Änderung rückgängig',exact=True)).to_be_visible()
    assert requests[-1]['apply_preview'] and requests[-1]['previous_id']==preview_id
    page.on('dialog',lambda dialog:dialog.accept())
    await page.get_by_role('button',name='Änderung rückgängig',exact=True).click()
    await expect(page.locator('#voice-chat')).to_contain_text('Änderung rückgängig.')
    assert undo[-1]=={'request_id':requests[-1]['request_id'],'confirm':True}
    await page.locator('#voice-transcript').fill('Suchen');await page.locator('#voice-send').click()
    await page.get_by_role('button',name='Weitere Treffer',exact=True).click()
    await expect(page.get_by_role('link',name='Treffer sieben',exact=True)).to_be_visible()
    assert requests[-1]['more'] is True


async def test_direct_result_link_undo_and_network_retry_keep_same_uuid(voice_page):
    page=voice_page;requests=[];undos=[]
    async def command(route):
        data=route.request.post_data_json;requests.append(data)
        if len(requests)==1:
            await route.abort()
        else:
            await route.fulfill(json={'request_id':data['request_id'],'changed':True,'created':True,'direct':True,
                'item_id':42,'message':'Eingetragen: Eigene Aufgabe.','items':[{'id':42,'title':'Eigene Aufgabe','url':'/items/42'}]})
    async def reverse(route):
        undos.append(route.request.post_data_json)
        await route.fulfill(json={'request_id':undos[-1]['request_id'],'changed':True,'undone':True,'message':'Erstellung rückgängig.'})
    await page.route('**/api/voice/command',command);await page.route('**/api/voice/undo',reverse)
    await page.locator('#voice-transcript').fill('Erstelle eine Aufgabe für morgen.')
    await page.locator('#voice-send').click()
    await expect(page.locator('#voice-process')).to_contain_text('erneut gesendet')
    await page.locator('#voice-send').click()
    await expect(page.get_by_role('link',name='Eigene Aufgabe',exact=True)).to_be_visible()
    assert len(requests)==2 and requests[0]['request_id']==requests[1]['request_id'] and requests[1]['preview'] is True
    assert await page.get_by_role('button',name='Übernehmen',exact=True).count()==0
    await page.get_by_role('button',name='Rückgängig',exact=True).click()
    await expect(page.locator('#voice-chat')).to_contain_text('Erstellung rückgängig.')
    assert undos==[{'request_id':requests[0]['request_id'],'confirm':True}]


async def test_old_pending_request_keeps_original_execution_semantics(voice_page):
    page=voice_page;seen=[]
    await page.evaluate("sessionStorage.setItem('campusboard.voice.pending',JSON.stringify({request_id:'e296857e-f9fc-4c98-b16f-d36d9e6fa7b0',text:'Legacy retry'}))")
    async def replay(route):
        data=route.request.post_data_json;seen.append(data)
        await route.fulfill(json={'request_id':data['request_id'],'changed':False,'message':'Original receipt.'})
    await page.route('**/api/voice/command',replay)
    await page.reload();await page.locator('#voice-open').click()
    await page.get_by_role('button',name='Dieselbe Anfrage erneut senden',exact=True).click()
    await expect(page.locator('#voice-chat')).to_contain_text('Original receipt.')
    assert seen[0]['preview'] is False and seen[0]['request_id']=='e296857e-f9fc-4c98-b16f-d36d9e6fa7b0'


@pytest.mark.parametrize('fails', [False, True])
async def test_undo_serializes_other_actions_and_releases_controls(voice_page, fails):
    page = voice_page
    requests = []
    async def command(route):
        payload = route.request.post_data_json
        requests.append(payload)
        await route.fulfill(json={'request_id': payload['request_id'], 'changed': False,
                                 'undoable': True, 'message': 'Änderung bereit.'})
    await page.route('**/api/voice/command', command)
    await page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = (url, options) => url === '/api/voice/undo'
        ? new Promise(resolve => window.finishUndo = data => resolve(new Response(JSON.stringify(data.body), {status:data.status})))
        : original(url, options);
    }""")
    await page.locator('#voice-transcript').fill('Änderung')
    await page.locator('#voice-send').click()
    undo = page.get_by_role('button', name='Änderung rückgängig', exact=True)
    await undo.click()
    await page.wait_for_function('typeof window.finishUndo === "function"')
    await expect(page.locator('#voice-send')).to_be_disabled()
    await expect(page.locator('#voice-new-chat')).to_be_disabled()
    await expect(page.locator('#voice-record')).to_be_disabled()
    await page.locator('#voice-transcript').fill('Nächste Eingabe')
    await page.locator('#voice-command-form').evaluate('(form) => form.requestSubmit()')
    assert len(requests) == 1
    await page.evaluate('(data) => finishUndo(data)', {
        'status': 500 if fails else 200,
        'body': {'detail': 'Fixturefehler'} if fails else {
            'request_id': requests[0]['request_id'], 'changed': True, 'undone': True, 'message': 'Rückgängig.'},
    })
    await expect(page.locator('#voice-send')).to_be_enabled()
    await expect(page.locator('#voice-new-chat')).to_be_enabled()
    await expect(page.locator('#voice-record')).to_be_enabled()
    await expect(page.locator('#voice-transcript')).to_have_value('Nächste Eingabe')
    if fails:
        await expect(undo).to_be_enabled()
        await expect(page.locator('#voice-process')).to_contain_text('Fixturefehler')
    else:
        await expect(page.locator('#voice-chat')).to_contain_text('Rückgängig.')

async def reply(page,index,text):
    await page.evaluate('([index,text])=>capture.requests[index]({text,engine:"whisper",model:"small",device:"cpu",latency:1})',[index,text])


async def test_transcription_progress_uses_reported_phase_and_stops_after_result(voice_page):
    page = voice_page
    phases = []
    async def state(route):
        phases.append(True)
        await route.fulfill(json={'stt_busy': True, 'stt_phase': 'Sprache erkennen (GPU)'})
    await page.route('**/api/voice/status', state)
    await record(page)
    await expect(page.locator('#voice-process')).to_contain_text('Sprache erkennen (GPU)')
    await reply(page, 0, 'Probe fertig')
    await expect(page.locator('#voice-process')).to_contain_text('Transkript bereit')
    count = len(phases)
    await page.wait_for_timeout(2200)
    assert len(phases) == count
    await expect(page.locator('#voice-process')).to_contain_text('Transkript bereit')

@pytest.mark.asyncio
async def test_native_hide_releases_capture_without_sending_command(voice_page):
    page=voice_page
    await page.locator('#voice-record').click()
    await expect(page.locator('#voice-record')).to_have_text('Aufnahme beenden')
    await page.evaluate("dispatchEvent(new Event('studycenter-hide'))")
    await expect(page.locator('#voice-record')).to_have_text('Aufnehmen')
    assert await page.evaluate('capture.stopped')>=1
    assert await page.evaluate('capture.requests.length')==0
    await page.locator('#voice-record').click()
    await expect(page.locator('#voice-record')).to_have_text('Aufnahme beenden')

@pytest.mark.asyncio
async def test_transcript_preserves_edits_and_closed_dialog_cannot_overwrite_new_capture(voice_page):
    page=voice_page
    await record(page)
    await page.locator('#voice-transcript').fill('Mein eigener Text')
    await reply(page,0,'Neue Aufnahme')
    await expect(page.locator('#voice-transcript')).to_have_value('Mein eigener Text')
    await expect(page.locator('#voice-draft-text')).to_have_text('Neue Aufnahme')
    await page.locator('#voice-use-draft').click()
    await expect(page.locator('#voice-transcript')).to_have_value('Neue Aufnahme')
    await page.locator('#voice-transcript').fill('')
    await record(page)
    await page.locator('#voice-close').click()
    await page.locator('#voice-open').click()
    await record(page)
    await reply(page,1,'Verspätetes altes Ergebnis')
    await expect(page.locator('#voice-record')).to_be_disabled()
    await expect(page.locator('#voice-transcript')).to_have_value('')
    await reply(page,2,'Aktuelles Ergebnis')
    await expect(page.locator('#voice-transcript')).to_have_value('Aktuelles Ergebnis')
    await expect(page.locator('#voice-record')).to_be_enabled()
    assert await page.evaluate('capture.stopped')>=3

@pytest.mark.asyncio
async def test_command_network_retry_reuses_identity_and_tts_stays_opt_in(voice_page):
    page=voice_page
    requests=[]
    async def command(route):
        requests.append(route.request.post_data_json)
        if len(requests)==1:await route.abort('connectionreset')
        else:await route.fulfill(json={'request_id':requests[-1]['request_id'],'message':'Testantwort','changed':False})
    await page.route('**/api/voice/command',command)
    await page.locator('#voice-transcript').fill('Was muss ich morgen machen?')
    await page.locator('#voice-send').click()
    await expect(page.locator('#voice-process')).to_contain_text('erneut gesendet')
    await page.reload()
    await page.locator('#voice-open').click()
    await expect(page.locator('#voice-pending-text')).to_have_text('Was muss ich morgen machen?')
    await page.locator('#voice-retry').click()
    await expect(page.locator('#voice-chat')).to_contain_text('Testantwort')
    await expect(page.locator('#voice-pending')).to_be_hidden()
    assert len(requests)==2 and requests[0]==requests[1]
    await expect(page.locator('#voice-auto')).not_to_be_checked()


@pytest.mark.asyncio
async def test_unsent_draft_survives_reload_and_new_chat_keeps_it(voice_page):
    page=voice_page
    await page.locator('#voice-transcript').fill('Noch nicht gesendeter Entwurf')
    await page.reload()
    await page.locator('#voice-open').click()
    await expect(page.locator('#voice-transcript')).to_have_value('Noch nicht gesendeter Entwurf')
    await page.route('**/api/voice/command',lambda route:route.fulfill(json={'request_id':route.request.post_data_json['request_id'],'message':'Testantwort','changed':False}))
    await page.locator('#voice-send').click()
    await expect(page.locator('#voice-chat')).to_contain_text('Testantwort')
    await expect(page.locator('#voice-transcript')).to_have_value('')
    await page.locator('#voice-transcript').fill('Neuer eigener Entwurf')
    await page.locator('#voice-new-chat').click()
    await expect(page.locator('#voice-transcript')).to_have_value('Neuer eigener Entwurf')
    await expect(page.locator('#voice-chat')).to_be_empty()
    assert await page.evaluate('sessionStorage.getItem("campusboard.voice.chat")') is None


@pytest.mark.asyncio
async def test_followup_chips_reload_capture_and_cancel_share_context(voice_page):
    page=voice_page;requests=[]
    async def command(route):
        payload=route.request.post_data_json;requests.append(payload)
        if payload.get('cancel'):
            result={'message':'Abgebrochen','cancelled':True,'changed':False}
        else:
            result={'message':'Um wie viel Uhr?','clarify':True,'changed':False,'dialog':{
                'waiting':True,'action':{'title':'CON-Lerntermin','date':'2030-10-14','course':'CON'},
                'options':[{'id':'time:none','label':'Ohne Uhrzeit speichern'}]}}
        await route.fulfill(json=result|{'request_id':payload['request_id']})
    await page.route('**/api/voice/command',command)
    await page.locator('#voice-transcript').fill('Lerntermin morgen')
    await page.locator('#voice-send').click()
    await expect(page.locator('#voice-send')).to_have_text('Antwort an Studi senden')
    await expect(page.locator('#voice-context')).to_contain_text('2030-10-14')
    await expect(page.locator('#voice-transcript')).to_have_value('')
    await page.reload();await page.locator('#voice-open').click()
    await expect(page.locator('#voice-question-text')).to_have_text('Um wie viel Uhr?')
    await page.locator('#voice-other-answer').click()
    await expect(page.locator('#voice-transcript')).to_be_focused()
    await record(page);await reply(page,0,'14 Uhr')
    await expect(page.locator('#voice-transcript')).to_have_value('14 Uhr')
    await page.locator('#voice-send').click()
    await expect(page.locator('#voice-transcript')).to_have_value('')
    assert requests[1]['previous_id']==requests[0]['request_id'] and requests[1]['text']=='14 Uhr'
    await page.get_by_role('button',name='Ohne Uhrzeit speichern',exact=True).click()
    await expect(page.locator('#voice-send')).to_be_enabled()
    assert requests[2]['option_id']=='time:none' and requests[2]['previous_id']==requests[1]['request_id']
    await page.locator('#voice-cancel').click()
    await expect(page.locator('#voice-question')).to_be_hidden()
    await expect(page.locator('#voice-send')).to_have_text('Mit Studi senden')
    assert requests[3]['cancel'] and requests[3]['previous_id']==requests[2]['request_id']
    await page.locator('#voice-transcript').fill('Neuer eigener Termin')
    await page.locator('#voice-send').click()
    await expect(page.locator('#voice-question')).to_be_visible()
    await page.locator('#voice-new-chat').click()
    await expect(page.locator('#voice-question')).to_be_hidden()
    await expect(page.locator('#voice-chat')).to_be_empty()
    assert requests[-1]['cancel']
    await page.reload();await page.locator('#voice-open').click()
    await expect(page.locator('#voice-question')).to_be_hidden()
    await expect(page.locator('#voice-choices')).to_be_empty()
    await expect(page.locator('#voice-auto')).not_to_be_checked()
    await page.set_viewport_size({'width':390,'height':844})
    assert await page.locator('#voice-panel').evaluate('(el)=>el.getBoundingClientRect().right<=innerWidth')


async def test_voice_focus_survives_reload_and_open_view_navigates(voice_page):
    page=voice_page
    async def command(route):
        data=route.request.post_data_json
        result={'request_id':data['request_id'],'changed':False,'message':'Eintrag gefunden.','focus_item':{'id':42,'title':'Persönliche Lernprobe','url':'/items/42'}}
        if data['text']=='Öffne den Kalender':result.update(navigate='/calendar',message='Kalender öffnen.')
        await route.fulfill(json=result)
    await page.route('**/api/voice/command',command)
    await page.route('https://campusboard.test/calendar',lambda route:route.fulfill(content_type='text/html',body='<h1>Kalender-Testansicht</h1>'))
    await page.locator('#voice-transcript').fill('Finde Lernprobe')
    await page.locator('#voice-send').click()
    await expect(page.locator('#voice-focus')).to_contain_text('Persönliche Lernprobe')
    await page.reload();await page.locator('#voice-open').click()
    await expect(page.locator('#voice-focus')).to_contain_text('Persönliche Lernprobe')
    await page.locator('#voice-new-chat').click()
    await expect(page.locator('#voice-focus')).to_be_hidden()
    await page.locator('#voice-transcript').fill('Öffne den Kalender')
    await page.locator('#voice-send').click()
    await expect(page).to_have_url('https://campusboard.test/calendar')
    await expect(page.locator('h1')).to_have_text('Kalender-Testansicht')


async def test_bulk_preview_lists_every_target_and_examples_only_fill_draft(voice_page):
    page=voice_page;requests=[]
    async def command(route):
        data=route.request.post_data_json;requests.append(data)
        if data.get('apply_preview'):
            result={'request_id':data['request_id'],'changed':True,'undoable':True,'message':'18 Einträge geändert.'}
        else:
            result={'request_id':data['request_id'],'changed':False,'message':'Noch nichts geändert.',
                    'preview':{'title':'18 Einträge','items':[
                        {'id':n,'title':f'Vorlesung {n}','course':'Physik','date':'2026-11-02',
                         'status':'open','pinned':False,'url':f'/items/{n}'} for n in range(1,19)],
                        'changes':[{'label':'Bearbeitungsstatus','before':None,'after':'done'}]}}
        await route.fulfill(json=result)
    await page.route('**/api/voice/command',command)
    await page.locator('.voice-tools > summary').click()
    await page.get_by_role('button',name='Offene Aufgaben',exact=True).click()
    await expect(page.locator('#voice-transcript')).to_have_value('Zeige meine offenen Aufgaben')
    assert requests==[]
    await expect(page.locator('#voice-item-tools')).to_be_hidden()
    await page.locator('#voice-transcript').fill('Markiere alle Vorlesungen bis heute als erledigt')
    await page.locator('#voice-send').click()
    await expect(page.locator('.voice-bulk-items a')).to_have_count(18)
    await expect(page.locator('.voice-bulk-items')).to_contain_text('Vorlesung 18')
    assert len(requests)==1 and requests[0]['preview'] is True
    await page.get_by_role('button',name='18 Einträge ändern',exact=True).click()
    await expect(page.locator('#voice-chat')).to_contain_text('18 Einträge geändert.')
    assert requests[1]['apply_preview'] is True and requests[1]['previous_id']==requests[0]['request_id']

async def test_compact_luna_keeps_background_interactive_and_fits_mobile(voice_page):
    page = voice_page
    await page.add_style_tag(content=(ROOT/'src/studycenter_public/core/static/voice-panel.css').read_text(encoding='utf-8'))
    await page.set_viewport_size({'width': 1280, 'height': 800})
    await page.evaluate("""() => {
      const button = document.createElement('button'); button.id = 'working-view';
      button.textContent = 'Arbeitsansicht'; button.onclick = () => button.textContent = 'Anklickbar';
      document.body.prepend(button);
    }""")
    panel = page.locator('#voice-panel')
    assert not await panel.evaluate("el => el.matches(':modal')")
    await expect(page.locator('#voice-open')).to_have_attribute('aria-expanded', 'true')
    box = await panel.bounding_box()
    assert 340 <= box['width'] <= 380 and box['x'] > 800
    assert box['y'] == 96 and box['height'] <= 680
    await page.locator('#working-view').click()
    await expect(page.locator('#working-view')).to_have_text('Anklickbar')
    await page.locator('#voice-chat').evaluate("el => { el.textContent = 'Längere Antwort. '.repeat(1000); }")
    await expect(page.locator('#voice-transcript')).to_be_visible()
    assert (await panel.bounding_box())['height'] <= 680
    assert await page.locator('.voice-body').evaluate('el => el.scrollHeight > el.clientHeight')
    await page.set_viewport_size({'width': 390, 'height': 700})
    box = await panel.bounding_box()
    assert box['x'] >= 0 and box['x'] + box['width'] <= 390
    await expect(page.locator('#voice-transcript')).to_be_visible()
    await expect(page.locator('#voice-send')).to_be_visible()
    await page.locator('#voice-close').click()
    await expect(page.locator('#voice-open')).to_have_attribute('aria-expanded', 'false')
    await expect(page.locator('#voice-open')).to_be_focused()

async def test_open_on_item_uses_visible_item_context_and_new_chat_clears_it(voice_page):
    page = voice_page
    requests = []
    async def command(route):
        requests.append(route.request.post_data_json)
        await route.fulfill(json={'request_id': requests[-1]['request_id'], 'message': 'Antwort', 'changed': False})
    await page.route('**/api/voice/command', command)
    await page.locator('#voice-close').click()
    await page.evaluate("""() => {
      history.pushState({}, '', '/items/42');
      const button = document.createElement('button');
      button.dataset.voiceItem = '42'; button.dataset.voiceTitle = 'Testaufgabe';
      document.body.append(button);
    }""")
    await page.locator('#voice-open').click()
    await expect(page.locator('#voice-focus')).to_contain_text('Testaufgabe')
    await page.locator('#voice-transcript').fill('Was ist zu tun?')
    await page.locator('#voice-send').click()
    await expect(page.locator('#voice-process')).to_have_text('Bereit.')
    assert requests[0]['context_item_id'] == 42 and requests[0]['preview'] is True
    await page.locator('#voice-new-chat').click()
    await page.locator('#voice-close').click()
    await page.locator('#voice-open').click()
    await page.locator('#voice-transcript').fill('Allgemeine Frage')
    await page.locator('#voice-send').click()
    await expect(page.locator('#voice-process')).to_have_text('Bereit.')
    assert 'context_item_id' not in requests[1]
