from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from feishu.agent.scheduled import (
    Schedule,
    ScheduledExecutionResult,
    ScheduledTaskRunner,
    ScheduledTaskStore,
    ScheduledTimezoneResolver,
)


def stamp(value: str) -> float:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def test_calendar_timezones_and_dst():
    daily = Schedule.calendar(hour=9, minute=0)
    assert daily.next_after(stamp("2026-01-01T00:30:00"), "Asia/Shanghai") == stamp("2026-01-01T01:00:00")
    assert daily.next_after(stamp("2026-01-01T00:30:00"), "Europe/London") == stamp("2026-01-01T09:00:00")
    gap = Schedule.calendar(hour=2, minute=30)
    assert gap.next_after(stamp("2026-03-08T05:00:00"), "America/New_York") == stamp("2026-03-08T07:00:00")
    overlap = Schedule.calendar(hour=1, minute=30)
    assert overlap.next_after(stamp("2026-11-01T04:00:00"), "America/New_York") == stamp("2026-11-01T05:30:00")


def test_interval_uses_utc_anchor():
    schedule = Schedule.interval(seconds=3600, anchor_at=1000)
    assert schedule.next_after(4600, "Asia/Shanghai") == 8200
    assert schedule.next_after(4600, "America/New_York") == 8200


def test_interval_rejects_tiny_values():
    with pytest.raises(ValueError):
        Schedule.interval(seconds=1e-320, anchor_at=0)


def test_tenant_and_identity(tmp_path):
    store = ScheduledTaskStore(tmp_path / "schedule.db")
    with pytest.raises(ValueError):
        store.create_draft(
            tenant_key="tenant-a",
            owner_keys=("alice",),
            chat_id="chat",
            instruction="check",
            schedule=Schedule.once(100),
            now=1,
        )
    draft = store.create_draft(
        tenant_key="tenant-a",
        owner_keys=("user_id:alice",),
        chat_id="chat",
        instruction="check",
        schedule=Schedule.once(100),
        now=1,
    )
    with pytest.raises(PermissionError):
        store.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key="tenant-b", now=2)
    task = store.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key="tenant-a", now=2)
    assert store.list_owned(("user_id:alice",), tenant_key="tenant-b") == []
    with pytest.raises(PermissionError):
        store.set_state(
            task.task_id, task.owner_keys, "paused", tenant_key="tenant-b", expected_revision=task.revision, now=3
        )


def test_corrupt_task_does_not_block(tmp_path):
    path = tmp_path / "schedule.db"
    store = ScheduledTaskStore(path)
    tasks = []
    for instant in (100, 101):
        draft = store.create_draft(
            tenant_key="tenant-a",
            owner_keys=("user_id:alice",),
            chat_id="chat",
            instruction="check",
            schedule=Schedule.once(instant),
            now=1,
        )
        tasks.append(
            store.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key="tenant-a", now=1)
        )
    with sqlite3.connect(path) as db:
        db.execute("UPDATE scheduled_tasks SET schedule='{}' WHERE task_id=?", (tasks[0].task_id,))
    assert store.claim_due(now=101).task_id == tasks[1].task_id


def test_confirmation_and_ownership(tmp_path):
    store = ScheduledTaskStore(tmp_path / "schedule.db")
    draft = store.create_draft(
        tenant_key="tenant-a",
        owner_keys=("user_id:alice",),
        chat_id="chat",
        instruction="check",
        schedule=Schedule.once(100),
        now=10,
        ttl=20,
    )
    with pytest.raises(PermissionError):
        store.confirm_draft(draft.draft_id, ("user_id:bob",), draft.payload_hash, tenant_key="tenant-a", now=11)
    with pytest.raises(ValueError):
        store.confirm_draft(draft.draft_id, ("user_id:alice",), "wrong", tenant_key="tenant-a", now=11)
    task = store.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key="tenant-a", now=11)
    with pytest.raises(ValueError):
        store.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key="tenant-a", now=11)
    with pytest.raises(PermissionError):
        store.set_state(
            task.task_id, ("user_id:bob",), "paused", expected_revision=task.revision, tenant_key="tenant-a", now=12
        )
    updated = store.set_state(
        task.task_id, ("user_id:alice",), "paused", expected_revision=task.revision, tenant_key="tenant-a", now=12
    )
    with pytest.raises(ValueError):
        store.set_state(
            task.task_id, ("user_id:alice",), "active", expected_revision=task.revision, tenant_key="tenant-a", now=12
        )
    assert updated.state == "paused"
    expired = store.create_draft(
        tenant_key="tenant-a",
        owner_keys=("user_id:alice",),
        chat_id="chat",
        instruction="check",
        schedule=Schedule.once(100),
        now=10,
        ttl=2,
    )
    with pytest.raises(ValueError):
        store.confirm_draft(expired.draft_id, ("user_id:alice",), expired.payload_hash, tenant_key="tenant-a", now=12)


