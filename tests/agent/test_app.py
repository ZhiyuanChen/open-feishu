from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from starlette.testclient import TestClient

from feishu.agent import Agent
from feishu.agent.loop import AgentEngine
from feishu.agent.tools import ToolRegistry
from feishu.events.jobs import EventJobStatus, InMemoryEventJobStore
from tests._fakes import FakeLlmBackend, text_turn


class _Client:
    pass


async def test_scheduling_lifecycle(tmp_path: Path) -> None:
    from feishu.agent.scheduled import Schedule

    delivered = asyncio.Event()
    messages: list[str] = []

    async def send(_target: str, content: str, **_kwargs: Any) -> dict[str, Any]:
        messages.append(content)
        delivered.set()
        return {"message_id": "message-1"}

    registry = ToolRegistry()
    agent = Agent(
        {
            "storage": {"path": str(tmp_path / "agent.db")},
            "scheduling": {"enabled": True, "poll_interval": 0.01},
            "toolkits": [],
        },
        client=SimpleNamespace(im=SimpleNamespace(send=send)),
        backend=FakeLlmBackend([text_turn("completed")]),
        registry=registry,
    )

    names = {tool.name for tool in agent.engine.registry.specs()}
    assert {
        "create_scheduled_task",
        "list_scheduled_tasks",
        "pause_scheduled_task",
        "resume_scheduled_task",
        "delete_scheduled_task",
    } <= names
    assert registry.specs() == []
    store = agent.engine.scheduled_store
    now = time.time()
    owner = ("user_id:user-1",)
    draft = store.create_draft(
        tenant_key="tenant-1",
        owner_keys=owner,
        chat_id="chat-1",
        instruction="Report status",
        schedule=Schedule.once(now + 0.1),
        now=now,
    )
    task = store.confirm_draft(draft.draft_id, owner, draft.payload_hash, tenant_key="tenant-1", now=now)
    await agent.start()
    try:
        await asyncio.wait_for(delivered.wait(), timeout=3)
    finally:
        await agent.aclose()
    assert messages == ["completed"]
    assert store.runs(task.task_id)[0].status == "succeeded"
    assert agent.status == "stopped"


def test_agent_facade(tmp_path: Path) -> None:
    agent = Agent(
        {"storage": {"path": str(tmp_path / "agent.db")}, "toolkits": []},
        client=_Client(),
        backend=FakeLlmBackend([]),
        registry=ToolRegistry(),
    )

    assert isinstance(agent.engine, AgentEngine)


async def test_agent_asgi_app_persists_message_before_ack() -> None:
    jobs = InMemoryEventJobStore()
    engine = AgentEngine(backend=FakeLlmBackend([]), registry=ToolRegistry())
    agent = Agent(
        engine=engine,
        event_job_store=jobs,
        event_partition_key=lambda _event: "chat-1",
    )

    with TestClient(agent.asgi_app()) as client:
        response = client.post(
            "/feishu/event",
            json={
                "schema": "2.0",
                "header": {"event_type": "im.message.receive_v1", "event_id": "evt-agent"},
                "event": {"message": {"chat_id": "chat-1", "message_id": "msg-1"}},
            },
        )
        assert response.status_code == 200

    job = await jobs.get("evt-agent")
    assert job is not None and job.status is EventJobStatus.QUEUED


def test_empty_injected_registry_overrides_configured_toolkits(tmp_path: Path) -> None:
    registry = ToolRegistry()
    agent = Agent(
        {
            "storage": {"path": str(tmp_path / "agent.db")},
            "toolkits": ["feishu.workplace"],
        },
        client=_Client(),
        backend=FakeLlmBackend([]),
        registry=registry,
    )

    assert agent.engine.registry is registry
    assert agent.engine.registry.specs() == []


