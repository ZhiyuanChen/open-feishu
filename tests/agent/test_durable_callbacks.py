from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from feishu.agent.integrity import payload_sha256
from feishu.agent.llm import Message, ToolUsePart
from feishu.agent.loop import AgentEngine
from feishu.agent.oauth import oauth_callback_handler
from feishu.agent.persistence import SqlitePendingApprovalStore, SqlitePendingAuthorizationStore
from feishu.agent.session import PendingApproval, PendingAuthorization
from feishu.agent.tools import ToolRegistry
from feishu.auth import OAuthStateSigner, SqliteOAuthTokenStore
from feishu.events.envelope import Event
from feishu.events.job_runner import EventJobRunner
from feishu.events.jobs import EventJobStatus, SqliteEventJobStore
from tests._fakes import FakeLlmBackend, text_turn


async def test_private_message_interrupts(tmp_path):
    from feishu.agent.durable_callbacks import SqliteDurableCallbacks
    from feishu.agent.persistence import SqliteSessionStore
    from feishu.agent.registration import create_agent_dispatcher

    started = asyncio.Event()

    class Model:
        async def stream(self, **_kwargs):
            started.set()
            await asyncio.Event().wait()
            yield None

    class IM:
        async def send(self, *_args, **_kwargs):
            return {"message_id": "progress"}

        async def patch(self, *_args, **_kwargs):
            return {}

    path = tmp_path / "callbacks.db"
    jobs = SqliteEventJobStore(path, unknown_ttl_seconds=None)
    tokens = SqliteOAuthTokenStore(path)
    authorizations = SqlitePendingAuthorizationStore(path)
    sessions = SqliteSessionStore(path)
    agent = AgentEngine(
        backend=Model(),
        registry=ToolRegistry(),
        store=sessions,
        client=SimpleNamespace(im=IM()),
        progress_card_builder=lambda *_: {},
    )
    inbox = SqliteDurableCallbacks(jobs, tokens, authorizations, interrupt_private=True)
    facade = SimpleNamespace(engine=agent, dispatcher=lambda: create_agent_dispatcher(agent))
    first = {
        "schema": "2.0",
        "header": {"event_type": "im.message.receive_v1", "event_id": "first"},
        "event": {
            "message": {
                "chat_id": "chat",
                "chat_type": "p2p",
                "message_id": "m1",
                "message_type": "text",
                "content": '{"text":"hello"}',
            }
        },
    }
    second = {**first, "header": {**first["header"], "event_id": "second"}}
    await inbox.enqueue("first", first, partition_key="chat")
    running = asyncio.create_task(inbox.runner(facade, worker_id="worker-a").run_once())
    try:
        await asyncio.wait_for(started.wait(), 2)
        app_message = {
            **first,
            "header": {**first["header"], "event_id": "app"},
            "event": {**first["event"], "sender": {"sender_type": "app"}},
        }
        assert not await inbox.enqueue("app", app_message, partition_key="chat")
        await asyncio.sleep(0.05)
        assert not running.done()
        await inbox.enqueue("second", second, partition_key="chat")
        old = await asyncio.wait_for(running, 2)
        new = await jobs.claim_next("worker-b")
        assert old is not None and old.status is EventJobStatus.COMPLETED
        assert new is not None and new.payload["data"]["header"]["event_id"] == "second"
    finally:
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        for store in (jobs, tokens, authorizations, sessions):
            store._db.close()


