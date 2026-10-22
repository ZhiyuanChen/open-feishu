"""Equal receipt timestamps must not reorder conversation work by opaque IDs."""

from __future__ import annotations

import pytest

from feishu.events.jobs import EventJobStatus, InMemoryEventJobStore, SqliteEventJobStore


@pytest.fixture(params=["memory", "sqlite"])
def store_factory(request, tmp_path):
    stores = []

    def make(**kwargs):
        store = (
            InMemoryEventJobStore(**kwargs)
            if request.param == "memory"
            else SqliteEventJobStore(tmp_path / f"jobs-{len(stores)}.db", **kwargs)
        )
        stores.append(store)
        return store

    yield make
    for store in stores:
        if isinstance(store, SqliteEventJobStore):
            store._db.close()


async def test_equal_timestamp_jobs_follow_receipt_order_not_lexical_id(store_factory):
    store = store_factory()
    for event_id, kind in [("z-message", "message"), ("m-approval", "approval"), ("a-oauth", "oauth")]:
        assert await store.enqueue(event_id, {"kind": kind}, partition_key="chat", now=10)
    assert not await store.enqueue("z-message", {"kind": "duplicate"}, partition_key="chat", now=10)

    for event_id in ["z-message", "m-approval", "a-oauth"]:
        job = await store.claim_next("worker", now=11)
        assert job is not None and job.event_id == event_id
        await store.complete(job.job_id, "worker", now=12)
    assert await store.claim_next("worker", now=13) is None


@pytest.mark.parametrize(
    "state,expected_wakeup",
    [("running", 40), ("retry_wait", 80), ("unknown", None), ("failed", None)],
)
async def test_unresolved_head_blocks_tied_successor_but_not_other_partition(store_factory, state, expected_wakeup):
    store = store_factory(max_attempts=1 if state == "failed" else 5)
    await store.enqueue("z-head", {}, partition_key="chat", now=10)
    head = await store.claim_next("owner", now=10, lease_seconds=30)
    assert head is not None and head.event_id == "z-head"
    if state in ("retry_wait", "failed"):
        await store.retry("z-head", "owner", available_at=80, error_type="Unavailable", now=10)
    elif state == "unknown":
        await store.mark_side_effect_started("z-head", "owner")
        await store.mark_unknown("z-head", "owner", error_type="ResponseLost", now=10)
    await store.enqueue("a-tail", {}, partition_key="chat", now=10)

    assert await store.next_wakeup() == expected_wakeup
    assert await store.claim_next("blocked", now=11) is None
    await store.enqueue("m-other", {}, partition_key="other-chat", now=10)
    other = await store.claim_next("independent", now=11)
    assert other is not None and other.event_id == "m-other"
    await store.complete("m-other", "independent", now=12)

    if state == "running":
        await store.complete("z-head", "owner", now=12)
    elif state == "retry_wait":
        retried = await store.claim_next("retry", now=80)
        assert retried is not None and retried.event_id == "z-head"
        await store.complete("z-head", "retry", now=81)
    else:
        record = await store.get("z-head")
        assert record.status is (EventJobStatus.UNKNOWN if state == "unknown" else EventJobStatus.FAILED)
        assert await store.next_wakeup() is None
        return
    tail = await store.claim_next("successor", now=82)
    assert tail is not None and tail.event_id == "a-tail"


async def test_created_at_remains_primary_order_for_different_timestamps(store_factory):
    store = store_factory()
    await store.enqueue("first-received", {}, partition_key="chat", now=20)
    await store.enqueue("earlier-created", {}, partition_key="chat", now=10)

    first = await store.claim_next("worker", now=20)
    assert first is not None and first.event_id == "earlier-created"
    await store.complete(first.job_id, "worker", now=21)
    second = await store.claim_next("worker", now=21)
    assert second is not None and second.event_id == "first-received"


async def test_sqlite_reopen_retains_receipt_order_for_equal_timestamps(tmp_path):
    path = tmp_path / "reopened.db"
    store = SqliteEventJobStore(path)
    await store.enqueue("z-first", {}, partition_key="chat", now=10)
    await store.enqueue("a-second", {}, partition_key="chat", now=10)
    await store.close()

    reopened = SqliteEventJobStore(path)
    try:
        first = await reopened.claim_next("worker", now=11)
        assert first is not None and first.event_id == "z-first"
        await reopened.complete(first.job_id, "worker", now=12)
        second = await reopened.claim_next("worker", now=12)
        assert second is not None and second.event_id == "a-second"
    finally:
        await reopened.close()