def test_agent_facade_keeps_time_context_out_of_system_prompt(tmp_path: Path) -> None:
    agent = Agent(
        {
            "storage": {"path": str(tmp_path / "agent.db")},
            "system": "base prompt",
            "timezone": "Asia/Shanghai",
            "toolkits": [],
        },
        client=_Client(),
        backend=FakeLlmBackend([]),
        registry=ToolRegistry(),
    )

    assert agent.engine.system == "base prompt"
    rendered = asyncio.run(agent.engine._turn_context_for_event(cast(Any, SimpleNamespace(body={})), "Europe/Berlin"))
    assert rendered is not None
    assert "Current datetime:" in rendered
    assert "Current timezone: Europe/Berlin" in rendered


def test_agent_facade_passes_idle_session_timeout(tmp_path: Path) -> None:
    agent = Agent(
        {
            "storage": {"path": str(tmp_path / "agent.db")},
            "session": {"idle_session_timeout_seconds": 7200},
            "toolkits": [],
        },
        client=_Client(),
        backend=FakeLlmBackend([]),
        registry=ToolRegistry(),
    )

    assert agent.engine.idle_session_timeout_seconds == 7200


def test_agent_facade_passes_progress_summary_interval(tmp_path: Path) -> None:
    agent = Agent(
        {
            "storage": {"path": str(tmp_path / "agent.db")},
            "reply": {"progress_summary_interval_seconds": 5},
            "toolkits": [],
        },
        client=_Client(),
        backend=FakeLlmBackend([]),
        registry=ToolRegistry(),
    )

    assert agent.engine._progress_summary_interval_seconds == 5


def test_agent_uses_deepseek_thinking_protocol_for_main_and_fast_models(tmp_path: Path) -> None:
    agent = Agent(
        {
            "storage": {"path": str(tmp_path / "agent.db")},
            "model": {
                "model": "deepseek-flash",
                "api_key": "key",
                "base_url": "https://api.deepseek.com",
                "thinking_enabled": True,
            },
            "fast_model": {
                "model": "deepseek-flash",
                "api_key": "key",
                "base_url": "https://api.deepseek.com",
            },
            "toolkits": [],
        },
        client=_Client(),
        backend=FakeLlmBackend([]),
        registry=ToolRegistry(),
        progress_summarizer=lambda _snapshot: None,
        text_summarizer=lambda _messages, **_kwargs: "",
    )

    main = agent._model_backend_from_config(object())
    fast = agent._fast_backend_from_config()

    assert main._defaults["extra_body"] == {"thinking": {"type": "enabled"}}
    assert main._replay_reasoning_content is True
    assert fast is not None
    assert fast._defaults["extra_body"] == {"thinking": {"type": "disabled"}}


def test_agent_asgi_app_exposes_card_callback_route(tmp_path: Path) -> None:
    agent = Agent(
        {"storage": {"path": str(tmp_path / "agent.db")}, "toolkits": []},
        client=_Client(),
        backend=FakeLlmBackend([]),
        registry=ToolRegistry(),
    )

    assert "/feishu/card" in {route.path for route in agent.asgi_app().routes}


