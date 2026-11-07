from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

from studycenter_public.core.app import create_app
from .reference_settings import Settings


async def test_quests_render_toggle_failure_and_mobile(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    if (root / '.local/browsers').exists():
        monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(root / '.local/browsers'))
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    quest = {'id': '8a59a287-c741-40be-98d6-544b14aaedbb', 'title': 'Lineare Algebra', 'mode': 'custom', 'horizon': 7,
             'daily_minutes': 60, 'start_date': '2026-11-02', 'end_date': '2026-11-08',
             'state': 'active', 'sources': [], 'version': 1, 'completed_count': 0,
             'total_count': 1, 'progress': 0, 'total_minutes': 30,
             'steps': [{'id': '7dedf381-f826-44bf-a57c-096a14bcb641', 'title': 'Matrix üben', 'detail': '', 'day_offset': 0,
                        'minutes': 30, 'item_id': None, 'done': False, 'completed_at': None,
                        'scheduled_date': '2026-11-02'}]}
    quest['steps'].extend(quest['steps'][0] | {'id': f'next-{i}', 'title': f'Etappe {i}'} for i in range(1, 4))
    quest['steps'][0]['topic'] = 'Grundlagen'
    quest['steps'][1]['topic'] = 'Grundlagen'
    quest['steps'][2]['topic'] = 'Anwenden'
    quest['steps'][3]['topic'] = 'Anwenden'
    quest['total_count'] = 4
    quest['sources'] = [{'id': 7, 'title': 'Aufgabe eins', 'course': 'Algebra'}, {'id': 8, 'title': 'Aufgabe zwei', 'course': 'Analysis'}, {'id': 7, 'title': 'Duplikat', 'course': 'Algebra'}, {'id': 9, 'title': 'Aufgabe drei', 'course': 'Logik'}]
    draft = quest | {'id': 'draft-id', 'title': 'Entwurf', 'state': 'draft',
                     'steps': [quest['steps'][0] | {'id': 'draft-step', 'detail': 'Beschreibung'}]}
    archived = quest | {'id': 'archived-id', 'title': 'Alte Quest', 'state': 'archived'}
    fail_step = True
    fail_reload = False
    attempts = []
    activations = []
    async with async_playwright() as driver:
        if not Path(driver.chromium.executable_path).exists():
            pytest.skip('Project Chromium not installed')
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 390, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        async def serve(route):
            path = urlsplit(route.request.url).path
            if path == '/api/quests' and route.request.method == 'GET':
                if fail_reload:
                    await route.fulfill(status=503, json={'detail': 'Nicht erreichbar'})
                else:
                    await route.fulfill(json={'today': '2026-11-02', 'quests': [quest, draft, archived], 'xp': 0,
                                              'level': 1, 'level_progress': 0, 'today_minutes': 30})
            elif path == '/api/quests/preview':
                attempts.append(route.request.post_data_json)
                await route.fulfill(status=500, json={'detail': 'Zeitüberschreitung'})
            elif path == '/api/quests/draft-id/activate':
                activations.append(route.request.post_data_json)
                draft.update(state='active', title=activations[-1]['title'], version=2)
                draft['steps'][0].update(activations[-1]['steps'][0])
                await route.fulfill(json={'quest': draft})
            elif path == '/api/quests/options':
                await route.fulfill(json={'items': [{'id': 7, 'title': 'Analysis Übung', 'course': 'Mathematik', 'kind': 'task', 'deadline': None, 'date_conflict': False}, {'id': 8, 'title': 'Prüfung', 'course': 'Mathematik', 'kind': 'exam', 'deadline': None, 'date_conflict': False}], 'model_ready': True})
            elif path == '/api/quests/8a59a287-c741-40be-98d6-544b14aaedbb/step':
                assert route.request.post_data_json['step_id'] == '7dedf381-f826-44bf-a57c-096a14bcb641'
                if fail_reload:
                    await route.fulfill(status=500, json={'detail': 'Nicht erreichbar'})
                elif fail_step:
                    assert route.request.post_data_json['version'] == 1
                    quest['version'] = 2
                    await route.fulfill(status=409, json={'detail': 'Quest wurde zwischenzeitlich geändert'})
                else:
                    assert route.request.post_data_json['version'] == 2
                    quest['steps'][0].update(done=True, completed_at='2026-11-02T10:00:00+00:00')
                    quest.update(version=3, completed_count=1, progress=100)
                    await route.fulfill(json={'quest': quest, 'xp_delta': 35})
            else:
                assert route.request.method == 'GET'
                response = client.get(path)
                await route.fulfill(status=response.status_code, body=response.content,
                                    content_type=response.headers.get('content-type', 'application/octet-stream'))

        await page.route('**/*', serve)
        response = await page.goto('http://localhost/quests?quest=8a59a287-c741-40be-98d6-544b14aaedbb')
        assert response.status == 200
        await page.locator('#quest-path-dialog').wait_for(state='visible')
        assert await page.locator('#quest-path-title').text_content() == 'Lineare Algebra'
        await page.locator('#quest-path-dialog [data-close-dialog]').click()
        await page.goto('http://localhost/quests?quest=missing')
        await page.get_by_role('status').filter(has_text='nicht mehr verfügbar').wait_for()
        assert not await page.locator('#quest-path-dialog').is_visible()
        await page.goto('http://localhost/quests')
        assert await page.get_by_role('heading', name='Upcoming').is_visible()
        draft_title = page.locator('[data-quest-card=draft-id] [name=title]')
        await draft_title.fill('Mein bearbeiteter Entwurf')
        await page.locator('[data-quest-card=draft-id]').get_by_role('button', name='Bearbeiten').click()
        await page.locator('#quest-step-form [name=step_title]').fill('Bearbeiteter Schritt')
        await page.locator('#quest-step-form [name=topic]').fill('Meine Grundlagen')
        await page.get_by_role('button', name='Schritt speichern').click()
        await page.locator('[data-quest-card=draft-id]').get_by_role('button', name='Lernweg ansehen').click()
        assert await page.locator('#quest-path-body').get_by_role('heading', name='Meine Grundlagen').is_visible()
        assert await page.locator('#quest-path-body').get_by_text('Bearbeiteter Schritt').is_visible()
        assert await page.locator('#quest-path-body input[type=checkbox]').first.is_disabled()
        await page.keyboard.press('Escape')
        archive = page.locator('.quest-archive')
        assert await archive.locator('summary').first.text_content() == 'Archiv (1)'
        assert not await archive.locator('[data-quest-card=archived-id]').is_visible()
        assert await page.locator('#quest-level').get_by_text('0 / 100 XP zum nächsten Level').is_visible()
        assert await page.locator('#quest-today input[type=checkbox]').count() == 3
        await page.get_by_role('button', name='Alle nächsten Schritte (4)').click()
        assert await page.locator('#quest-today input[type=checkbox]').count() == 4
        await page.locator('[data-quest-card="8a59a287-c741-40be-98d6-544b14aaedbb"]').get_by_role('button', name='Lernweg öffnen').click()
        assert await page.locator('#quest-path-body input[type=checkbox]').count() == 4
        assert await page.locator('#quest-path-body .quest-topic-heading').all_text_contents() == ['Grundlagen', 'Anwenden']
        assert await page.locator('#quest-path-body a[href="/items/7"]').count() == 1
        assert await page.locator('#quest-path-body a[href="/items/8"]').count() == 1
        assert await page.locator('#quest-path-body .quest-source-links a').count() == 3
        assert await page.locator('[data-quest-card="8a59a287-c741-40be-98d6-544b14aaedbb"] .quest-meta').first.text_content() == 'Algebra · Analysis · 1 weitere'
        assert await page.locator('.quest-add-card').is_visible()
        path_check = page.locator('#quest-path-body input[type=checkbox]').first
        await path_check.focus()
        await path_check.press('Space')
        await page.locator('#quest-path-message').get_by_text('Der aktuelle Stand wurde geladen', exact=False).wait_for()
        assert await path_check.evaluate('(e) => document.activeElement === e')
        await page.locator('#quest-path-dialog [data-close-dialog]').click()
        check = page.locator('#quest-today input[type=checkbox]').first
        assert not await check.is_checked()
        assert await draft_title.input_value() == 'Mein bearbeiteter Entwurf'
        assert await page.locator('[data-quest-card=draft-id]').get_by_text('Bearbeiteter Schritt').is_visible()
        assert await page.locator('#quest-today').get_by_text('Matrix üben').is_visible()
        fail_step = False
        await check.click()
        await page.get_by_role('status').filter(has_text='+35 XP').wait_for()
        assert await page.locator('#quest-today input[type=checkbox]').first.is_checked()
        assert await page.locator('#quest-today input[type=checkbox]').first.evaluate('(e) => document.activeElement === e')
        assert await draft_title.input_value() == 'Mein bearbeiteter Entwurf'
        fail_reload = True
        await page.locator('#quest-today input[type=checkbox]').first.click()
        await page.get_by_role('status').filter(has_text='konnte nicht geladen werden').wait_for()
        assert await page.locator('#quest-today input[type=checkbox]').first.is_checked()
        fail_reload = False
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.get_by_role('button', name='+ Neue Quest', exact=True).click()
        assert await page.get_by_role('dialog').is_visible()
        await page.locator('input[name=mode][value=auto]').press('Enter')
        assert await page.locator('[data-wizard-pane=schedule]').is_visible()
        assert not await page.locator('#quest-planning-options').is_visible()
        assert await page.locator('input[name=planning][value=paced]').is_checked()
        assert await page.locator('#quest-wizard-progress').text_content() == '2 · Dein Rhythmus'
        assert await page.locator('[data-wizard-pane=schedule] h3').evaluate('(e) => document.activeElement === e')
        await page.get_by_role('button', name='Zurück').click()
        await page.locator('input[name=mode][value=custom]').check()
        await page.get_by_role('button', name='Weiter', exact=True).click()
        await page.get_by_role('button', name='Weiter', exact=True).click()
        assert await page.locator('#quest-form-message').get_by_text('Bitte beschreibe').is_visible()
        await page.locator('[name=goal]').fill('Ein eigenes Thema')
        await page.locator('[name=goal]').press('Enter')
        assert await page.locator('[name=goal]').input_value() == 'Ein eigenes Thema\n'
        await page.get_by_role('button', name='Weiter', exact=True).click()
        await page.get_by_role('button', name='Zurück').click()
        assert await page.locator('[name=goal]').input_value() == 'Ein eigenes Thema\n'
        await page.get_by_role('button', name='Zurück').click()
        await page.locator('input[name=mode][value=item]').check()
        await page.get_by_role('button', name='Weiter', exact=True).click()
        await page.locator('#quest-item-search').fill('Mathematik')
        await page.locator('#quest-item-results [data-item="7"]').click()
        await page.locator('#quest-item-search').press('Enter')
        assert await page.locator('[data-wizard-pane=schedule] h3').evaluate('(e) => document.activeElement === e')
        assert await page.locator('input[name=planning][value=checklist]').is_checked()
        assert not await page.locator('#quest-paced-options').is_visible()
        assert await page.locator('#quest-checklist-explanation').is_visible()
        await page.locator('input[name=planning][value=paced]').check()
        assert await page.locator('#quest-paced-options').is_visible()
        await page.locator('input[name=planning][value=checklist]').check()
        assert not await page.locator('#quest-paced-options').is_visible()
        await page.get_by_role('button', name='Zurück').click()
        assert await page.locator('#quest-item-selected').text_content() == 'Ausgewählt: Analysis Übung'
        assert await page.locator('#quest-item-pane h3').evaluate('(e) => document.activeElement === e')
        await page.locator('#quest-item-results [data-item="8"]').click()
        await page.get_by_role('button', name='Weiter', exact=True).click()
        assert not await page.locator('#quest-planning-options').is_visible()
        assert await page.locator('input[name=planning][value=paced]').is_checked()
        await page.get_by_role('button', name='Zurück').click()
        await page.locator('#quest-item-results [data-item="7"]').click()
        await page.get_by_role('button', name='Weiter', exact=True).click()
        assert await page.locator('input[name=planning][value=checklist]').is_checked()
        await page.get_by_role('button', name='Mit Studi planen').click()
        await page.locator('#quest-form-message').get_by_text('erneut versuchen', exact=False).wait_for()
        await page.locator('#quest-dialog [data-close-dialog]').click()
        await page.get_by_role('button', name='+ Neue Quest', exact=True).click()
        await page.get_by_role('button', name='Mit Studi planen').click()
        await page.wait_for_function('document.querySelector("#quest-plan").disabled === false')
        assert len(attempts) == 2 and attempts[0] == attempts[1]
        assert attempts[0]['request_id'] and attempts[0]['item_id'] == 7 and attempts[0]['goal'] == '' and attempts[0]['planning'] == 'checklist'
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors
        await page.keyboard.press('Escape')
        await page.locator('[data-quest-card=draft-id]').get_by_role('button', name='Quest starten').click()
        await page.get_by_text('Deine Quest ist gestartet.', exact=True).wait_for()
        assert activations[0]['steps'][0]['topic'] == 'Meine Grundlagen'
        await page.locator('[data-quest-card=draft-id]').get_by_role('button', name='Lernweg öffnen').click()
        assert await page.locator('#quest-path-body').get_by_role('heading', name='Meine Grundlagen').is_visible()
        await browser.close()



