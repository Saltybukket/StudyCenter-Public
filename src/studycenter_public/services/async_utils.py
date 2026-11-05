"""Keep ownership of a thread until its database or file work has stopped."""
import asyncio


async def finish_thread(function,*args,**kwargs):
    task=asyncio.create_task(asyncio.to_thread(function,*args,**kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:await asyncio.shield(task)
            except asyncio.CancelledError:continue
            except Exception:break
        if not task.cancelled():task.exception()
        raise