async def test_durable_card_callback(tmp_path: Path) -> None:
    from feishu.agent.durable_callbacks import SqliteDurableCallbacks
    from feishu.agent.integrity import payload_sha256
    from feishu.agent.llm import Message, ToolUsePart
    from feishu.agent.persistence import SqlitePendingApprovalStore, SqlitePendingAuthorizationStore
    from feishu.agent.session import PendingApproval
    from feishu.auth import SqliteOAuthTokenStore
    from feishu.events.jobs import SqliteEventJobStore

    path = tmp_path / "agent.db"
    jobs, tokens = SqliteEventJobStore(path), SqliteOAuthTokenStore(path)
    approvals, authorizations = SqlitePendingApprovalStore(path), SqlitePendingAuthorizationStore(path)
    inbox = SqliteDurableCallbacks(jobs, tokens, authorizations, approvals=approvals)
    registry = ToolRegistry()
    writes: list[str] = []
    registry.register("write", lambda: writes.append("done"), description="write", input_schema={"type": "object"})
    app_client = _Client()
    agent = Agent(
        {"storage": {"path": str(path)}, "feishu": {"verification_token": "fake-token"}, "toolkits": []},
        client=app_client,
        backend=FakeLlmBackend([text_turn("done")]),
        registry=registry,
        user_tokens=SimpleNamespace(store=tokens, client=app_client),
        approvals=approvals,
        authorizations=authorizations,
        durable_callbacks=inbox,
    )
    digest = payload_sha256({})
    pending = PendingApproval(
        approval_id="approval",
        session_id="chat",
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
    await approvals.put(pending)
    await agent.engine.store.set(
        "chat",
        [
            Message(
                role="assistant", content=[ToolUsePart(id="call", name="write", arguments={})], continuation_id="origin"
            )
        ],
    )
    event = {
        "schema": "2.0",
        "header": {
            "event_type": "card.action.trigger",
            "event_id": "click",
            "token": "fake-token",
            "tenant_key": "tenant",
        },
        "event": {
            "operator": {"open_id": "owner"},
            "action": {"value": {"__approval__": "approval", "decision": "approve", "payload_sha256": digest}},
            "context": {"open_message_id": "card", "open_chat_id": "chat"},
        },
    }
    with TestClient(agent.asgi_app()) as client:
        response = client.post("/feishu/card", json=event)
        assert response.status_code == 200 and writes == []
        result = await inbox.runner(agent, worker_id="worker").run_once()
        assert result.status.value == "completed" and writes == ["done"]
    for store in (jobs, tokens, approvals, authorizations):
        store._db.close()


def test_injected_engine_oauth(tmp_path: Path) -> None:
    from feishu.agent.durable_callbacks import SqliteDurableCallbacks
    from feishu.agent.persistence import SqlitePendingApprovalStore, SqlitePendingAuthorizationStore, SqliteSessionStore
    from feishu.auth import SqliteOAuthTokenStore, UserTokenProvider
    from feishu.events.jobs import SqliteEventJobStore

    path = tmp_path / "agent.db"
    jobs = SqliteEventJobStore(path, unknown_ttl_seconds=None)
    tokens = SqliteOAuthTokenStore(path)
    approvals = SqlitePendingApprovalStore(path)
    authorizations = SqlitePendingAuthorizationStore(path)
    sessions = SqliteSessionStore(path)
    client = _Client()
    provider = UserTokenProvider(client, tokens)
    engine = AgentEngine(
        backend=FakeLlmBackend([]),
        registry=ToolRegistry(),
        client=cast(Any, client),
        user_tokens=provider,
        store=sessions,
        approvals=approvals,
        authorizations=authorizations,
    )
    inbox = SqliteDurableCallbacks(jobs, tokens, authorizations, approvals=approvals)
    agent = Agent(
        {
            "feishu": {"verification_token": "fake"},
            "oauth": {"public_url": "https://example.test", "state_secret": "test-secret"},
        },
        engine=engine,
        client=client,
        user_tokens=provider,
        durable_callbacks=inbox,
    )
    assert "/oauth/callback" in {route.path for route in agent.asgi_app().routes}
    assert engine.authorize_url_builder is not None
    for store in (jobs, tokens, approvals, authorizations, sessions):
        store._db.close()


@pytest.mark.parametrize(
    ("server", "callback_path"),
    [
        ({}, "/feishu/card"),
        ({}, "/feishu/event"),
        ({"card_path": "/callbacks/card"}, "/callbacks/card"),
        ({"card_path": "/feishu/event"}, "/feishu/event"),
    ],
)
def test_agent_http_card_callback_returns_toast(server: dict[str, str], callback_path: str) -> None:
    agent = Agent(
        {"server": server},
        engine=AgentEngine(backend=FakeLlmBackend([]), registry=ToolRegistry()),
    )
    payload = {
        "schema": "2.0",
        "header": {"event_type": "card.action.trigger", "event_id": "card_1"},
        "event": {"action": {"value": {"decision": "approve"}}},
    }

    with TestClient(agent.asgi_app()) as client:
        response = client.post(callback_path, json=payload)

    assert response.status_code == 200
    assert response.json() == {"toast": {"type": "info", "content": "没有待处理的确认请求"}}
