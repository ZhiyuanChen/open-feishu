from __future__ import annotations

import pytest

from feishu.agent.llm import Message, TextPart
from feishu.agent.persistence import SqliteSessionStore
from feishu.agent.replies import ScheduledTurn
from feishu.agent.scheduled import Schedule, ScheduledTaskStore
from feishu.agent.scheduled_session import current_fenced_session, fenced_session_scope


async def test_expired_lease_cannot_write(tmp_path):
    path = tmp_path / "agent.db"
    sessions, schedules = SqliteSessionStore(path), ScheduledTaskStore(path)
    draft = schedules.create_draft(
        tenant_key="t",
        owner_keys=("user_id:alice",),
        chat_id="chat",
        instruction="check",
        schedule=Schedule.once(100),
        now=1,
    )
    schedules.confirm_draft(draft.draft_id, ("user_id:alice",), draft.payload_hash, tenant_key="t", now=2)
    run = schedules.claim_due(now=100, lease_seconds=10)
    turn = ScheduledTurn(
        chat_id="chat",
        user={"user_id": "alice"},
        tenant_key="t",
        session_id=run.session_id,
        run_id=run.run_id,
        policy="read_only",
        lease_token=run.lease_token,
    )
    now = [100]
    message = Message(role="user", content=[TextPart(text="check")])
    with fenced_session_scope(sessions, schedules, turn, clock=lambda: now[0]):
        await current_fenced_session(sessions).set(run.session_id, [message])
        now[0] = 111
        schedules.expire_runs(now=111)
        with pytest.raises(PermissionError):
            await current_fenced_session(sessions).append(run.session_id, message)
    assert len(await sessions.get(run.session_id)) == 1


def test_separate_databases_rejected(tmp_path):
    sessions = SqliteSessionStore(tmp_path / "session.db")
    schedules = ScheduledTaskStore(tmp_path / "schedule.db")
    with pytest.raises(ValueError), fenced_session_scope(sessions, schedules, None):
        pass
