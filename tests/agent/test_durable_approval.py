"""Durable approval admission and request-bound decision/continuation execution."""

from __future__ import annotations

import asyncio
import time

import pytest

from feishu.agent import approval as approval_flow
from feishu.agent.context import current_tool_context
from feishu.agent.integrity import payload_sha256
from feishu.agent.llm import Message, TextPart, ToolResultPart, ToolUsePart
from feishu.agent.loop import AgentEngine
from feishu.agent.persistence import SqlitePendingApprovalStore, SqliteSessionStore
from feishu.agent.scheduled import Schedule, ScheduledTaskStore
from feishu.agent.scheduled_policy import ScheduledPolicyRegistry
from feishu.agent.session import InMemoryPendingApprovalStore, InMemorySessionStore, PendingApproval
from feishu.agent.tools import ToolRegistry
from feishu.events.envelope import Event
from feishu.events.job_runner import EventJobRunner
from feishu.events.jobs import EventJobStatus, InMemoryEventJobStore
from tests._fakes import FakeLlmBackend, text_turn


def _action(*, user_id="owner", decision="approve", digest=None, tenant="tenant", message_id="card"):
    value = {"__approval__": "approval", "decision": decision}
    if digest is not None:
        value["payload_sha256"] = digest
    return Event.from_payload(
        {
            "schema": "2.0",
            "header": {"event_type": "card.action.trigger", "event_id": "click", "tenant_key": tenant},
            "event": {
                "operator": {"user_id": user_id},
                "action": {"value": value},
                "context": {"open_message_id": message_id, "open_chat_id": "chat"},
            },
        }
    )


async def _prepared(*, approvals=None, backend=None, handler=None):
    writes = []

    async def deploy(env):
        writes.append((env, current_tool_context().requesting_user()))
        return "deployed"

    registry = ToolRegistry()
    registry.register(
        "deploy",
        handler or deploy,
        input_schema={"type": "object", "properties": {"env": {"type": "string"}}, "required": ["env"]},
        description="deploy",
        requires_approval=True,
    )
    agent = AgentEngine(
        backend=backend or FakeLlmBackend([text_turn("done")]),
        registry=registry,
        store=InMemorySessionStore(),
        approvals=approvals or InMemoryPendingApprovalStore(),
        # No client or progress builder: durability cannot depend on a visible card.
    )
    pending = PendingApproval(
        approval_id="approval",
        session_id="chat:thread",
        tool_call_id="call",
        tool_name="deploy",
        arguments={"env": "prod"},
        payload_sha256=payload_sha256({"env": "prod"}),
        owner_user_keys=("user_id:owner",),
        tenant_key="tenant",
        chat_id="chat",
        created_message_id="original",
        created_at=int(time.time()),
        extra={"origin_continuation_id": "origin", "progress_message_id": "card"},
    )
    await agent.approvals.put(pending)
    await agent.store.set(
        pending.session_id,
        [
            Message(
                role="assistant",
                content=[ToolUsePart(id="call", name="deploy", arguments={"env": "prod"})],
                continuation_id="origin",
            )
        ],
    )
    return agent, pending, writes


async def test_dispatch_waits_for_durable_acceptance_before_processing_toast():
    agent, pending, writes = await _prepared()
    events = InMemoryEventJobStore()
    started, release = asyncio.Event(), asyncio.Event()
    event = _action(digest=pending.payload_sha256)

    async def dispatch(received, trusted_pending):
        assert trusted_pending.session_id == "chat:thread"
        assert trusted_pending.owner_user_keys == ("user_id:owner",)
        started.set()
        await release.wait()
        await events.enqueue(received.event_id, dict(received.raw), partition_key=trusted_pending.session_id)

    task = asyncio.create_task(approval_flow.handle_card_action(agent, event, decision_dispatcher=dispatch))
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert not task.done()
        assert await events.get("click") is None
    finally:
        release.set()
    response = await task
    assert response["toast"]["type"] == "info"
    assert (await events.get("click")).partition_key == "chat:thread"
    assert writes == []
    assert agent._bg_tasks == set()
    assert (await agent.approvals.get("approval")).state == "awaiting_confirmation"