async def test_checklist_has_no_artificial_schedule_and_next_step_advances(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    if (root / '.local/browsers').exists():
        monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(root / '.local/browsers'))
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    steps = [{'id': f'step-{i}', 'title': f'Aufgabe {i}', 'topic': 'Arbeitsblatt', 'detail': 'Bearbeiten',
              'day_offset': 0, 'minutes': 15, 'item_id': 7, 'done': False, 'completed_at': None,
              'scheduled_date': None} for i in range(1, 4)]
    quest = {'id': 'check-id', 'title': 'Übungsblatt', 'mode': 'item', 'planning': 'checklist',
             'horizon': 7, 'daily_minutes': 60, 'start_date': '2026-11-02', 'end_date': '2026-11-08',
             'state': 'active', 'sources': [{'id': 7, 'title': 'Übungsblatt', 'course': 'Mathematik',
                                               'deadline': '2026-11-12', 'date_conflict': False}],
             'version': 1, 'steps': steps, 'completed_count': 0, 'total_count': 3,
             'progress': 0, 'total_minutes': 45, 'warnings': ['Unterlagen nicht vollständig']}
    draft = quest | {'id': 'draft-check', 'title': 'Entwurf', 'state': 'draft', 'steps': [dict(steps[0])]}
    async with async_playwright() as driver:
        if not Path(driver.chromium.executable_path).exists():
            pytest.skip('Project Chromium not installed')
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 390, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        async def serve(route):
            path = urlsplit(route.request.url).path
            if path == '/api/quests' and route.request.method == 'GET':
                await route.fulfill(json={'today': '2026-11-02', 'quests': [quest, draft], 'xp': 0,
                                          'level': 1, 'level_progress': 0, 'today_minutes': 0})
            elif path == '/api/quests/check-id/step':
                payload = route.request.post_data_json
                assert payload['step_id'] == 'step-1' and payload['version'] == 1 and payload['done'] is True
                steps[0].update(done=True, completed_at='2026-11-02T10:00:00+00:00')
                quest.update(version=2, completed_count=1, progress=33)
                await route.fulfill(json={'quest': quest, 'xp_delta': 10})
            else:
                assert route.request.method == 'GET'
                response = client.get(path)
                await route.fulfill(status=response.status_code, body=response.content,
                                    content_type=response.headers.get('content-type', 'application/octet-stream'))

        await page.route('**/*', serve)
        await page.goto('http://localhost/quests')
        today = page.locator('#quest-today')
        await today.locator('input[type=checkbox]').wait_for()
        assert await today.locator('input[type=checkbox]').count() == 1
        assert await today.get_by_text('Aufgabe 1').is_visible()
        assert await today.get_by_text('Freiwilliger Einstieg', exact=False).is_visible()
        assert await page.locator('#quest-today-time').text_content() == '0 Min. geplant'
        card = page.locator('[data-quest-card=check-id]')
        assert await card.get_by_text('In deinem Tempo · Frist 12.11.2026').is_visible()
        assert await card.get_by_text('Unterlagen nicht vollständig').is_visible()
        assert not await card.get_by_text('02.11.2026', exact=False).count()
        await page.locator('[data-quest-card=draft-check]').get_by_role('button', name='Bearbeiten').click()
        assert not await page.locator('#quest-step-day-label').is_visible()
        assert not await page.locator('#quest-step-minutes-label').is_visible()
        await page.get_by_role('button', name='Schritt speichern').click()
        await card.get_by_role('button', name='Lernweg öffnen').click()
        assert await page.locator('#quest-path-body input[type=checkbox]:not(:disabled)').count() == 3
        assert await page.locator('#quest-path-body').get_by_text('In deinem Tempo · Frist 12.11.2026').is_visible()
        await page.locator('#quest-path-dialog [data-close-dialog]').click()
        await today.locator('input[type=checkbox]').click()
        await today.get_by_text('Aufgabe 2').wait_for()
        assert not await today.get_by_text('Aufgabe 1').count()
        assert await page.locator('#quest-today-time').text_content() == '0 Min. geplant'
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors
        await browser.close()


