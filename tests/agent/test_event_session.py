from __future__ import annotations

import pytest

from feishu.agent.event_session import current_event_session, fenced_event_session_scope
from feishu.agent.llm import Message, TextPart
from feishu.agent.persistence import SqliteSessionStore
from feishu.agent.replies import ScheduledTurn
from feishu.agent.scheduled import Schedule, ScheduledTaskStore
from feishu.agent.scheduled_session import fenced_session_scope
from feishu.events.jobs import EventJobContext, EventJobLeaseError, SqliteEventJobStore


@pytest.mark.parametrize("expired", ["event", "scheduled"])
async def test_both_leases_required(tmp_path, expired):
    path = tmp_path / "agent.db"
    jobs = SqliteEventJobStore(path, unknown_ttl_seconds=None)
    sessions = SqliteSessionStore(path)
    schedules = ScheduledTaskStore(path)
    owner = ("user_id:alice",)
    draft = schedules.create_draft(
        tenant_key="t", owner_keys=owner, chat_id="chat", instruction="check", schedule=Schedule.once(100), now=1
    )
    schedules.confirm_draft(draft.draft_id, owner, draft.payload_hash, tenant_key="t", now=2)
    run = schedules.claim_due(now=100, lease_seconds=5 if expired == "scheduled" else 60)
    assert run is not None
    await jobs.enqueue("callback", {}, partition_key=run.session_id, now=100)
    job = await jobs.claim_next("worker", now=100, lease_seconds=5 if expired == "event" else 60)
    assert job is not None and job.lease_token
    clock = [100.0]
    context = EventJobContext(
        jobs,
        job.job_id,
        job.event_id,
        "worker",
        lease_token=job.lease_token,
        partition_key=run.session_id,
        clock=lambda: clock[0],
    )
    turn = ScheduledTurn(
        chat_id="chat",
        user={"user_id": "alice"},
        tenant_key="t",
        session_id=run.session_id,
        run_id=run.run_id,
        policy="read_only",
        lease_token=run.lease_token,
    )
    message = Message(role="user", content=[TextPart(text="check")])
    try:
        with (
            fenced_event_session_scope(sessions, context, run.session_id),
            fenced_session_scope(sessions, schedules, turn, clock=lambda: clock[0]),
        ):
            view = current_event_session(sessions)
            await view.set(run.session_id, [message])
            clock[0] = 106
            with pytest.raises((PermissionError, EventJobLeaseError)):
                await view.append(run.session_id, message)
        assert len(await sessions.get(run.session_id)) == 1
    finally:
        await jobs.close()
        sessions._db.close()
