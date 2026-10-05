from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest
from starlette.testclient import TestClient

from feishu.agent import Agent, ToolRegistry
from feishu.agent.loop import AgentEngine
from feishu.events.envelope import Event
from tests._fakes import FakeLlmBackend


def make_agent(tmp_path: Path, *, shutdown_timeout_seconds: float = 1.0) -> Agent:
    return Agent(
        {
            "runtime": {"shutdown_timeout_seconds": shutdown_timeout_seconds},
            "storage": {"path": str(tmp_path / "agent.db")},
            "toolkits": [],
        },
        client=object(),
        backend=FakeLlmBackend([]),
        registry=ToolRegistry(),
    )


async def test_agent_start_and_close_drains_engine_background_tasks(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    cancelled = asyncio.Event()

    async def background_work() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(background_work())
    agent.engine.track_background_task(task)

    await agent.start()
    assert agent.status == "ready"
    assert agent.engine.accepting_events

    await agent.aclose()
    await agent.aclose()

    assert cancelled.is_set()
    assert not agent.engine.accepting_events
    assert agent.status == "stopped"


def test_asgi_lifespan_updates_health_and_readiness(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    with TestClient(agent.asgi_app()) as client:
        assert client.get("/health").status_code == 200
        ready = client.get("/ready")
        assert ready.status_code == 200
        assert ready.json() == {"ready": True, "status": "ready"}

    assert agent.status == "stopped"


class ClosableClient:
    def __init__(self) -> None:
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


async def test_agent_closes_owned_client_and_sqlite_connections_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = ClosableClient()
    monkeypatch.setattr(Agent, "_client_from_config", lambda _self: client)
    agent = Agent(
        {
            "feishu": {"app_id": "cli_test", "app_secret": "secret"},
            "storage": {"path": str(tmp_path / "agent.db")},
            "toolkits": [],
        },
        backend=FakeLlmBackend([]),
        registry=ToolRegistry(),
    )
    session_db = cast(Any, agent.engine.store)._db

    await agent.aclose()
    await agent.aclose()

    assert client.close_calls == 1
    with pytest.raises(sqlite3.ProgrammingError):
        session_db.execute("SELECT 1")


async def test_agent_does_not_close_injected_client_or_engine(tmp_path: Path) -> None:
    client = ClosableClient()
    engine = AgentEngine(backend=FakeLlmBackend([]), registry=ToolRegistry())
    agent = Agent(engine=engine, client=client)

    await agent.aclose()

    assert client.close_calls == 0
    assert engine.accepting_events


async def test_engine_rejects_new_messages_after_shutdown_begins() -> None:
    engine = AgentEngine(backend=FakeLlmBackend([]), registry=ToolRegistry())
    engine.stop_accepting_events()
    event = Event.from_payload(
        {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1", "event_id": "during_shutdown"},
            "event": {"message": {"chat_id": "oc_1", "message_id": "om_1"}},
        }
    )

    with pytest.raises(RuntimeError, match="not accepting events"):
        await engine.run(event)


async def test_websocket_shutdown_marks_not_ready_before_closing_intake(tmp_path: Path) -> None:
    agent = make_agent(tmp_path)
    await agent.start()

    agent._begin_shutdown(stop_engine=False)

    assert agent.status == "draining"
    assert not agent.is_ready
    assert agent.engine.accepting_events

    await agent.aclose()
    assert not agent.engine.accepting_events


async def test_websocket_startup_error_leaves_runtime_unstarted() -> None:
    engine = AgentEngine(backend=FakeLlmBackend([]), registry=ToolRegistry())
    agent = Agent(engine=engine)

    with pytest.raises(RuntimeError, match="feishu.app_id / feishu.app_secret"):
        await agent.run_ws()

    assert agent.status == "created"