async def test_upcoming_window_and_item_deep_link(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    if (root / '.local/browsers').exists():
        monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(root / '.local/browsers'))
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    steps = [{'id': str(i), 'title': title, 'detail': '', 'minutes': 10, 'done': False,
              'completed_at': None, 'scheduled_date': day, 'day_offset': i, 'item_id': 7}
             for i, (title, day) in enumerate([('Überfällig', '2026-11-01'), ('Heute', '2026-11-02'),
                                               ('Sechster Tag', '2026-11-08'), ('Später', '2026-11-09')])]
    steps.extend(steps[0] | {'id': f'old-{n}', 'title': f'Alt {n}'} for n in range(1, 5))
    quest = {'id': 'paced', 'title': 'Lernweg', 'state': 'active', 'planning': 'paced',
             'start_date': '2026-11-02', 'end_date': '2026-11-09', 'steps': steps,
             'sources': [], 'version': 1, 'total_count': 4, 'completed_count': 0}
    writes = []
    async with async_playwright() as driver:
        if not Path(driver.chromium.executable_path).exists():
            pytest.skip('Project Chromium not installed')
        browser = await driver.chromium.launch(headless=True)
        page = await browser.new_page()
        async def serve(route):
            path = urlsplit(route.request.url).path
            if path == '/api/quests' and route.request.method == 'GET':
                await route.fulfill(json={'today': '2026-11-02', 'quests': [quest]})
            elif path == '/api/quests/options':
                await route.fulfill(json={'items': [{'id': 7, 'title': 'Quellenaufgabe', 'kind': 'task'}], 'model_ready': True})
            elif path == '/api/quests/paced/step':
                payload = route.request.post_data_json
                writes.append(payload)
                steps[3]['done'] = True
                quest['version'] = 2
                await route.fulfill(json={'quest': quest, 'xp_delta': 10})
            elif route.request.method == 'POST':
                writes.append({'unexpected': path})
                await route.fulfill(status=500)
            else:
                response = client.get(path)
                await route.fulfill(status=response.status_code, body=response.content,
                                    content_type=response.headers.get('content-type', 'application/octet-stream'))
        await page.route('**/*', serve)
        await page.goto('http://localhost/quests')
        upcoming = page.locator('#quest-today')
        await upcoming.get_by_text('Sechster Tag').wait_for()
        assert await upcoming.locator('strong').all_text_contents() == ['Heute', 'Sechster Tag']
        assert await page.locator('#quest-overdue > .quest-step strong').all_text_contents() == ['Überfällig', 'Alt 1', 'Alt 2']
        assert await page.locator('#quest-overdue details summary').text_content() == '2 weitere offene Schritte'
        await page.locator('#quest-overdue details summary').click()
        assert await page.locator('#quest-overdue details .quest-step').count() == 2
        assert await page.locator('#quest-today-time').text_content() == '20 Min. geplant'
        assert not await upcoming.get_by_text('Später').count()
        await page.locator('[data-quest-card=paced]').get_by_role('button', name='Lernweg öffnen').click()
        await page.locator('#quest-path-body').get_by_label('Später abschließen').click()
        await page.locator('#quest-path-message').get_by_text('+10 XP', exact=False).wait_for()
        assert writes == [{'version': 1, 'step_id': '3', 'done': True}]
        await page.goto('http://localhost/quests?item=7')
        await page.locator('#quest-item-selected').get_by_text('Ausgewählt: Quellenaufgabe').wait_for()
        assert await page.locator('#quest-dialog').is_visible()
        assert await page.locator('[data-item="7"]').get_attribute('aria-pressed') == 'true'
        assert len(writes) == 1
        await page.goto('http://localhost/quests?item=999')
        await page.locator('#quest-form-message').get_by_text('nicht mehr für eine Quest verfügbar', exact=False).wait_for()
        assert len(writes) == 1
        await browser.close()
