from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from feishu.events.jobs import EventJobStatus, InMemoryEventJobStore, SqliteEventJobStore


@pytest.mark.parametrize("store_factory", [InMemoryEventJobStore])
async def test_enqueue_is_idempotent_and_claims_fifo_per_partition(store_factory) -> None:
    store = store_factory()

    assert await store.enqueue("evt-1", {"n": 1}, partition_key="chat-1", now=0) is True
    assert await store.enqueue("evt-1", {"n": 999}, partition_key="chat-1", now=0) is False
    assert await store.enqueue("evt-2", {"n": 2}, partition_key="chat-1", now=1) is True
    assert await store.enqueue("evt-3", {"n": 3}, partition_key="chat-2", now=2) is True

    first = await store.claim_next("worker-a", now=10, lease_seconds=30)
    other_partition = await store.claim_next("worker-b", now=10, lease_seconds=30)

    assert first is not None and first.event_id == "evt-1"
    assert other_partition is not None and other_partition.event_id == "evt-3"
    assert await store.claim_next("worker-c", now=10, lease_seconds=30) is None


async def test_pre_effect_expired_lease_can_be_reclaimed() -> None:
    store = InMemoryEventJobStore()
    await store.enqueue("evt-1", {"type": "message"}, partition_key="chat-1", now=1)

    first = await store.claim_next("worker-a", now=5, lease_seconds=10)
    recovered = await store.claim_next("worker-b", now=15, lease_seconds=10)

    assert first is not None and first.status is EventJobStatus.RUNNING
    assert recovered is not None and recovered.event_id == "evt-1"
    assert recovered.attempt == 2
    assert recovered.worker_id == "worker-b"


async def test_retry_wait_blocks_newer_jobs_in_the_same_partition() -> None:
    store = InMemoryEventJobStore()
    await store.enqueue("evt-1", {"n": 1}, partition_key="chat-1", now=1)
    await store.enqueue("evt-2", {"n": 2}, partition_key="chat-1", now=2)
    first = await store.claim_next("worker-a", now=5, lease_seconds=10)
    assert first is not None and first.event_id == "evt-1"
    await store.retry("evt-1", "worker-a", available_at=50, error_type="Timeout", now=6)

    assert await store.next_wakeup() == 50
    assert await store.claim_next("worker-b", now=10, lease_seconds=10) is None
    retry = await store.claim_next("worker-c", now=50, lease_seconds=10)
    assert retry is not None and retry.event_id == "evt-1"
    await store.complete("evt-1", "worker-c", now=51)

    second = await store.claim_next("worker-d", now=52, lease_seconds=10)
    assert second is not None and second.event_id == "evt-2"


async def test_unknown_job_blocks_same_partition_without_busy_wakeup() -> None:
    store = InMemoryEventJobStore()
    await store.enqueue("evt-1", {}, partition_key="chat-1", now=1)
    await store.enqueue("evt-2", {}, partition_key="chat-1", now=2)
    first = await store.claim_next("worker-a", now=5, lease_seconds=10)
    assert first is not None
    await store.mark_side_effect_started("evt-1", "worker-a")

    assert await store.claim_next("worker-b", now=15, lease_seconds=10) is None
    assert await store.next_wakeup() is None

    await store.requeue_unknown("evt-1", now=20)
    assert await store.next_wakeup() == 20


async def test_failed_job_blocks_partition_until_explicit_requeue() -> None:
    store = InMemoryEventJobStore(max_attempts=1)
    await store.enqueue("evt-1", {}, partition_key="chat-1", now=1)
    await store.enqueue("evt-2", {}, partition_key="chat-1", now=2)
    first = await store.claim_next("worker-a", now=5, lease_seconds=10)
    assert first is not None
    await store.retry("evt-1", "worker-a", available_at=6, error_type="Timeout", now=5)

    failed = await store.get("evt-1")
    assert failed is not None and failed.status is EventJobStatus.FAILED
    assert await store.claim_next("worker-b", now=10, lease_seconds=10) is None

    await store.requeue_failed("evt-1", now=11)
    replay = await store.claim_next("worker-c", now=11, lease_seconds=10)
    assert replay is not None and replay.event_id == "evt-1" and replay.attempt == 1


