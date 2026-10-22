from __future__ import annotations

import asyncio
import subprocess
import sys
from importlib import import_module
from types import SimpleNamespace

import pytest


class _AbortController:
    def __init__(self) -> None:
        self.signal = SimpleNamespace(aborted=False)

    def abort(self) -> None:
        self.signal.aborted = True


def _install_runtime(monkeypatch, wait):
    controllers = []

    def new_controller():
        controller = _AbortController()
        controllers.append(controller)
        return controller

    monkeypatch.setitem(
        sys.modules,
        "js",
        SimpleNamespace(
            AbortController=SimpleNamespace(new=new_controller),
            Object=SimpleNamespace(new=SimpleNamespace),
            scheduler=SimpleNamespace(wait=wait),
        ),
    )
    return controllers


def test_timer_module_imports_without_javascript_runtime() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import examples.cloudflare.src.timers"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


async def test_timer_aborts_after_normal_resolution(monkeypatch) -> None:
    received = []

    async def wait(delay, options):
        received.append((delay, options.signal))

    controllers = _install_runtime(monkeypatch, wait)
    sleep = import_module("examples.cloudflare.src.timers").sleep
    await sleep(0.25)

    assert len(controllers) == 1
    assert received == [(250, controllers[0].signal)]
    assert controllers[0].signal.aborted


async def test_timer_aborts_after_scheduler_failure(monkeypatch) -> None:
    failure = RuntimeError("scheduler failure")

    async def wait(delay, options):
        raise failure

    controllers = _install_runtime(monkeypatch, wait)
    sleep = import_module("examples.cloudflare.src.timers").sleep
    with pytest.raises(RuntimeError) as raised:
        await sleep(40)

    assert raised.value is failure
    assert controllers[0].signal.aborted


async def test_timer_aborts_when_python_wait_is_cancelled(monkeypatch) -> None:
    entered = asyncio.Event()

    async def wait(delay, options):
        entered.set()
        await asyncio.Event().wait()

    controllers = _install_runtime(monkeypatch, wait)
    sleep = import_module("examples.cloudflare.src.timers").sleep
    task = asyncio.create_task(sleep(40))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert controllers[0].signal.aborted