async def test_dispatch_failure_propagates_without_background_fallback():
    agent, pending, writes = await _prepared()

    async def dispatch(event, trusted_pending):
        raise RuntimeError("durable inbox unavailable")

    with pytest.raises(RuntimeError, match="durable inbox unavailable"):
        await approval_flow.handle_card_action(
            agent, _action(digest=pending.payload_sha256), decision_dispatcher=dispatch
        )
    assert writes == []
    assert agent._bg_tasks == set()
    assert (await agent.approvals.get("approval")).state == "awaiting_confirmation"


@pytest.mark.parametrize(
    "changes",
    [
        {"user_id": "intruder"},
        {"digest": "wrong"},
        {"digest": None},
        {"decision": "invalid"},
        {"tenant": "another-tenant"},
        {"message_id": "another-card"},
    ],
)
async def test_dispatch_rejects_untrusted_decisions_before_persistence(changes):
    agent, pending, writes = await _prepared()
    events = InMemoryEventJobStore()

    async def dispatch(event, trusted_pending):
        await events.enqueue(event.event_id, dict(event.raw), partition_key=trusted_pending.session_id)

    await approval_flow.handle_card_action(
        agent, _action(**{"digest": pending.payload_sha256, **changes}), decision_dispatcher=dispatch
    )
    assert await events.get("click") is None
    assert writes == []
    assert agent._bg_tasks == set()


async def test_dispatch_does_not_accept_expired_pending(tmp_path, request):
    approvals = SqlitePendingApprovalStore(tmp_path / "approvals.db", ttl_seconds=1)
    request.addfinalizer(approvals._db.close)
    agent, pending, writes = await _prepared(approvals=approvals)
    pending.created_at = 1
    await approvals.put(pending)
    accepted = []

    async def dispatch(event, trusted_pending):
        accepted.append(trusted_pending)

    await approval_flow.handle_card_action(agent, _action(digest=pending.payload_sha256), decision_dispatcher=dispatch)
    assert accepted == []
    assert writes == []


@pytest.mark.parametrize("missing", [False, True])
async def test_scheduled_expiry(expired_scheduled_wait, missing):
    agent, store, run, event, writes = await expired_scheduled_wait("approval", missing)
    await approval_flow.process_card_action(agent, event)
    assert writes == []
    assert store.get_run(run.run_id).status == ("unknown" if missing else "failed")


async def test_process_awaits_execution_and_continuation_once():
    agent, pending, writes = await _prepared()
    event = _action(digest=pending.payload_sha256)
    await approval_flow.process_card_action(agent, event)
    await approval_flow.process_card_action(agent, event)
    assert writes == [("prod", {"user_id": "owner"})]
    assert await agent.approvals.get("approval") is None
    history = await agent.store.get("chat:thread")
    assert any(isinstance(part, ToolResultPart) and part.content == "deployed" for m in history for part in m.content)
    assert history[-1].content == [TextPart(text="done")]
    assert len(agent.backend.calls) == 1
    assert agent._bg_tasks == set()


@pytest.mark.parametrize("changes,expected", [({}, 1), ({"user_id": "intruder"}, 0), ({"digest": "wrong"}, 0)])
async def test_scheduled_approval(changes, expected):
    agent, pending, writes = await _prepared()
    agent.registry = ScheduledPolicyRegistry(agent.registry, "confirm_writes")
    event = _action(**{"digest": pending.payload_sha256, **changes})
    await approval_flow.process_card_action(agent, event)
    await approval_flow.process_card_action(agent, event)
    assert len(writes) == expected


@pytest.mark.parametrize("tenant,expected", [("tenant", 1), ("other-tenant", 0)])
async def test_scheduling_approval_tenant(tenant, expected):
    agent, pending, writes = await _prepared()
    tool = agent.registry._tools.pop("deploy")
    tool.name = "create_scheduled_task"
    agent.registry.add(tool)
    pending.tool_name = tool.name
    history = await agent.store.get(pending.session_id)
    history[0].content[0].name = tool.name
    await agent.store.set(pending.session_id, history)
    await approval_flow.handle_card_action(agent, _action(digest=pending.payload_sha256, tenant=tenant))
    await asyncio.gather(*agent._bg_tasks)
    assert len(writes) == expected


