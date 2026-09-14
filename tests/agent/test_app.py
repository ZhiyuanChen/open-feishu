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