async def test_early_callback_retries(tmp_path):
    from feishu.agent.durable_callbacks import SqliteDurableCallbacks
    from feishu.agent.persistence import SqliteSessionStore
    from feishu.agent.scheduled import Schedule, ScheduledTaskStore

    path = tmp_path / "callbacks.db"
    jobs = SqliteEventJobStore(path, unknown_ttl_seconds=None)
    tokens, authorizations = SqliteOAuthTokenStore(path), SqlitePendingAuthorizationStore(path)
    approvals, sessions = SqlitePendingApprovalStore(path), SqliteSessionStore(path)
    schedules = ScheduledTaskStore(path)
    now = time.time()
    owner = ("open_id:owner",)
    draft = schedules.create_draft(
        tenant_key="tenant",
        owner_keys=owner,
        chat_id="chat",
        instruction="check",
        schedule=Schedule.once(now + 1),
        now=now,
    )
    schedules.confirm_draft(draft.draft_id, owner, draft.payload_hash, tenant_key="tenant", now=now)
    run = schedules.claim_due(now=now + 1)
    assert run is not None
    pending = PendingApproval(
        approval_id="sched_early",
        session_id=run.session_id,
        tool_call_id="call",
        tool_name="write",
        arguments={},
        payload_sha256=payload_sha256({}),
        owner_user_keys=owner,
        tenant_key="tenant",
        chat_id="chat",
        created_at=int(now),
        extra={"progress_message_id": "card", "scheduled_policy": "read_only", "scheduled_run_id": run.run_id},
    )
    await approvals.put(pending)
    schedules.record_wait(
        run.run_id, "approval", pending.approval_id, now + 1800, expected_token=run.lease_token, now=now + 1
    )
    inbox = SqliteDurableCallbacks(jobs, tokens, authorizations, approvals=approvals, interrupt_private=True)
    event = Event.from_payload(
        {
            "header": {"event_type": "card.action.trigger", "event_id": "click", "tenant_key": "tenant"},
            "event": {
                "operator": {"open_id": "owner"},
                "action": {
                    "value": {
                        "__approval__": pending.approval_id,
                        "decision": "approve",
                        "payload_sha256": pending.payload_sha256,
                    }
                },
                "context": {"open_message_id": "card", "open_chat_id": "chat"},
            },
        }
    )
    await inbox.dispatch_approval(event, pending)
    agent = AgentEngine(
        backend=FakeLlmBackend([]),
        registry=ToolRegistry(),
        store=sessions,
        approvals=approvals,
        scheduled_store=schedules,
        now=lambda: now + 1,
    )
    runner = EventJobRunner(
        jobs,
        lambda payload: inbox.dispatch(payload, agent=agent, dispatcher=None),
        worker_id="worker",
        payload_decoder=lambda payload: payload,
        protected=True,
    )
    try:
        result = await runner.run_once()
        assert result is not None and result.status is EventJobStatus.RETRY_WAIT
        assert not result.side_effect_started
    finally:
        for store in (jobs, tokens, authorizations, approvals, sessions):
            store._db.close()


@pytest.fixture
def callback_state(tmp_path):
    path = tmp_path / "callbacks.db"
    jobs = SqliteEventJobStore(path)
    tokens = SqliteOAuthTokenStore(path)
    authorizations = SqlitePendingAuthorizationStore(path)
    approvals = SqlitePendingApprovalStore(path)
    yield SimpleNamespace(jobs=jobs, tokens=tokens, authorizations=authorizations, approvals=approvals)
    for store in (jobs, tokens, authorizations, approvals):
        store._db.close()


@pytest.mark.parametrize("mode", ["ok", "reject_commit", "expired"])
async def test_oauth_acceptance(callback_state, mode):
    from feishu.agent.durable_callbacks import SqliteDurableCallbacks

    stores = callback_state
    pending = PendingAuthorization(
        authorization_id="pending",
        session_id="namespace::chat",
        tool_call_id="call",
        tool_name="read",
        arguments={},
        owner_user_keys=("open_id:owner",),
        tenant_key="tenant",
        chat_id="chat",
        created_at=int(time.time()) - (7200 if mode == "expired" else 0),
    )
    await stores.authorizations.put(pending)
    exchange = AsyncMock(return_value={"access_token": "fake-access"})
    user_info = AsyncMock(return_value={"tenant_key": "tenant", "open_id": "owner"})
    provider = SimpleNamespace(
        client=SimpleNamespace(oauth=SimpleNamespace(exchange_code=exchange, user_info=user_info)), store=stores.tokens
    )
    signer = OAuthStateSigner("fake-state-key")
    state = signer.issue(user_keys=("open_id:owner",), tenant_key="tenant", extra={"authorization_id": "pending"})
    receiver = SqliteDurableCallbacks(stores.jobs, stores.tokens, stores.authorizations)
    if mode == "reject_commit":
        stores.jobs._db.execute(
            "CREATE TRIGGER reject_resume BEFORE INSERT ON event_jobs BEGIN SELECT RAISE(ABORT, 'reject'); END"
        )
        stores.jobs._db.commit()
    handler = oauth_callback_handler(provider, signer, "https://example.invalid/callback", durable_receiver=receiver)
    request = SimpleNamespace(query_params={"code": "one-shot-code", "state": state})
    response = await handler(request)
    if mode == "expired":
        assert response.status_code == 403
        assert await stores.tokens.get_for_tenant("tenant", {"open_id": "owner"}) is None
        assert await stores.jobs.claim_next("worker") is None
        assert exchange.await_count == 0
        return
    if mode == "reject_commit":
        assert response.status_code == 503
        assert await stores.tokens.get_for_tenant("tenant", {"open_id": "owner"}) is None
        assert await stores.jobs.claim_next("worker") is None
        assert (await handler(request)).status_code != 200
    else:
        assert response.status_code == 200
        assert await stores.tokens.get_for_tenant("tenant", {"open_id": "owner"}) is not None
        job = await stores.jobs.claim_next("worker")
        assert job.partition_key == "namespace::chat"
        assert job.payload["kind"] == "authorization"
        assert "fake-access" not in str(job.payload) and "one-shot-code" not in str(job.payload)
        assert (await handler(request)).status_code == 200
    assert exchange.await_count == 1


