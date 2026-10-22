from __future__ import annotations

import asyncio

import pytest

from feishu.events.dispatcher import EventDispatcher
from feishu.events.envelope import Event
from feishu.events.idempotency import InMemorySeenStore
from feishu.events.job_runner import EventJobRunner
from feishu.events.jobs import EventJobStatus, InMemoryEventJobStore, mark_current_event_side_effect_started


def payload(event_id: str) -> dict:
    return {
        "schema": "2.0",
        "header": {"event_id": event_id, "event_type": "im.message.receive_v1"},
        "event": {"message": {"chat_id": "chat-1", "message_id": f"msg-{event_id}"}},
    }


async def test_runner_completes_a_job_and_deduplicates_later_delivery() -> None:
    store = InMemoryEventJobStore()
    await store.enqueue("evt-1", payload("evt-1"), partition_key="chat-1", now=1)
    received: list[str] = []

    async def handle(event: Event) -> None:
        received.append(event.event_id)

    runner = EventJobRunner(store, handle, worker_id="worker-a", now=lambda: 5)

    completed = await runner.run_once()

    assert completed is not None and completed.status is EventJobStatus.COMPLETED
    assert received == ["evt-1"]
    assert await runner.run_once() is None


async def test_runner_retries_pre_effect_exception_with_backoff() -> None:
    store = InMemoryEventJobStore()
    await store.enqueue("evt-1", payload("evt-1"), partition_key="chat-1", now=1)

    async def fail(_event: Event) -> None:
        raise TimeoutError("model request timed out")

    runner = EventJobRunner(store, fail, worker_id="worker-a", now=lambda: 5, backoff_base_seconds=2)

    retried = await runner.run_once()

    assert retried is not None
    assert retried.status is EventJobStatus.RETRY_WAIT
    assert retried.available_at == 7
    assert retried.error_type == "TimeoutError"


async def test_runner_freezes_failure_after_side_effect_begins() -> None:
    store = InMemoryEventJobStore()
    await store.enqueue("evt-1", payload("evt-1"), partition_key="chat-1", now=1)

    async def fail_after_send(_event: Event) -> None:
        await mark_current_event_side_effect_started()
        raise TimeoutError("reply result unknown")

    runner = EventJobRunner(store, fail_after_send, worker_id="worker-a", now=lambda: 5)

    unknown = await runner.run_once()

    assert unknown is not None and unknown.status is EventJobStatus.UNKNOWN
    assert unknown.side_effect_started is True
    assert unknown.error_type == "TimeoutError"
    assert await runner.run_once() is None


async def test_runner_retries_a_cancelled_pre_effect_handler() -> None:
    store = InMemoryEventJobStore()
    await store.enqueue("evt-1", payload("evt-1"), partition_key="chat-1", now=1)

    async def cancel(_event: Event) -> None:
        raise asyncio.CancelledError

    runner = EventJobRunner(store, cancel, worker_id="worker-a", now=lambda: 5, backoff_base_seconds=1)

    with pytest.raises(asyncio.CancelledError):
        await runner.run_once()

    job = await store.get("evt-1")
    assert job is not None and job.status is EventJobStatus.RETRY_WAIT


async def test_safe_retry_bypasses_dispatcher_seen_store_claim() -> None:
    store = InMemoryEventJobStore()
    await store.enqueue("evt-1", payload("evt-1"), partition_key="chat-1", now=1)
    dispatcher = EventDispatcher(seen_store=InMemorySeenStore())
    attempts = 0

    @dispatcher.on("im.message.receive_v1")
    async def handle(_event: Event) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("retry before side effect")

    async def dispatch(event: Event) -> None:
        await dispatcher.dispatch(event, propagate_errors=True)

    runner = EventJobRunner(
        store,
        dispatch,
        worker_id="worker-a",
        now=lambda: 5,
        backoff_base_seconds=0,
    )

    first = await runner.run_once()
    second = await runner.run_once()

    assert first is not None and first.status is EventJobStatus.RETRY_WAIT
    assert second is not None and second.status is EventJobStatus.COMPLETED
    assert attempts == 2