def test_claim_is_not_replayed(tmp_path):
    path = tmp_path / "schedule.db"
    first, second = ScheduledTaskStore(path), ScheduledTaskStore(path)
    draft = first.create_draft(
        tenant_key="tenant-a",
        owner_keys=("user_id:alice",),
        chat_id="chat",
        instruction="check",
        schedule=Schedule.once(100),
        now=0,
    )
    task = first.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key="tenant-a", now=1)
    run = first.claim_due(now=100, timezone_for=lambda _: "UTC")
    assert run is not None and run.status == "running" and run.task_id == task.task_id
    assert second.claim_due(now=100, timezone_for=lambda _: "UTC") is None
    assert second.runs(task.task_id)[0].status == "running"


def test_expired_execution_is_fenced(tmp_path):
    store = ScheduledTaskStore(tmp_path / "schedule.db")
    draft = store.create_draft(
        tenant_key="tenant-a",
        owner_keys=("user_id:alice",),
        chat_id="chat",
        instruction="check",
        schedule=Schedule.once(100),
        now=1,
    )
    store.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key="tenant-a", now=1)
    run = store.claim_due(now=100, lease_seconds=10)
    assert store.renew_lease(run.run_id, run.lease_token, now=105, lease_seconds=10)
    assert store.expire_runs(now=116) == 1
    assert store.get_run(run.run_id).status == "unknown"
    assert not store.renew_lease(run.run_id, run.lease_token, now=116, lease_seconds=10)
    with pytest.raises(PermissionError):
        store.validate_lease(run.run_id, run.lease_token, now=116)
    with pytest.raises(PermissionError):
        store.reconcile_unknown(
            run.run_id, tenant_key="tenant-b", actor="admin", decision="confirmed_failed", reason="checked", now=117
        )
    store.reconcile_unknown(
        run.run_id, tenant_key="tenant-a", actor="admin", decision="confirmed_failed", reason="checked", now=117
    )
    assert store.get_run(run.run_id).status == "failed"
    with pytest.raises(ValueError):
        store.reconcile_unknown(
            run.run_id, tenant_key="tenant-a", actor="admin", decision="confirmed_failed", reason="checked", now=118
        )


def test_missed_and_overlapping_runs(tmp_path):
    store = ScheduledTaskStore(tmp_path / "schedule.db")
    draft = store.create_draft(
        tenant_key="tenant-a",
        owner_keys=("user_id:alice",),
        chat_id="chat",
        instruction="check",
        schedule=Schedule.interval(seconds=60, anchor_at=100),
        now=1,
    )
    task = store.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key="tenant-a", now=1)
    missed = store.claim_due(now=220, timezone_for=lambda _: "UTC", grace_seconds=5)
    assert missed.status == "missed"
    active = store.claim_due(now=280, timezone_for=lambda _: "UTC", grace_seconds=5)
    assert active.status == "running"
    overlap = store.claim_due(now=340, timezone_for=lambda _: "UTC", grace_seconds=5)
    assert overlap.status == "skipped_overlap"
    assert [r.status for r in store.runs(task.task_id)] == ["missed", "running", "skipped_overlap"]