async def test_post_effect_expired_lease_is_frozen_as_unknown() -> None:
    store = InMemoryEventJobStore()
    await store.enqueue("evt-1", {"type": "message"}, partition_key="chat-1", now=1)
    job = await store.claim_next("worker-a", now=5, lease_seconds=10)
    assert job is not None
    await store.mark_side_effect_started(job.job_id, "worker-a")

    assert await store.claim_next("worker-b", now=15, lease_seconds=10) is None
    unknown = await store.get(job.job_id)
    assert unknown is not None
    assert unknown.status is EventJobStatus.UNKNOWN

    await store.requeue_unknown(job.job_id, now=20)
    replay = await store.claim_next("worker-c", now=20, lease_seconds=10)
    assert replay is not None and replay.event_id == "evt-1"
    assert replay.attempt == 1


async def test_only_current_worker_can_complete_or_retry_a_claim() -> None:
    store = InMemoryEventJobStore()
    await store.enqueue("evt-1", {"type": "message"}, partition_key="chat-1", now=1)
    job = await store.claim_next("worker-a", now=5, lease_seconds=10)
    assert job is not None

    with pytest.raises(RuntimeError, match="running lease"):
        await store.complete(job.job_id, "worker-b")

    await store.complete(job.job_id, "worker-a")
    completed = await store.get(job.job_id)
    assert completed is not None and completed.status is EventJobStatus.COMPLETED


async def test_next_wakeup_tracks_ready_work_and_expiring_leases() -> None:
    store = InMemoryEventJobStore()

    assert await store.next_wakeup() is None
    await store.enqueue("evt-1", {}, partition_key="chat-1", now=10)
    assert await store.next_wakeup() == 10

    claimed = await store.claim_next("worker-a", now=10, lease_seconds=30)
    assert claimed is not None
    assert await store.next_wakeup() == 40

    await store.complete("evt-1", "worker-a", now=11)
    assert await store.next_wakeup() is None


async def test_sqlite_store_recovers_jobs_after_reconstruction(tmp_path: Path) -> None:
    path = tmp_path / "jobs.db"
    first = SqliteEventJobStore(path)
    assert await first.enqueue("evt-1", {"schema": "2.0"}, partition_key="chat-1", now=1) is True
    claimed = await first.claim_next("worker-a", now=5, lease_seconds=30)
    assert claimed is not None
    await first.close()

    second = SqliteEventJobStore(path)
    recovered = await second.get(claimed.job_id)
    assert recovered is not None
    assert recovered.event_id == "evt-1"
    assert recovered.payload == {"schema": "2.0"}
    assert recovered.status is EventJobStatus.RUNNING
    await second.close()


async def test_sqlite_claim_is_exclusive_across_connections(tmp_path: Path) -> None:
    path = tmp_path / "jobs.db"
    left = SqliteEventJobStore(path)
    right = SqliteEventJobStore(path)
    await left.enqueue("evt-1", {}, partition_key="chat-1", now=1)

    claims = await asyncio.gather(
        left.claim_next("worker-a", now=5, lease_seconds=30),
        right.claim_next("worker-b", now=5, lease_seconds=30),
    )

    assert sum(claim is not None for claim in claims) == 1
    await left.close()
    await right.close()


async def test_sqlite_next_wakeup_obeys_partition_head_and_unknown_state(tmp_path: Path) -> None:
    store = SqliteEventJobStore(tmp_path / "jobs.db")
    await store.enqueue("evt-1", {}, partition_key="chat-1", now=1)
    await store.enqueue("evt-2", {}, partition_key="chat-1", now=2)
    first = await store.claim_next("worker-a", now=5, lease_seconds=10)
    assert first is not None
    await store.mark_side_effect_started("evt-1", "worker-a")

    assert await store.claim_next("worker-b", now=15, lease_seconds=10) is None
    assert await store.next_wakeup() is None

    await store.requeue_unknown("evt-1", now=20)
    assert await store.next_wakeup() == 20
    await store.close()