async def test_approval_acceptance(callback_state):
    from feishu.agent.durable_callbacks import SqliteDurableCallbacks

    stores = callback_state
    digest = payload_sha256({})
    pending = PendingApproval(
        approval_id="approval",
        session_id="namespace::chat",
        tool_call_id="call",
        tool_name="write",
        arguments={},
        payload_sha256=digest,
        owner_user_keys=("open_id:owner",),
        tenant_key="tenant",
        chat_id="chat",
        created_at=int(time.time()),
        extra={"progress_message_id": "card", "origin_continuation_id": "origin"},
    )
    await stores.approvals.put(pending)
    inbox = SqliteDurableCallbacks(stores.jobs, stores.tokens, stores.authorizations, approvals=stores.approvals)

    def action(*, decision="approve", tenant="tenant"):
        return Event.from_payload(
            {
                "header": {"event_type": "card.action.trigger", "event_id": "click", "tenant_key": tenant},
                "event": {
                    "token": "fake-callback-token",
                    "operator": {"open_id": "owner"},
                    "action": {
                        "value": {
                            "__approval__": "approval",
                            "decision": decision,
                            "payload_sha256": digest,
                        }
                    },
                    "context": {"open_message_id": "card", "open_chat_id": "chat"},
                },
            }
        )

    with pytest.raises(PermissionError):
        await inbox.dispatch_approval(action(tenant="other"), pending)
    fixed_tenant_agent = AgentEngine(
        backend=FakeLlmBackend([]), registry=ToolRegistry(), approvals=stores.approvals, trusted_tenant_key="tenant"
    )
    response = await inbox.card_handler(fixed_tenant_agent)(action(tenant=None))
    assert response["toast"]["content"] == "处理中…"
    await inbox.dispatch_approval(action(), pending)
    await inbox.dispatch_approval(action(), pending)
    with pytest.raises(ValueError):
        await inbox.dispatch_approval(action(decision="reject"), pending)
    calls = []
    registry = ToolRegistry()
    registry.register("write", lambda: calls.append("written"), description="write", input_schema={"type": "object"})
    agent = AgentEngine(backend=FakeLlmBackend([text_turn("done")]), registry=registry, approvals=stores.approvals)
    await agent.store.set(
        "namespace::chat",
        [
            Message(
                role="assistant", content=[ToolUsePart(id="call", name="write", arguments={})], continuation_id="origin"
            )
        ],
    )
    runner = EventJobRunner(
        stores.jobs,
        lambda payload: inbox.dispatch(payload, agent=agent, dispatcher=None),
        worker_id="worker",
        payload_decoder=lambda payload: payload,
    )
    job = await runner.run_once()
    assert job.partition_key == "namespace::chat" and job.payload["kind"] == "approval"
    assert "fake-callback-token" not in str(job.payload)
    assert job.status.value == "completed" and calls == ["written"]
    await inbox.enqueue(
        "message", {"token": "secret", "header": {"token": "secret"}, "event": {}}, partition_key="namespace::chat"
    )
    event_job = await stores.jobs.claim_next("worker")
    assert event_job.payload["kind"] == "event" and "secret" not in str(event_job.payload)


async def test_expired_approval_rejected(callback_state):
    from feishu.agent.durable_callbacks import SqliteDurableCallbacks

    stores = callback_state
    pending = PendingApproval(
        approval_id="expired",
        session_id="scheduled:task:run",
        tool_call_id="call",
        tool_name="write",
        arguments={},
        payload_sha256=payload_sha256({}),
        owner_user_keys=("open_id:owner",),
        tenant_key="tenant",
        chat_id="chat",
        created_at=int(time.time()) - 1801,
        extra={"progress_message_id": "card"},
    )
    await stores.approvals.put(pending)
    inbox = SqliteDurableCallbacks(stores.jobs, stores.tokens, stores.authorizations, approvals=stores.approvals)
    event = Event.from_payload(
        {
            "header": {"event_type": "card.action.trigger", "event_id": "click", "tenant_key": "tenant"},
            "event": {
                "operator": {"open_id": "owner"},
                "action": {
                    "value": {
                        "__approval__": "expired",
                        "decision": "approve",
                        "payload_sha256": pending.payload_sha256,
                    }
                },
                "context": {"open_message_id": "card", "open_chat_id": "chat"},
            },
        }
    )
    with pytest.raises(PermissionError):
        await inbox.dispatch_approval(event, pending)
    assert await stores.jobs.claim_next("worker") is None


async def test_old_jobs_rejected(callback_state):
    from feishu.agent.durable_callbacks import SqliteDurableCallbacks

    stores = callback_state
    await stores.jobs.enqueue("old", {"event": {}}, partition_key="chat")
    with pytest.raises(ValueError, match="drain old"):
        SqliteDurableCallbacks(stores.jobs, stores.tokens, stores.authorizations)