def test_runner_unknown_and_overlap(tmp_path):
    store = ScheduledTaskStore(tmp_path / "schedule.db")
    draft = store.create_draft(
        tenant_key="tenant-a",
        owner_keys=("user_id:alice",),
        chat_id="chat",
        instruction="check",
        schedule=Schedule.interval(seconds=60, anchor_at=100),
        now=1,
    )
    task = store.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key="tenant-a", now=1)
    now = [100.0]
    delivered = []

    async def execute(_task, _run):
        raise RuntimeError("unknown side effect")

    async def notify(_task, run, result):
        delivered.append((run.run_id, result.status))

    runner = ScheduledTaskRunner(store, execute, notify, clock=lambda: now[0])
    assert asyncio.run(runner.run_once()).status == "unknown"
    now[0] = 160
    assert asyncio.run(runner.run_once()).status == "skipped_overlap"
    assert [run.status for run in store.runs(task.task_id)] == ["unknown", "skipped_overlap"]
    assert [status for _, status in delivered] == ["unknown", "skipped_overlap"]


def test_runner_delivery_unknown(tmp_path):
    store = ScheduledTaskStore(tmp_path / "schedule.db")
    draft = store.create_draft(
        tenant_key="tenant-a",
        owner_keys=("user_id:alice",),
        chat_id="chat",
        instruction="check",
        schedule=Schedule.once(100),
        now=1,
    )
    task = store.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key="tenant-a", now=1)

    async def execute(_task, _run):
        return ScheduledExecutionResult("succeeded", "done")

    async def notify(_task, _run, _result):
        raise TimeoutError("uncertain delivery")

    runner = ScheduledTaskRunner(store, execute, notify, clock=lambda: 100)
    run = asyncio.run(runner.run_once())
    assert (run.status, run.delivery_status) == ("succeeded", "unknown")
    assert store.runs(task.task_id)[0].delivery_status == "unknown"


def test_resume_expired_once(tmp_path):
    store = ScheduledTaskStore(tmp_path / "schedule.db")
    draft = store.create_draft(
        tenant_key="tenant-a",
        owner_keys=("user_id:alice",),
        chat_id="chat",
        instruction="check",
        schedule=Schedule.once(100),
        now=1,
    )
    task = store.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key="tenant-a", now=1)
    task = store.set_state(
        task.task_id, task.owner_keys, "paused", expected_revision=task.revision, tenant_key="tenant-a", now=2
    )
    task = store.set_state(
        task.task_id, task.owner_keys, "active", expected_revision=task.revision, tenant_key="tenant-a", now=101
    )
    assert task.state == "completed"
    assert [run.status for run in store.runs(task.task_id)] == ["missed"]
    assert store.claim_due(now=101) is None
    task = store.set_state(
        task.task_id, task.owner_keys, "deleted", tenant_key=task.tenant_key, expected_revision=task.revision, now=102
    )
    assert task.state == "deleted"


def test_missed_notice_delivered_once(tmp_path):
    store = ScheduledTaskStore(tmp_path / "schedule.db")
    draft = store.create_draft(
        tenant_key="tenant-a",
        owner_keys=("user_id:alice",),
        chat_id="chat",
        instruction="check",
        schedule=Schedule.once(100),
        now=1,
    )
    task = store.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key="tenant-a", now=1)
    task = store.set_state(
        task.task_id, task.owner_keys, "paused", tenant_key=task.tenant_key, expected_revision=task.revision, now=2
    )
    store.set_state(
        task.task_id, task.owner_keys, "active", tenant_key=task.tenant_key, expected_revision=task.revision, now=101
    )
    sent = []

    async def execute(_task, _run):
        raise AssertionError("missed task must not execute")

    async def notify(_task, _run, result):
        sent.append(result.status)

    runner = ScheduledTaskRunner(store, execute, notify, clock=lambda: 101)
    asyncio.run(runner.run_once())
    asyncio.run(runner.run_once())
    assert sent == ["missed"]


