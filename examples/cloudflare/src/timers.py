"""Runtime waits that release their Cloudflare timer when Python cancels the await."""

from __future__ import annotations


async def sleep(seconds: float) -> None:
    """Wait without leaving Pyodide's cancelled ``asyncio.sleep`` timer pending."""
    from js import AbortController, Object, scheduler

    controller = AbortController.new()
    options = Object.new()
    options.signal = controller.signal
    try:
        await scheduler.wait(seconds * 1000, options)
    finally:
        controller.abort()
