import asyncio
import threading

import httpx
import pytest

from studycenter_public.core.app import create_app
from .reference_settings import Settings


@pytest.mark.asyncio
async def test_backup_excludes_inflight_mutations_and_bad_csrf_does_not_hold_lease(tmp_path, monkeypatch):
    import studycenter_public.core.app as module

    app = create_app(Settings(data_dir=tmp_path))
    course = app.state.db.execute("INSERT INTO courses(title,semester) VALUES('Test','2026W')")
    started, release = asyncio.Event(), asyncio.Event()
    backup_started, backup_release = threading.Event(), threading.Event()
    hold_backup = False

    async def slow_form():
        started.set()
        await release.wait()
        yield f'alias=Race&course_id={course}'.encode()

    def backup(db, directory):
        backup_started.set()
        if hold_backup:
            assert backup_release.wait(10)
        return directory / 'dummy.zip'

    monkeypatch.setattr(module, 'create_backup', backup)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
        headers = {'X-CSRF-Token': app.state.csrf}
        assert (await client.post('/settings/aliases', headers={'X-CSRF-Token': 'bad'})).status_code == 403
        mutation = asyncio.create_task(client.post('/settings/aliases', headers=headers | {'Content-Type': 'application/x-www-form-urlencoded'}, content=slow_form()))
        await started.wait()
        refused = await client.post('/settings/backups/create', headers=headers | {'X-Requested-With': 'fetch'})
        release.set()
        assert (await mutation).status_code == 303
        assert refused.status_code == 409
        assert not backup_started.is_set()
        assert app.state.db.one("SELECT alias FROM course_aliases WHERE alias='race'")
        hold_backup = True
        running = asyncio.create_task(client.post('/settings/backups/create', headers=headers | {'X-Requested-With': 'fetch'}))
        assert await asyncio.to_thread(backup_started.wait, 5)
        assert (await client.post('/settings/aliases', headers=headers, data={'alias': 'Other', 'course_id': str(course)})).status_code == 409
        backup_release.set()
        assert (await running).status_code == 200


@pytest.mark.asyncio
async def test_cancelled_backup_keeps_mutations_blocked_until_worker_finishes(tmp_path, monkeypatch):
    import studycenter_public.core.app as module

    app = create_app(Settings(data_dir=tmp_path))
    started, release = threading.Event(), threading.Event()

    def backup(db, directory):
        started.set()
        assert release.wait(10)
        return directory / 'dummy.zip'

    monkeypatch.setattr(module, 'create_backup', backup)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
        headers = {'X-CSRF-Token': app.state.csrf}
        running = asyncio.create_task(client.post('/settings/backups/create', headers=headers | {'X-Requested-With': 'fetch'}))
        assert await asyncio.to_thread(started.wait, 5)
        running.cancel()
        await asyncio.sleep(0)
        running.cancel()  # Repeated cancellation must not release a live worker.
        await asyncio.sleep(0)
        assert (await client.post('/settings/aliases', headers=headers)).status_code == 409
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert (await client.post('/settings/aliases', headers={'X-CSRF-Token': 'bad'})).status_code == 403


@pytest.mark.asyncio
async def test_reminder_tick_and_backup_exclude_each_other(tmp_path, monkeypatch):
    import studycenter_public.core.app as module

    app = create_app(Settings(data_dir=tmp_path))
    reminder = app.state.reminders
    entered, release = threading.Event(), threading.Event()
    backup_started, backup_release = threading.Event(), threading.Event()
    original = reminder.reconcile

    def reconcile(current):
        entered.set()
        assert release.wait(10)
        original(current)

    def backup(db, directory):
        backup_started.set()
        assert backup_release.wait(10)
        return directory / 'dummy.zip'

    monkeypatch.setattr(reminder, 'reconcile', reconcile)
    monkeypatch.setattr(module, 'create_backup', backup)
    can_run = lambda: not app.state.maintenance and not (tmp_path / 'restore-pending.zip').exists() and not app.state.lifecycle_request
    worker = asyncio.create_task(reminder.run(can_run))
    assert await asyncio.to_thread(entered.wait, 5)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
        headers = {'X-CSRF-Token': app.state.csrf, 'X-Requested-With': 'fetch'}
        assert (await client.post('/settings/backups/create', headers=headers)).status_code == 409
        assert not backup_started.is_set()
        reminder.stop.set()
        release.set()
        await worker
        reminder.stop.clear()
        entered.clear()
        running = asyncio.create_task(client.post('/settings/backups/create', headers=headers))
        assert await asyncio.to_thread(backup_started.wait, 5)
        worker = asyncio.create_task(reminder.run(can_run))
        await asyncio.sleep(0)
        assert not entered.is_set() and not reminder.busy
        reminder.stop.set()
        await worker
        backup_release.set()
        assert (await running).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize('service_name', ['reminders', 'class_completion'])
async def test_cancelled_worker_keeps_busy_until_tick_finishes(tmp_path, monkeypatch, service_name):
    app = create_app(Settings(data_dir=tmp_path))
    reminder = getattr(app.state, service_name)
    entered, release = threading.Event(), threading.Event()

    def tick(*args):
        if service_name=='reminders':
            assert len(args)==2 and args[1] is True
        else:
            assert not args
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(reminder, 'tick', tick)
    worker = asyncio.create_task(reminder.run())
    assert await asyncio.to_thread(entered.wait, 5)
    worker.cancel()
    await asyncio.sleep(0)
    worker.cancel()
    await asyncio.sleep(0)
    busy = reminder.busy
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert busy
    assert not reminder.busy