def test_timezone_change_updates_due(tmp_path):
    store = ScheduledTaskStore(tmp_path / "schedule.db")
    now = stamp("2026-01-01T00:00:00")
    draft = store.create_draft(
        tenant_key="tenant-a",
        owner_keys=("user_id:alice",),
        chat_id="chat",
        instruction="check",
        schedule=Schedule.calendar(hour=9, minute=0),
        timezone="Asia/Shanghai",
        now=now - 1,
    )
    task = store.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key="tenant-a", now=now)
    store.refresh_timezones(lambda _: "Europe/London", now=now)
    assert store.get(task.task_id).next_run_at == stamp("2026-01-01T09:00:00")


def test_timezone_cache_is_tenant_bound(tmp_path):
    store = ScheduledTaskStore(tmp_path / "schedule.db")
    now = stamp("2026-01-01T00:00:00")
    tasks = []
    for tenant in ("tenant-a", "tenant-b"):
        draft = store.create_draft(
            tenant_key=tenant,
            owner_keys=("user_id:alice",),
            chat_id="chat",
            instruction="check",
            schedule=Schedule.calendar(hour=9, minute=0),
            timezone="Asia/Shanghai",
            now=now - 1,
        )
        tasks.append(
            store.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key=tenant, now=now)
        )

    class Tokens:
        async def user_token_for_tenant(self, tenant_key, user):
            assert user == {"user_id": "alice"}
            assert tenant_key in {"tenant-a", "tenant-b"}
            return "token"

    class OAuth:
        zone = "Europe/London"
        calls = 0

        async def user_info(self, _token):
            self.calls += 1
            return {"tenant_key": "tenant-a", "user_id": "alice", "timezone": self.zone}

    oauth = OAuth()
    instant = [0.0]
    resolver = ScheduledTimezoneResolver(
        Tokens(), SimpleNamespace(oauth=oauth), clock=lambda: instant[0], ttl_seconds=10
    )

    async def scenario():
        assert await resolver(tasks[0]) == "Europe/London"
        assert await resolver(tasks[1]) == "Asia/Shanghai"
        oauth.zone = "America/New_York"
        assert await resolver(tasks[0]) == "Europe/London"
        instant[0] = 11
        assert await resolver(tasks[0]) == "America/New_York"
        assert oauth.calls == 3

    asyncio.run(scenario())


def test_slow_timezone_does_not_block(tmp_path):
    store = ScheduledTaskStore(tmp_path / "schedule.db")
    for schedule in (Schedule.once(100), Schedule.calendar(hour=9, minute=0)):
        draft = store.create_draft(
            tenant_key="tenant-a",
            owner_keys=("user_id:alice",),
            chat_id="chat",
            instruction="check",
            schedule=schedule,
            now=1,
        )
        store.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key="tenant-a", now=1)

    async def scenario():
        async def timezone_for(_task):
            await asyncio.sleep(30)
            return "UTC"

        async def execute(_task, _run):
            return ScheduledExecutionResult("succeeded")

        async def notify(_task, _run, _result):
            return None

        runner = ScheduledTaskRunner(store, execute, notify, clock=lambda: 100, timezone_for=timezone_for)
        result = await asyncio.wait_for(runner.run_once(), timeout=3)
        assert result.status == "succeeded"

    asyncio.run(scenario())


def test_shutdown_preserves_unknown(tmp_path):
    store = ScheduledTaskStore(tmp_path / "schedule.db")
    draft = store.create_draft(
        tenant_key="tenant-a",
        owner_keys=("user_id:alice",),
        chat_id="chat",
        instruction="check",
        schedule=Schedule.once(100),
        now=1,
    )
    task = store.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key="tenant-a", now=1)

    async def scenario():
        started = asyncio.Event()

        async def execute(_task, _run):
            started.set()
            await asyncio.Event().wait()

        async def notify(_task, _run, _result):
            raise AssertionError("cancelled execution should not deliver a result")

        runner = ScheduledTaskRunner(store, execute, notify, clock=lambda: 100)
        await runner.start()
        await started.wait()
        await runner.aclose(deadline=asyncio.get_running_loop().time())

    asyncio.run(scenario())
    assert store.runs(task.task_id)[0].status == "unknown"
    assert store.claim_due(now=100) is None


