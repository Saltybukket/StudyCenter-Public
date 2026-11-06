"""A stop signal must become visible only after its writer has closed it."""
from pathlib import Path
from types import SimpleNamespace

from studycenter_public.platform.runtime import Runtime, request_stop


def test_shutdown_request_is_published_after_writer_closes(tmp_path, monkeypatch):
    paths = SimpleNamespace(data=tmp_path)
    runtime = Runtime(paths)
    runtime.endpoint.write_text('{}')
    original = Path.write_text

    def watcher_runs_while_writer_is_open(path, text, *args, **kwargs):
        if path == runtime.quit_file:
            with path.open('w', encoding='ascii') as writer:
                writer.write(text)
                # Reproduce the real Windows notification/close race. Windows
                # refuses deletion while write_text still owns this handle.
                runtime.close()
                return len(text)
        return original(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', watcher_runs_while_writer_is_open)
    assert request_stop(paths)
    assert runtime.quit_file.is_file()
    runtime.close()
    assert not runtime.quit_file.exists() and not runtime.endpoint.exists()


def test_server_signals_workers_before_draining_ongoing_write(tmp_path):
    import asyncio
    import threading
    from contextlib import asynccontextmanager
    import httpx
    from fastapi import FastAPI

    paths = SimpleNamespace(data=tmp_path, instance_id='synthetic')
    runtime = Runtime(paths, preferred_port=0)
    runtime.acquire()
    entered = threading.Event()
    finished = threading.Event()
    order = []
    stop_worker = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal stop_worker
        stop_worker = asyncio.Event()
        yield

    app = FastAPI(lifespan=lifespan)

    def begin_stop(system=False):
        assert system
        order.append('signal')
        stop_worker.set()

    app.state.begin_stop = begin_stop

    @app.post('/write')
    async def write():
        entered.set()
        await stop_worker.wait()
        (tmp_path / 'write-receipt').write_text('finished')
        order.append('write')
        finished.set()
        return {'ok': True}

    runtime.start(app)
    results = []
    def send_write():
        try:
            results.append(httpx.post(runtime.url + '/write', timeout=10, trust_env=False))
        except Exception as error:
            results.append(error)
    writer = threading.Thread(target=send_write)
    writer.start()
    try:
        assert entered.wait(5)
        assert not finished.is_set()  # Work really is in progress at shutdown.
        runtime.stop()
        runtime.close()
        writer.join(5)
        assert not writer.is_alive()
        assert finished.is_set() and order == ['signal', 'write']
        assert results[0].status_code == 200
        assert (tmp_path / 'write-receipt').read_text() == 'finished'
        assert runtime.server.config.timeout_graceful_shutdown == 5
    finally:
        if runtime.thread.is_alive():
            runtime.stop()
            runtime.thread.join(10)
        writer.join(10)
