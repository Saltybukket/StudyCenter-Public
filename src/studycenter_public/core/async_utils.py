"""Keep ownership of background work until its thread has actually stopped."""
import asyncio


async def settle_cancelled(task):
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    if task.done() and not task.cancelled():
        task.exception()


async def finish_thread(function, *args):
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await settle_cancelled(task)
        raise