@pytest.mark.parametrize("case", ["confirmed", "mismatch", "paused", "preparing"])
async def test_scheduled_resume_restores_policy(tmp_path, case):
    from tests.agent.test_loop import _ApprovalRecordingClient

    agent, pending, writes = await _prepared()
    agent.client = _ApprovalRecordingClient()
    history = await agent.store.get(pending.session_id)
    store = ScheduledTaskStore(tmp_path / "scheduled.db")
    agent.store = SqliteSessionStore(tmp_path / "scheduled.db")
    draft = store.create_draft(
        tenant_key="tenant",
        owner_keys=("user_id:owner",),
        chat_id="chat",
        instruction="check",
        schedule=Schedule.interval(seconds=100, anchor_at=100),
        now=0,
        policy="read_only" if case == "mismatch" else "confirm_writes",
    )
    task = store.confirm_draft(draft.draft_id, ("user_id:owner",), draft.payload_hash, tenant_key="tenant", now=1)
    run = store.claim_due(now=100)
    agent.scheduled_store = store
    pending.session_id = run.session_id
    pending.extra.update(scheduled_policy="confirm_writes", scheduled_run_id=run.run_id)
    await agent.store.set(pending.session_id, history)
    if case == "preparing":
        await agent.approvals.pop(pending.approval_id)
        pending.approval_id = "sched_approval"
        await agent.approvals.put(pending)
    store.record_wait(
        run.run_id,
        "approval",
        pending.approval_id,
        pending.created_at + 1800,
        expected_token=run.lease_token,
        now=100,
    )
    if case != "preparing":
        store.finish_run(run.run_id, "awaiting_confirmation", now=100, expected_token=run.lease_token)
    if case == "paused":
        current = store.get(task.task_id)
        store.set_state(
            task.task_id, ("user_id:owner",), "paused", tenant_key="tenant", expected_revision=current.revision, now=101
        )
    if case == "preparing":
        event = _action(digest=pending.payload_sha256)
        event.body["action"]["value"]["__approval__"] = pending.approval_id
        response = await approval_flow.handle_card_action(agent, event)
        assert "请稍后再试" in response["toast"]["content"]
        assert writes == []
    elif case != "confirmed":
        with pytest.raises(PermissionError, match="active task and run"):
            await approval_flow.process_card_action(agent, _action(digest=pending.payload_sha256))
        assert writes == []
    else:
        await approval_flow.process_card_action(agent, _action(digest=pending.payload_sha256))
        assert writes == [("prod", {"user_id": "owner"})]
        assert store.get_run(run.run_id).status == "succeeded"


async def test_scheduled_resume_requires_policy():
    agent, pending, writes = await _prepared()
    pending.session_id = "scheduled:task:run"
    with pytest.raises(ValueError, match="missing its run or tool policy"):
        await approval_flow.process_card_action(agent, _action(digest=pending.payload_sha256))
    assert writes == []


@pytest.mark.parametrize("changes", [{"user_id": "intruder"}, {"digest": "wrong"}, {"digest": None}])
async def test_process_revalidates_owner_and_payload(changes):
    agent, pending, writes = await _prepared()
    await approval_flow.process_card_action(agent, _action(**{"digest": pending.payload_sha256, **changes}))
    assert writes == []
    assert (await agent.approvals.get("approval")).state == "awaiting_confirmation"
    assert agent.backend.calls == []


async def test_process_propagates_pending_read_failure():
    class UnavailableApprovals(InMemoryPendingApprovalStore):
        async def get(self, approval_id):
            raise RuntimeError("pending store unavailable")

    agent, pending, writes = await _prepared(approvals=UnavailableApprovals())
    with pytest.raises(RuntimeError, match="pending store unavailable"):
        await approval_flow.process_card_action(agent, _action(digest=pending.payload_sha256))
    assert writes == []


async def test_concurrent_processes_share_atomic_approval_claim():
    first, pending, writes = await _prepared()
    second = AgentEngine(
        backend=FakeLlmBackend([text_turn("done")]),
        registry=first.registry,
        store=first.store,
        approvals=first.approvals,
    )
    await asyncio.gather(
        approval_flow.process_card_action(first, _action(digest=pending.payload_sha256)),
        approval_flow.process_card_action(second, _action(digest=pending.payload_sha256, decision="reject")),
    )
    assert len(writes) <= 1
    assert len(first.backend.calls) + len(second.backend.calls) == 1
    history = await first.store.get(pending.session_id)
    assert sum(isinstance(part, ToolResultPart) for message in history for part in message.content) == 1
    assert await first.approvals.get("approval") is None