def test_shutdown_drains_running(tmp_path):
    store = ScheduledTaskStore(tmp_path / "schedule.db")
    draft = store.create_draft(
        tenant_key="tenant-a",
        owner_keys=("user_id:alice",),
        chat_id="chat",
        instruction="check",
        schedule=Schedule.once(100),
        now=1,
    )
    task = store.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key="tenant-a", now=1)

    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()

        async def execute(_task, _run):
            started.set()
            await finish.wait()
            return ScheduledExecutionResult("succeeded")

        async def notify(_task, _run, _result):
            return None

        runner = ScheduledTaskRunner(store, execute, notify, clock=lambda: 100)
        await runner.start()
        await started.wait()
        closing = asyncio.create_task(runner.aclose(deadline=asyncio.get_running_loop().time() + 1))
        await asyncio.sleep(0)
        finish.set()
        await closing

    asyncio.run(scenario())
    assert store.runs(task.task_id)[0].status == "succeeded"


def test_shutdown_timeout_recovers(tmp_path):
    store = ScheduledTaskStore(tmp_path / "schedule.db")
    draft = store.create_draft(
        tenant_key="tenant-a",
        owner_keys=("user_id:alice",),
        chat_id="chat",
        instruction="check",
        schedule=Schedule.once(100),
        now=1,
    )
    store.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key="tenant-a", now=1)

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def execute(_task, _run):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return ScheduledExecutionResult("succeeded")

        async def notify(_task, _run, _result):
            return None

        runner = ScheduledTaskRunner(store, execute, notify, clock=lambda: 100)
        await runner.start()
        await started.wait()
        assert not await runner.aclose(deadline=asyncio.get_running_loop().time())
        release.set()
        assert await runner.aclose(deadline=asyncio.get_running_loop().time() + 1)

    asyncio.run(scenario())


def test_callback_wins_initial_race(tmp_path):
    store = ScheduledTaskStore(tmp_path / "schedule.db")
    draft = store.create_draft(
        tenant_key="tenant-a",
        owner_keys=("user_id:alice",),
        chat_id="chat",
        instruction="check",
        schedule=Schedule.once(100),
        now=1,
    )
    store.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key="tenant-a", now=1)

    async def execute(_task, run):
        store.finish_run(run.run_id, "succeeded", now=100, expected_token=run.lease_token)
        return ScheduledExecutionResult("awaiting_confirmation")

    async def notify(_task, _run, _result):
        raise AssertionError("callback already advanced the run")

    runner = ScheduledTaskRunner(store, execute, notify, clock=lambda: 100)
    assert asyncio.run(runner.run_once()).status == "succeeded"


def test_store_permissions(tmp_path):
    path = tmp_path / "schedule.db"
    path.touch(mode=0o644)
    os.chmod(path, 0o644)
    ScheduledTaskStore(path)
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("outcome, expected", [("expired", "failed"), ("missing", "unknown")])
@pytest.mark.parametrize(
    "kind, waiting", [("approval", "awaiting_confirmation"), ("authorization", "awaiting_authorization")]
)
def test_expired_wait(tmp_path, outcome, expected, kind, waiting):
    store = ScheduledTaskStore(tmp_path / "schedule.db")
    draft = store.create_draft(
        tenant_key="tenant-a",
        owner_keys=("user_id:alice",),
        chat_id="chat",
        instruction="check",
        schedule=Schedule.once(100),
        now=1,
    )
    store.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key="tenant-a", now=1)
    now = [100.0]

    async def execute(_task, run):
        store.record_wait(run.run_id, kind, "ap_1", 1900, expected_token=run.lease_token, now=100)
        return ScheduledExecutionResult(waiting)

    async def notify(_task, _run, _result):
        return None

    async def expire(approval_id, _now):
        assert approval_id == "ap_1"
        return outcome

    runner = ScheduledTaskRunner(
        store, execute, notify, clock=lambda: now[0], expire_approval=expire, expire_authorization=expire
    )
    assert asyncio.run(runner.run_once()).status == waiting
    now[0] = 1900
    assert asyncio.run(runner.run_once()).status == expected
