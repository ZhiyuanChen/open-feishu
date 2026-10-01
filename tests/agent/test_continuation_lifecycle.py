"""Claim gates, cross-connection metadata writes, and durable continuation origins."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from feishu.agent.adapters.anthropic import _to_anthropic_messages
from feishu.agent.adapters.openai import _to_openai_messages
from feishu.agent.approval import (
    ApprovalStatus,
    DefaultApprovalEngine,
    persist_approval_card_message_id,
)
from feishu.agent.integrity import payload_sha256
from feishu.agent.llm import ToolResultPart, ToolUsePart
from feishu.agent.loop import AgentEngine
from feishu.agent.oauth import persist_authorization_card_message_id
from feishu.agent.persistence import SqlitePendingApprovalStore, SqlitePendingAuthorizationStore, SqliteSessionStore
from feishu.agent.result import ToolOutcome, ToolResult
from feishu.agent.session import ClaimResult, PendingApproval, PendingAuthorization
from feishu.agent.tools import ToolRegistry
from tests._fakes import FakeLlmBackend, text_turn, tool_turn
from tests.agent import test_continuation_integrity as continuation_tests
from tests.agent.test_loop import _action_event, _ApprovalRecordingClient, _drain, _text_event

SCHEMA = continuation_tests.SCHEMA
durable_stores = continuation_tests.durable_stores


@pytest.mark.parametrize(
    "state,callback_hash,expected_status",
    [
        ("awaiting_confirmation", None, ApprovalStatus.TAMPERED),
        ("awaiting_confirmation", "wrong", ApprovalStatus.TAMPERED),
        ("executing", "valid", ApprovalStatus.ALREADY_DECIDED),
        ("execution_unknown", "valid", ApprovalStatus.ALREADY_DECIDED),
    ],
)
async def test_cached_replay_obeys_hash_and_lifecycle_claim_gate(durable_stores, state, callback_hash, expected_status):
    stores = durable_stores
    engine = DefaultApprovalEngine(approvals=stores.approvals, executions=stores.executions)
    sha = payload_sha256({"env": "prod"})
    writes = []

    async def dispatch(name, args):
        writes.append(args)
        return ToolResult(ToolOutcome.COMPLETED, "deployed")

    first = PendingApproval(
        "first", "oc_1", "call", "deploy", {"env": "prod"}, payload_sha256=sha, idempotency_key="same_write"
    )
    await engine.on_request(first)
    await engine.on_decision("first", "approve", expected_payload_sha256=sha, dispatch=dispatch)
    pending = PendingApproval(
        "second",
        "oc_1",
        "call",
        "deploy",
        {"env": "prod"},
        payload_sha256=sha,
        idempotency_key="same_write",
        state=state,
    )
    await engine.on_request(pending)
    outcome = await engine.on_decision(
        "second",
        "approve",
        expected_payload_sha256=sha if callback_hash == "valid" else callback_hash,
        dispatch=dispatch,
    )
    assert outcome.status == expected_status
    current = await stores.approvals.get("second")
    assert current is not None and current.state == state
    assert len(writes) == 1


@pytest.mark.parametrize("authorization", [False, True])
async def test_two_connections_cannot_restore_awaiting_state_during_card_metadata_update(tmp_path, authorization):
    store_type = SqlitePendingAuthorizationStore if authorization else SqlitePendingApprovalStore
    first = store_type(tmp_path / "pending.db")
    second = store_type(tmp_path / "pending.db")
    sha = payload_sha256({"env": "prod"})
    if authorization:
        pending = PendingAuthorization("pending", "oc_1", "call", "deploy", {"env": "prod"})
    else:
        pending = PendingApproval("pending", "oc_1", "call", "deploy", {"env": "prod"}, payload_sha256=sha)
    await first.put(pending)
    read_done = threading.Event()
    release = threading.Event()
    claim_finished = threading.Event()
    original_load = first._load_locked

    def paused_load(key):
        current = original_load(key)
        read_done.set()
        assert release.wait(5), "metadata updater was not released"
        return current

    first._load_locked = paused_load

    async def metadata_update():
        if authorization:
            await persist_authorization_card_message_id(SimpleNamespace(authorizations=first), pending, "card")
        else:
            await persist_approval_card_message_id(SimpleNamespace(approvals=first), pending, "card")

    async def claim_and_freeze():
        if authorization:
            claim = await second.claim("pending")
        else:
            claim = await second.claim("pending", expected_payload_sha256=sha)
        assert claim == ClaimResult.CLAIMED
        await second.complete("pending", outcome="frozen")
        claim_finished.set()

    try:
        with ThreadPoolExecutor(2) as workers:
            update = workers.submit(asyncio.run, metadata_update())
            assert await asyncio.to_thread(read_done.wait, 2)
            claim = workers.submit(asyncio.run, claim_and_freeze())
            # An old implementation lets claim/freeze finish before stale metadata writes. A transaction
            # holds the write lock; release it after a bounded attempt so the corrected test cannot deadlock.
            await asyncio.to_thread(claim_finished.wait, 1)
            release.set()
            await asyncio.to_thread(update.result, 5)
            await asyncio.to_thread(claim.result, 5)
        current = await second.get("pending")
        assert current is not None and current.state == "execution_unknown"
        assert "card" in current.extra.values()
    finally:
        release.set()
        first._db.close()
        second._db.close()


@pytest.mark.parametrize("authorization", [False, True])
async def test_clear_and_reused_tool_ids_cannot_revive_an_old_continuation(durable_stores, authorization):
    stores = durable_stores
    pending_records = []
    writes = []
    client = _ApprovalRecordingClient()
    registry = ToolRegistry()

    async def deploy(env):
        writes.append(env)
        return "deployed"

    registry.register(
        "deploy",
        deploy,
        input_schema=SCHEMA,
        description="deploy",
        requires_approval=not authorization,
        auth_scopes=("scope",) if authorization else (),
    )
    backend = FakeLlmBackend(
        [
            tool_turn(id="same_call", name="deploy", arguments_json='{"env":"prod"}'),
            tool_turn(id="same_call", name="deploy", arguments_json='{"env":"prod"}'),
            text_turn("done"),
        ]
    )
    agent = AgentEngine(
        backend=backend,
        registry=registry,
        store=stores.sessions,
        approvals=stores.approvals,
        authorizations=stores.authorizations,
        client=client,
        clear_command=lambda text: text == "/clear",
        approval_card_builder=lambda pending: pending_records.append(pending) or {"id": pending.approval_id},
        auth_card_builder=lambda url: {"url": url},
        authorize_url_builder=lambda user, scopes, pending: pending_records.append(pending)
        or "https://example.invalid",
    )
    await agent.run(_text_event("first", message_id="first"))
    old = pending_records[0]
    await agent.run(_text_event("/clear", message_id="clear"))
    await agent.run(_text_event("second", message_id="second"))
    new = pending_records[1]
    before = await stores.sessions.get("oc_1")
    if authorization:
        assert await agent.resume_authorization(old.authorization_id, user={"open_id": "ou_tester"}) == "superseded"
        assert await stores.authorizations.get(new.authorization_id) is not None
    else:
        await agent.handle_card_action(_action_event(old.approval_id, "approve", payload_sha256=old.payload_sha256))
        await _drain(agent)
        assert await stores.approvals.get(new.approval_id) is not None
    assert writes == []
    assert len(backend.calls) == 2
    assert await stores.sessions.get("oc_1") == before


@pytest.mark.parametrize("authorization_first", [False, True])
async def test_identical_live_turns_keep_exact_origin_when_switching_confirmation_and_oauth(
    durable_stores, authorization_first
):
    stores = durable_stores
    approvals = []
    authorizations = []
    client = _ApprovalRecordingClient()
    registry = ToolRegistry()

    async def deploy(env):
        return ToolResult(ToolOutcome.NEEDS_USER_AUTH, "need auth", auth_scopes=("scope",), is_error=True)

    registry.register(
        "deploy",
        deploy,
        input_schema=SCHEMA,
        description="deploy",
        requires_approval=True,
        auth_scopes=("scope",) if authorization_first else (),
    )
    backend = FakeLlmBackend(
        [
            tool_turn(id="same_call", name="deploy", arguments_json='{"env":"prod"}'),
            tool_turn(id="same_call", name="deploy", arguments_json='{"env":"prod"}'),
        ]
    )
    agent = AgentEngine(
        backend=backend,
        registry=registry,
        store=stores.sessions,
        approvals=stores.approvals,
        authorizations=stores.authorizations,
        client=client,
        approval_card_builder=lambda pending: approvals.append(pending) or {"id": pending.approval_id},
        auth_card_builder=lambda url: {"url": url},
        authorize_url_builder=lambda user, scopes, pending: authorizations.append(pending) or "https://example.invalid",
    )
    await agent.run(_text_event("first", message_id="first"))
    await agent.run(_text_event("second", message_id="second"))
    origins = [
        getattr(message, "continuation_id", None)
        for message in await stores.sessions.get("oc_1")
        if any(isinstance(part, ToolUsePart) for part in message.content)
    ]
    assert len(origins) == 2 and origins[0] and origins[0] != origins[1]
    results = [
        part
        for message in await stores.sessions.get("oc_1")
        for part in message.content
        if isinstance(part, ToolResultPart)
    ]
    assert len(results) == 2
    if authorization_first:
        assert (
            await agent.resume_authorization(authorizations[0].authorization_id, user={"open_id": "ou_tester"})
            == "resumed"
        )
        created = approvals[0]
    else:
        await agent.handle_card_action(
            _action_event(approvals[0].approval_id, "approve", payload_sha256=approvals[0].payload_sha256)
        )
        await _drain(agent)
        created = authorizations[0]
    assert created.extra["origin_continuation_id"] == origins[0]
    assert created.extra["origin_continuation_id"] != origins[1]


async def test_reused_ids_in_new_assistant_batches_execute_and_record_each_logical_call(durable_stores):
    stores = durable_stores
    proposals = []
    writes = []
    client = _ApprovalRecordingClient()
    registry = ToolRegistry()

    async def deploy(env):
        writes.append(env)
        return f"deployment {len(writes)}"

    registry.register("deploy", deploy, input_schema=SCHEMA, description="deploy", requires_approval=True)
    backend = FakeLlmBackend(
        [
            tool_turn(id="same_call", name="deploy", arguments_json='{"env":"prod"}'),
            tool_turn(id="same_call", name="deploy", arguments_json='{"env":"prod"}'),
            text_turn("done"),
        ]
    )
    agent = AgentEngine(
        backend=backend,
        registry=registry,
        store=stores.sessions,
        approvals=stores.approvals,
        client=client,
        approval_engine=DefaultApprovalEngine(approvals=stores.approvals, executions=stores.executions),
        approval_card_builder=lambda pending: proposals.append(pending) or {"id": pending.approval_id},
    )
    await agent.run(_text_event("deploy twice", message_id="same_message"))
    for index in range(2):
        pending = proposals[index]
        await agent.handle_card_action(
            _action_event(pending.approval_id, "approve", payload_sha256=pending.payload_sha256)
        )
        await _drain(agent)
    assert writes == ["prod", "prod"]
    assert proposals[0].idempotency_key != proposals[1].idempotency_key
    history = await stores.sessions.get("oc_1")
    provider = _to_openai_messages(history, None)
    assert [message["content"] for message in provider if message["role"] == "tool"] == ["deployment 1", "deployment 2"]
    assert [message["role"] for message in provider] == ["user", "assistant", "tool", "assistant", "tool", "assistant"]
    for message in history:
        if message.continuation_id:
            assert message.continuation_id not in str(provider)
            assert message.continuation_id not in str(_to_anthropic_messages(history))


@pytest.mark.parametrize("authorization", [False, True])
async def test_origin_binding_survives_sqlite_reopen_and_engine_restart(durable_stores, authorization):
    stores = durable_stores
    proposals = []
    writes = []
    client = _ApprovalRecordingClient()
    registry = ToolRegistry()

    async def deploy(env):
        writes.append(env)
        return "deployed"

    registry.register(
        "deploy",
        deploy,
        input_schema=SCHEMA,
        description="deploy",
        requires_approval=not authorization,
        auth_scopes=("scope",) if authorization else (),
    )
    initial = AgentEngine(
        backend=FakeLlmBackend([tool_turn(id="call", name="deploy", arguments_json='{"env":"prod"}')]),
        registry=registry,
        store=stores.sessions,
        approvals=stores.approvals,
        authorizations=stores.authorizations,
        client=client,
        approval_card_builder=lambda pending: proposals.append(pending) or {"id": pending.approval_id},
        auth_card_builder=lambda url: {"url": url},
        authorize_url_builder=lambda user, scopes, pending: proposals.append(pending) or "https://example.invalid",
    )
    await initial.run(_text_event("deploy"))
    pending = proposals[0]
    path = stores.sessions._db.execute("PRAGMA database_list").fetchone()[2]
    reopened_sessions = SqliteSessionStore(path)
    reopened_approvals = SqlitePendingApprovalStore(path)
    reopened_authorizations = SqlitePendingAuthorizationStore(path)
    try:
        restored = AgentEngine(
            backend=FakeLlmBackend([text_turn("done")]),
            registry=registry,
            store=reopened_sessions,
            approvals=reopened_approvals,
            authorizations=reopened_authorizations,
            client=client,
        )
        history = await reopened_sessions.get("oc_1")
        origin = next(message.continuation_id for message in history if message.role == "assistant")
        assert origin and pending.extra["origin_continuation_id"] == origin
        if authorization:
            assert (
                await restored.resume_authorization(pending.authorization_id, user={"open_id": "ou_tester"})
                == "resumed"
            )
        else:
            await restored.handle_card_action(
                _action_event(pending.approval_id, "approve", payload_sha256=pending.payload_sha256)
            )
            await _drain(restored)
        assert writes == ["prod"]
        assert client.replies[-1][1] == "done"
    finally:
        reopened_sessions._db.close()
        reopened_approvals._db.close()
        reopened_authorizations._db.close()


@pytest.mark.parametrize("authorization", [False, True])
async def test_legacy_pending_without_durable_origin_fails_closed(durable_stores, authorization):
    stores = durable_stores
    proposals = []
    writes = []
    client = _ApprovalRecordingClient()
    registry = ToolRegistry()

    async def deploy(env):
        writes.append(env)
        return "deployed"

    registry.register(
        "deploy",
        deploy,
        input_schema=SCHEMA,
        description="deploy",
        requires_approval=not authorization,
        auth_scopes=("scope",) if authorization else (),
    )
    backend = FakeLlmBackend([tool_turn(id="call", name="deploy", arguments_json='{"env":"prod"}'), text_turn("done")])
    agent = AgentEngine(
        backend=backend,
        registry=registry,
        store=stores.sessions,
        approvals=stores.approvals,
        authorizations=stores.authorizations,
        client=client,
        approval_card_builder=lambda pending: proposals.append(pending) or {"id": pending.approval_id},
        auth_card_builder=lambda url: {"url": url},
        authorize_url_builder=lambda user, scopes, pending: proposals.append(pending) or "https://example.invalid",
    )
    await agent.run(_text_event("deploy"))
    pending = proposals[0]

    def legacy_record(current):
        current.extra.pop("origin_continuation_id", None)
        return None, current

    if authorization:
        await stores.authorizations.update(pending.authorization_id, legacy_record)
        assert await agent.resume_authorization(pending.authorization_id, user={"open_id": "ou_tester"}) == "superseded"
        assert await stores.authorizations.get(pending.authorization_id) is None
    else:
        await stores.approvals.update(pending.approval_id, legacy_record)
        await agent.handle_card_action(
            _action_event(pending.approval_id, "approve", payload_sha256=pending.payload_sha256)
        )
        await _drain(agent)
        assert await stores.approvals.get(pending.approval_id) is None
    assert writes == []
    assert len(backend.calls) == 1
    assert client.patches


async def test_prior_approval_cannot_transfer_to_another_identical_live_batch(durable_stores):
    stores = durable_stores
    proposals = []
    auth_records = []
    writes = []
    authorized = False
    client = _ApprovalRecordingClient()
    registry = ToolRegistry()

    async def deploy(env):
        if not authorized:
            return ToolResult(ToolOutcome.NEEDS_USER_AUTH, "need auth", auth_scopes=("scope",), is_error=True)
        writes.append(env)
        return "deployed"

    registry.register("deploy", deploy, input_schema=SCHEMA, description="deploy", requires_approval=True)
    backend = FakeLlmBackend(
        [
            tool_turn(id="same_call", name="deploy", arguments_json='{"env":"prod"}'),
            tool_turn(id="same_call", name="deploy", arguments_json='{"env":"prod"}'),
            text_turn("done"),
        ]
    )
    agent = AgentEngine(
        backend=backend,
        registry=registry,
        store=stores.sessions,
        approvals=stores.approvals,
        authorizations=stores.authorizations,
        client=client,
        approval_card_builder=lambda pending: proposals.append(pending) or {"id": pending.approval_id},
        auth_card_builder=lambda url: {"url": url},
        authorize_url_builder=lambda user, scopes, pending: auth_records.append(pending) or "https://example.invalid",
    )
    await agent.run(_text_event("first", message_id="first"))
    await agent.run(_text_event("second", message_id="second"))
    old = proposals[0]
    await agent.handle_card_action(_action_event(old.approval_id, "approve", payload_sha256=old.payload_sha256))
    await _drain(agent)
    newer_origin = proposals[1].extra["origin_continuation_id"]

    def change_origin(pending):
        pending.extra["origin_continuation_id"] = newer_origin
        return None, pending

    await stores.authorizations.update(auth_records[0].authorization_id, change_origin)
    authorized = True
    assert (
        await agent.resume_authorization(auth_records[0].authorization_id, user={"open_id": "ou_tester"}) == "resumed"
    )
    assert writes == []
    assert len(proposals) == 3
    assert proposals[2].extra["origin_continuation_id"] == newer_origin


async def test_cache_read_failure_finishes_claim_without_dispatch_and_settles_card(durable_stores):
    stores = durable_stores
    proposals = []
    writes = []
    client = _ApprovalRecordingClient()
    registry = ToolRegistry()

    async def deploy(env):
        writes.append(env)
        return "deployed"

    registry.register("deploy", deploy, input_schema=SCHEMA, description="deploy", requires_approval=True)
    backend = FakeLlmBackend(
        [tool_turn(id="call", name="deploy", arguments_json='{"env":"prod"}'), text_turn("could not execute")]
    )
    agent = AgentEngine(
        backend=backend,
        registry=registry,
        store=stores.sessions,
        approvals=stores.approvals,
        client=client,
        approval_engine=DefaultApprovalEngine(approvals=stores.approvals, executions=stores.executions),
        approval_card_builder=lambda pending: proposals.append(pending) or {"id": pending.approval_id},
    )
    await agent.run(_text_event("deploy"))
    pending = proposals[0]
    stores.executions._db.close()
    await agent.handle_card_action(_action_event(pending.approval_id, "approve", payload_sha256=pending.payload_sha256))
    await _drain(agent)
    assert writes == []
    assert await stores.approvals.get(pending.approval_id) is None
    results = [
        part
        for message in await stores.sessions.get("oc_1")
        for part in message.content
        if isinstance(part, ToolResultPart)
    ]
    assert len(results) == 1 and results[0].is_error
    assert "ProgrammingError" not in str(results[0].content)
    assert client.patches and "执行失败" in str(client.patches[-1][1])
    assert len(backend.calls) == 2


@pytest.mark.parametrize("state,label", [("executing", "已处理"), ("execution_unknown", "结果未知")])
async def test_clear_does_not_turn_claimed_or_unknown_approval_into_resend_prompt(durable_stores, state, label):
    stores = durable_stores
    proposals = []
    writes = []
    client = _ApprovalRecordingClient()
    registry = ToolRegistry()

    async def deploy(env):
        writes.append(env)
        return "deployed"

    registry.register("deploy", deploy, input_schema=SCHEMA, description="deploy", requires_approval=True)
    backend = FakeLlmBackend([tool_turn(id="call", name="deploy", arguments_json='{"env":"prod"}')])
    agent = AgentEngine(
        backend=backend,
        registry=registry,
        store=stores.sessions,
        approvals=stores.approvals,
        client=client,
        clear_command=lambda text: text == "/clear",
        approval_card_builder=lambda pending: proposals.append(pending) or {"id": pending.approval_id},
    )
    await agent.run(_text_event("deploy"))
    pending = proposals[0]
    assert (
        await stores.approvals.claim(pending.approval_id, expected_payload_sha256=pending.payload_sha256)
        == ClaimResult.CLAIMED
    )
    if state == "execution_unknown":
        await stores.approvals.complete(pending.approval_id, outcome="frozen")
    await agent.run(_text_event("/clear", message_id="clear"))
    await agent.handle_card_action(_action_event(pending.approval_id, "approve", payload_sha256=pending.payload_sha256))
    await _drain(agent)
    current = await stores.approvals.get(pending.approval_id)
    assert current is not None and current.state == state
    assert writes == []
    assert await stores.sessions.get("oc_1") == []
    assert len(backend.calls) == 1
    assert client.patches and label in str(client.patches[-1][1])
    assert "替代" not in str(client.patches[-1][1])
