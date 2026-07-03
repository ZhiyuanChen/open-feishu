from __future__ import annotations

from pathlib import Path

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
