from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from feishu.agent import Agent
from feishu.agent.loop import AgentEngine
from feishu.agent.tools import ToolRegistry
from tests._fakes import FakeLlmBackend


class _Client:
    pass


def test_agent_facade(tmp_path: Path) -> None:
    agent = Agent(
        {"storage": {"path": str(tmp_path / "agent.db")}, "toolkits": []},
        client=_Client(),
        backend=FakeLlmBackend([]),
        registry=ToolRegistry(),
    )

    assert isinstance(agent.engine, AgentEngine)


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
