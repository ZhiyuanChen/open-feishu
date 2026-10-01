"""Approval/OAuth races, cache identity, and session continuation provenance."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from feishu.agent.approval import ApprovalStatus, DefaultApprovalEngine, request_approval
from feishu.agent.context import ToolContext, use_tool_context
from feishu.agent.integrity import payload_sha256
from feishu.agent.llm import ToolCall, ToolUsePart
from feishu.agent.loop import AgentEngine
from feishu.agent.oauth import cancel_pending_authorization
from feishu.agent.persistence import (
    SqliteExecutionResultStore,
    SqlitePendingApprovalStore,
    SqlitePendingAuthorizationStore,
    SqliteSessionStore,
)
from feishu.agent.result import ToolOutcome, ToolResult
from feishu.agent.session import PendingApproval, PendingAuthorization
from feishu.agent.tools import ToolRegistry
from tests._fakes import FakeLlmBackend, text_turn, tool_turn
from tests.agent.test_loop import _action_event, _ApprovalRecordingClient, _drain, _text_event

SCHEMA = {"type": "object", "properties": {"env": {"type": "string"}}, "required": ["env"]}


@pytest.fixture
def durable_stores(tmp_path):
    path = tmp_path / "agent.db"
    stores = SimpleNamespace(
        approvals=SqlitePendingApprovalStore(path),
        authorizations=SqlitePendingAuthorizationStore(path),
        executions=SqliteExecutionResultStore(path),
        sessions=SqliteSessionStore(path),
    )
    yield stores
    for store in vars(stores).values():
        store._db.close()


class NoProgress:
    message_id = None

    async def replace_with_card(self, card):
        return None


@pytest.mark.parametrize("uncertain", [False, True])
async def test_card_send_metadata_never_restores_resolved_or_frozen_approval(durable_stores, uncertain):
    """A click processed before send returns must remain consumed or frozen."""
    stores = durable_stores
    engine = DefaultApprovalEngine(approvals=stores.approvals, executions=stores.executions)
    proposals = []
    writes = []

    def card_builder(approval):
        proposals.append(approval)
        return {"id": approval.approval_id, "hash": approval.payload_sha256}

    async def dispatch(name, args):
        writes.append((name, args))
        if uncertain:
            raise RuntimeError("transport failed after the write")
        return ToolResult(ToolOutcome.COMPLETED, "done")

    class IM:
        async def send(self, chat_id, card, **kwargs):
            outcome = await engine.on_decision(
                card["id"], "approve", expected_payload_sha256=card["hash"], dispatch=dispatch
            )
            assert outcome.status == (ApprovalStatus.FROZEN if uncertain else ApprovalStatus.EXECUTED)
            return {"message_id": "approval_card"}

    agent = SimpleNamespace(
        approvals=stores.approvals,
        approval_engine=engine,
        client=SimpleNamespace(im=IM()),
        _approval_card_builder=card_builder,
        shared_files=None,
    )
    with use_tool_context(ToolContext(user={"open_id": "ou_tester"})):
        assert await request_approval(
            agent, _text_event(), "oc_1", [], ToolCall("call", "deploy", '{"env":"prod"}'), NoProgress()
        )
    pending = await stores.approvals.get(proposals[0].approval_id)
    if uncertain:
        assert pending is not None and pending.state == "execution_unknown"
        assert pending.extra["progress_message_id"] == "approval_card"
    else:
        assert pending is None
    await engine.on_decision(
        proposals[0].approval_id,
        "approve",
        expected_payload_sha256=proposals[0].payload_sha256,
        dispatch=dispatch,
    )
    assert len(writes) == 1


@pytest.mark.parametrize("second_name,second_call", [("overwrite", "call_1"), ("append", "call_2")])
async def test_auto_execution_cache_distinguishes_tools_and_logical_calls(durable_stores, second_name, second_call):
    stores = durable_stores
    engine = DefaultApprovalEngine(approvals=stores.approvals, executions=stores.executions)
    writes = []

    async def dispatch(name, args):
        writes.append(name)
        return ToolResult(ToolOutcome.COMPLETED, name)

    for index, (name, call_id) in enumerate([("append", "call_1"), (second_name, second_call)]):
        pending = PendingApproval(
            f"ap_{index}",
            "oc_1",
            call_id,
            name,
            {"env": "prod"},
            payload_sha256=payload_sha256({"env": "prod"}),
            created_message_id="same_message",
        )
        await engine.on_request(pending)
        outcome = await engine.on_decision(
            pending.approval_id, "approve", expected_payload_sha256=pending.payload_sha256, dispatch=dispatch
        )
        assert outcome.status == ApprovalStatus.EXECUTED
        assert outcome.content == name
    assert writes == ["append", second_name]


async def test_explicit_key_deduplicates_same_tool_but_cannot_replay_other_tool(durable_stores):
    stores = durable_stores
    engine = DefaultApprovalEngine(approvals=stores.approvals, executions=stores.executions)
    writes = []

    async def dispatch(name, args):
        writes.append(name)
        return ToolResult(ToolOutcome.COMPLETED, name)

    for index, name in enumerate(["append", "append", "overwrite"]):
        pending = PendingApproval(
            f"ap_{index}",
            "oc_1",
            f"call_{index}",
            name,
            {"env": "prod"},
            payload_sha256=payload_sha256({"env": "prod"}),
            idempotency_key="explicit_request",
        )
        await engine.on_request(pending)
        assert pending.idempotency_key == "explicit_request"
        outcome = await engine.on_decision(
            pending.approval_id, "approve", expected_payload_sha256=pending.payload_sha256, dispatch=dispatch
        )
        assert outcome.status == (ApprovalStatus.REPLAYED if index == 1 else ApprovalStatus.EXECUTED)
        assert outcome.content == name
    assert writes == ["append", "overwrite"]


async def test_unbound_legacy_cache_never_replays_without_tool_provenance(durable_stores):
    stores = durable_stores
    sha = payload_sha256({"env": "prod"})
    stores.executions.put("explicit_request", execution_status="executed", result="other tool", payload_sha256=sha)
    engine = DefaultApprovalEngine(approvals=stores.approvals, executions=stores.executions)
    pending = PendingApproval(
        "ap", "oc_1", "call", "deploy", {"env": "prod"}, payload_sha256=sha, idempotency_key="explicit_request"
    )
    await engine.on_request(pending)
    writes = []

    async def dispatch(name, args):
        writes.append(name)
        return ToolResult(ToolOutcome.COMPLETED, "deployed")

    outcome = await engine.on_decision("ap", "approve", expected_payload_sha256=sha, dispatch=dispatch)
    assert outcome.status == ApprovalStatus.EXECUTED
    assert outcome.content == "deployed"
    assert writes == ["deploy"]


@pytest.mark.parametrize("authorization", [False, True])
@pytest.mark.parametrize("history_change", ["clear", "wrong_name", "wrong_arguments"])
async def test_stale_continuation_cannot_execute_or_append_orphan_result(durable_stores, authorization, history_change):
    stores = durable_stores
    proposals = []
    client = _ApprovalRecordingClient()
    writes = []
    registry = ToolRegistry()

    async def deploy(env):
        writes.append(env)
        return "deployed"

    registry.register(
        "deploy",
        deploy,
        description="deploy",
        input_schema=SCHEMA,
        requires_approval=not authorization,
        auth_scopes=("scope",) if authorization else (),
    )
    backend = FakeLlmBackend([tool_turn(id="call", name="deploy", arguments_json='{"env":"prod"}'), text_turn("done")])
    agent = AgentEngine(
        backend=backend,
        registry=registry,
        client=client,
        store=stores.sessions,
        approvals=stores.approvals,
        authorizations=stores.authorizations,
        approval_card_builder=lambda pending: proposals.append(pending) or {"id": pending.approval_id},
        auth_card_builder=lambda url: {"url": url},
        authorize_url_builder=lambda user, scopes, pending: proposals.append(pending) or "https://example.invalid/auth",
        clear_command=lambda text: text == "/clear",
    )
    await agent.run(_text_event("deploy"))
    pending = proposals[0]
    if history_change == "clear":
        await agent.run(_text_event("/clear", message_id="clear"))
    else:
        history = await stores.sessions.get("oc_1")
        for message in history:
            for part in message.content:
                if isinstance(part, ToolUsePart):
                    if history_change == "wrong_name":
                        part.name = "unrelated_tool"
                    else:
                        part.arguments = {"env": "staging"}
        await stores.sessions.set("oc_1", history)
    before = await stores.sessions.get("oc_1")
    if authorization:
        status = await agent.resume_authorization(pending.authorization_id, user={"open_id": "ou_tester"})
        assert status in ("expired", "superseded")
        assert await stores.authorizations.get(pending.authorization_id) is None
    else:
        await agent.handle_card_action(
            _action_event(pending.approval_id, "approve", payload_sha256=pending.payload_sha256)
        )
        await _drain(agent)
        assert await stores.approvals.get(pending.approval_id) is None
    assert writes == []
    assert len(backend.calls) == 1
    assert await stores.sessions.get("oc_1") == before
    assert client.patches
    assert any("过期" in str(card) or "替代" in str(card) for _, card in client.patches)


@pytest.mark.parametrize("authorization", [False, True])
@pytest.mark.parametrize("replace_progress_card", [False, True])
async def test_superseding_message_cleans_cancelled_card_send_pending(
    durable_stores, authorization, replace_progress_card
):
    stores = durable_stores
    proposals = []
    started = asyncio.Event()
    client = _ApprovalRecordingClient()
    writes = []
    registry = ToolRegistry()

    async def deploy(env):
        writes.append(env)
        return "done"

    registry.register(
        "deploy",
        deploy,
        description="deploy",
        input_schema=SCHEMA,
        requires_approval=not authorization,
        auth_scopes=("scope",) if authorization else (),
    )

    async def blocked_delivery(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    if replace_progress_card:
        original_patch = client.im.patch

        async def patch(message_id, card):
            if "id" in card or "url" in card:
                return await blocked_delivery(message_id, card)
            return await original_patch(message_id, card)

        client.im.patch = patch
    else:
        client.im.send = blocked_delivery
    backend = FakeLlmBackend(
        [tool_turn(id="call", name="deploy", arguments_json='{"env":"prod"}'), text_turn("new reply")]
    )
    agent = AgentEngine(
        backend=backend,
        registry=registry,
        client=client,
        store=stores.sessions,
        approvals=stores.approvals,
        authorizations=stores.authorizations,
        approval_card_builder=lambda pending: proposals.append(pending) or {"id": pending.approval_id},
        auth_card_builder=lambda url: {"url": url},
        authorize_url_builder=lambda user, scopes, pending: proposals.append(pending) or "https://example.invalid/auth",
        progress_card_builder=(
            (lambda steps, done, text: {"progress": steps, "done": done, "text": text})
            if replace_progress_card
            else None
        ),
    )
    task = asyncio.create_task(agent.run(_text_event("deploy")))
    await asyncio.wait_for(started.wait(), 1)
    await agent.run(_text_event("new request", message_id="new"))
    await task
    pending = proposals[0]
    if authorization:
        assert await stores.authorizations.get(pending.authorization_id) is None
    else:
        assert await stores.approvals.get(pending.approval_id) is None
    assert writes == []
    if replace_progress_card:
        assert client.patches[-1][1]["text"] == "new reply"
    else:
        assert client.replies[-1][1] == "new reply"


@pytest.mark.parametrize("state", ["executing", "execution_unknown"])
async def test_delivery_cleanup_preserves_claimed_and_unknown_approval(durable_stores, state):
    stores = durable_stores
    engine = DefaultApprovalEngine(approvals=stores.approvals)
    pending = PendingApproval("ap", "oc_1", "call", "deploy", {}, state=state)
    await stores.approvals.put(pending)
    await engine.on_cancel("ap")
    current = await stores.approvals.get("ap")
    assert current is not None and current.state == state


@pytest.mark.parametrize("state", ["executing", "execution_unknown"])
async def test_delivery_cleanup_preserves_claimed_and_unknown_authorization(durable_stores, state):
    stores = durable_stores
    pending = PendingAuthorization("auth", "oc_1", "call", "deploy", {}, state=state)
    await stores.authorizations.put(pending)
    await cancel_pending_authorization(SimpleNamespace(authorizations=stores.authorizations), "auth")
    current = await stores.authorizations.get("auth")
    assert current is not None and current.state == state


@pytest.mark.parametrize("authorization", [False, True])
async def test_reset_waits_until_continuation_finishes_its_side_effect_and_history(durable_stores, authorization):
    stores = durable_stores
    proposals = []
    started = asyncio.Event()
    release = asyncio.Event()
    client = _ApprovalRecordingClient()
    writes = []
    registry = ToolRegistry()

    async def deploy(env):
        writes.append(env)
        started.set()
        await release.wait()
        return "deployed"

    registry.register(
        "deploy",
        deploy,
        description="deploy",
        input_schema=SCHEMA,
        requires_approval=not authorization,
        auth_scopes=("scope",) if authorization else (),
    )
    backend = FakeLlmBackend([tool_turn(id="call", name="deploy", arguments_json='{"env":"prod"}'), text_turn("done")])
    agent = AgentEngine(
        backend=backend,
        registry=registry,
        client=client,
        store=stores.sessions,
        approvals=stores.approvals,
        authorizations=stores.authorizations,
        approval_card_builder=lambda pending: proposals.append(pending) or {"id": pending.approval_id},
        auth_card_builder=lambda url: {"url": url},
        authorize_url_builder=lambda user, scopes, pending: proposals.append(pending) or "https://example.invalid/auth",
        clear_command=lambda text: text == "/clear",
    )
    await agent.run(_text_event("deploy"))
    pending = proposals[0]

    async def resume():
        if authorization:
            return await agent.resume_authorization(pending.authorization_id, user={"open_id": "ou_tester"})
        await agent.handle_card_action(
            _action_event(pending.approval_id, "approve", payload_sha256=pending.payload_sha256)
        )
        await _drain(agent)

    callback = asyncio.create_task(resume())
    await asyncio.wait_for(started.wait(), 1)
    reset = asyncio.create_task(agent.run(_text_event("/clear", message_id="reset")))
    try:
        # Let the reset coroutine try to acquire the live session lock while dispatch is blocked.
        await asyncio.sleep(0)
        assert not reset.done()
        assert any(
            isinstance(part, ToolUsePart) for message in await stores.sessions.get("oc_1") for part in message.content
        )
    finally:
        release.set()
        await asyncio.gather(callback, reset)
    assert writes == ["prod"]
    assert await stores.sessions.get("oc_1") == []
    assert len(backend.calls) == 2
