"""Bounded clipboard transport helpers, without native clipboard side effects."""
import asyncio

CHUNK = 256 * 1024
MIME = "application/octet-stream"


async def blocking(function, *args, cancelled):
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Packaging/extraction checks this flag between entries/chunks. Wait
        # for the worker before closing its file or removing the temp folder.
        cancelled.set()
        await asyncio.gather(task, return_exceptions=True)
        raise
