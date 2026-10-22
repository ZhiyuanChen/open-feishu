"""Invoke optional asynchronous persistence without blocking async runtimes."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

_P = ParamSpec("_P")
_T = TypeVar("_T")


async def invoke_store(method: Callable[_P, _T | Awaitable[_T]], *args: _P.args, **kwargs: _P.kwargs) -> _T:
    """Await async stores directly; run existing synchronous stores in a thread.

    A synchronous wrapper may itself return an awaitable, which is then awaited
    on the caller's event loop. Async bindings never enter the thread executor.
    """
    result: _T | Awaitable[_T]
    if inspect.iscoroutinefunction(method):
        result = method(*args, **kwargs)
    else:
        result = await asyncio.to_thread(method, *args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result
