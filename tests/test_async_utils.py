"""Cancellation must not abandon an in-flight local file/database worker."""
import asyncio
from threading import Event

from studycenter_public.services.async_utils import finish_thread


def test_cancel_waits_until_owned_thread_finishes():
    async def scenario():
        started,release,finished=Event(),Event(),Event()
        def work():
            started.set()
            if not release.wait(5):raise RuntimeError('Synthetic worker timeout')
            finished.set()
        task=asyncio.create_task(finish_thread(work))
        assert await asyncio.to_thread(started.wait,5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        try:await task
        except asyncio.CancelledError:pass
        else:raise AssertionError('Cancellation must propagate')
        assert finished.is_set()
    asyncio.run(scenario())