@pytest.mark.parametrize("change", ["cleared", "origin", "arguments"])
async def test_process_refuses_replaced_original_tool_call(change):
    agent, pending, writes = await _prepared()
    history = await agent.store.get(pending.session_id)
    if change == "cleared":
        history = []
    elif change == "origin":
        history[0].continuation_id = "different-origin"
    else:
        history[0].content = [ToolUsePart(id="call", name="deploy", arguments={"env": "staging"})]
    await agent.store.set(pending.session_id, history)
    await approval_flow.process_card_action(agent, _action(digest=pending.payload_sha256))
    assert writes == []
    assert await agent.approvals.get("approval") is None
    assert agent.backend.calls == []


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_continuation_failure_is_unknown_in_worker_without_progress_card(decision):
    agent, pending, writes = await _prepared(backend=FakeLlmBackend([]))
    event = _action(digest=pending.payload_sha256, decision=decision)
    events = InMemoryEventJobStore()
    await events.enqueue(event.event_id, dict(event.raw), partition_key=pending.session_id)

    async def process(received):
        await approval_flow.process_card_action(agent, received)

    job = await EventJobRunner(events, process, worker_id="worker").run_once()
    assert job.status is EventJobStatus.UNKNOWN
    assert job.error_type == "AssertionError"
    assert job.side_effect_started
    assert writes == ([("prod", {"user_id": "owner"})] if decision == "approve" else [])


async def test_effect_marker_failure_prevents_claim_and_tool_execution():
    class UnavailableMarker(InMemoryEventJobStore):
        async def mark_side_effect_started(self, job_id, worker_id):
            raise RuntimeError("lease fence unavailable")

    agent, pending, writes = await _prepared()
    event = _action(digest=pending.payload_sha256)
    events = UnavailableMarker()
    await events.enqueue(event.event_id, dict(event.raw), partition_key=pending.session_id)

    async def process(received):
        await approval_flow.process_card_action(agent, received)

    job = await EventJobRunner(events, process, worker_id="worker").run_once()
    assert job.status is EventJobStatus.RETRY_WAIT
    assert job.error_type == "RuntimeError"
    assert not job.side_effect_started
    assert writes == []
    assert (await agent.approvals.get("approval")).state == "awaiting_confirmation"


async def test_cancellation_during_tool_freezes_worker_and_pending_without_progress():
    started = asyncio.Event()

    async def deploy(env):
        started.set()
        await asyncio.Event().wait()

    agent, pending, _ = await _prepared(handler=deploy)
    event = _action(digest=pending.payload_sha256)
    events = InMemoryEventJobStore()
    await events.enqueue(event.event_id, dict(event.raw), partition_key=pending.session_id)

    async def process(received):
        await approval_flow.process_card_action(agent, received)

    task = asyncio.create_task(EventJobRunner(events, process, worker_id="worker").run_once())
    try:
        await asyncio.wait_for(started.wait(), 1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert (await events.get("click")).status is EventJobStatus.UNKNOWN
    assert (await agent.approvals.get("approval")).state == "execution_unknown"


async def test_stale_provenance_cannot_consume_pending_when_fence_fails():
    class UnavailableMarker(InMemoryEventJobStore):
        async def mark_side_effect_started(self, job_id, worker_id):
            raise RuntimeError("lease fence unavailable")

    agent, pending, writes = await _prepared()
    await agent.store.clear(pending.session_id)
    event = _action(digest=pending.payload_sha256)
    events = UnavailableMarker()
    await events.enqueue(event.event_id, dict(event.raw), partition_key=pending.session_id)

    async def process(received):
        await approval_flow.process_card_action(agent, received)

    job = await EventJobRunner(events, process, worker_id="worker").run_once()
    assert job.status is EventJobStatus.RETRY_WAIT
    assert job.error_type == "RuntimeError"
    assert (await agent.approvals.get("approval")).state == "awaiting_confirmation"
    assert writes == []