async def test_runner_renews_lease_while_handler_is_active() -> None:
    store = InMemoryEventJobStore()
    await store.enqueue("evt-1", payload("evt-1"), partition_key="chat-1", now=1)
    clock = [1.0]
    handler_started = asyncio.Event()
    finish_handler = asyncio.Event()
    heartbeat_waits: asyncio.Queue[float] = asyncio.Queue()
    heartbeat_steps: asyncio.Queue[None] = asyncio.Queue()

    async def controlled_sleep(interval: float) -> None:
        await heartbeat_waits.put(interval)
        await heartbeat_steps.get()
        clock[0] += interval

    async def blocked(_event: Event) -> None:
        handler_started.set()
        await finish_handler.wait()

    runner = EventJobRunner(
        store,
        blocked,
        worker_id="worker-a",
        lease_seconds=60,
        now=lambda: clock[0],
        sleep=controlled_sleep,
    )
    task = asyncio.create_task(runner.run_once())
    try:
        await asyncio.wait_for(handler_started.wait(), timeout=5)
        await asyncio.wait_for(heartbeat_waits.get(), timeout=5)
        for expected_lease in (81, 101):
            await heartbeat_steps.put(None)
            # Entering the following heartbeat wait proves renewal finished.
            await asyncio.wait_for(heartbeat_waits.get(), timeout=5)
            leased = await store.get("evt-1")
            assert leased is not None and leased.lease_until == expected_lease

        # Past the original lease and first renewal, within the second renewal.
        clock[0] = 91
        assert await store.claim_next("worker-b", now=clock[0], lease_seconds=60) is None
        finish_handler.set()
        completed = await asyncio.wait_for(task, timeout=5)
        assert completed is not None and completed.status is EventJobStatus.COMPLETED
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_runner_cancellation_after_effect_becomes_unknown() -> None:
    store = InMemoryEventJobStore()
    await store.enqueue("evt-1", payload("evt-1"), partition_key="chat-1")
    started = asyncio.Event()

    async def blocked_after_effect(_event: Event) -> None:
        await mark_current_event_side_effect_started()
        started.set()
        await asyncio.Event().wait()

    runner = EventJobRunner(store, blocked_after_effect, worker_id="worker-a")
    task = asyncio.create_task(runner.run_once())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    job = await store.get("evt-1")
    assert job is not None and job.status is EventJobStatus.UNKNOWN


@pytest.mark.parametrize("cancel_runner", [False, True])
async def test_runner_uses_and_cancels_injected_heartbeat_wait(cancel_runner) -> None:
    store = InMemoryEventJobStore()
    await store.enqueue("evt-1", payload("evt-1"), partition_key="chat-1", now=1)
    waiting = asyncio.Event()
    wait_closed = asyncio.Event()
    intervals = []

    async def sleep(interval: float) -> None:
        intervals.append(interval)
        waiting.set()
        try:
            await asyncio.Event().wait()
        finally:
            wait_closed.set()

    async def handle(_event: Event) -> None:
        await waiting.wait()
        if cancel_runner:
            await asyncio.Event().wait()

    runner = EventJobRunner(store, handle, worker_id="worker-a", lease_seconds=120, now=lambda: 5, sleep=sleep)
    task = asyncio.create_task(runner.run_once())
    if cancel_runner:
        await waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        completed = await task
        assert completed is not None and completed.status is EventJobStatus.COMPLETED

    assert intervals == [40]
    assert wait_closed.is_set()
    job = await store.get("evt-1")
    expected_status = EventJobStatus.RETRY_WAIT if cancel_runner else EventJobStatus.COMPLETED
    assert job is not None and job.status is expected_status
