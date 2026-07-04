from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from feishu.agent.prompting import build_time_aware_system_prompt, build_time_context, build_timezone_resolver


def test_time_aware_system_prompt_appends_current_time_context() -> None:
    async def timezone_resolver(_event=None) -> str:
        return "Asia/Shanghai"

    system = build_time_aware_system_prompt(
        "base prompt",
        timezone_resolver,
        now=lambda tz: datetime(2026, 7, 2, 9, 30, 5, tzinfo=tz),
    )
    rendered = asyncio.run(system(timezone="Europe/Berlin"))

    assert rendered.startswith("base prompt")
    assert "Current datetime: 2026-07-02T09:30:05+02:00" in rendered
    assert "Current date: 2026-07-02" in rendered
    assert "Current timezone: Europe/Berlin" in rendered


def test_time_context_can_be_rendered_separately_from_system_prompt() -> None:
    async def timezone_resolver(_event=None) -> str:
        return "Asia/Shanghai"

    context = build_time_context(
        timezone_resolver,
        now=lambda tz: datetime(2026, 7, 2, 9, 30, 5, tzinfo=tz),
    )
    rendered = asyncio.run(context(timezone="Europe/Berlin"))

    assert rendered == (
        "Current datetime: 2026-07-02T09:30:05+02:00\n" "Current date: 2026-07-02\n" "Current timezone: Europe/Berlin"
    )


def test_timezone_resolver_prefers_event_context_timezone() -> None:
    resolver = build_timezone_resolver("Asia/Shanghai")
    event = SimpleNamespace(body={"context": {"timezone": "Europe/Berlin"}})

    assert asyncio.run(resolver(event)) == "Europe/Berlin"


def test_timezone_resolver_can_read_user_timezone_from_user_token() -> None:
    class _UserTokens:
        async def user_token(self, user):
            assert user == {"open_id": "ou_1"}
            return "u-token"

    class _OAuth:
        async def user_info(self, token):
            assert token == "u-token"
            return {"user": {"time_zone": "America/Los_Angeles"}}

    client = SimpleNamespace(oauth=_OAuth())
    resolver = build_timezone_resolver("Asia/Shanghai", user_tokens=_UserTokens(), client=client)
    event = SimpleNamespace(body={"sender": {"sender_id": {"open_id": "ou_1"}}})

    assert asyncio.run(resolver(event)) == "America/Los_Angeles"
    assert ZoneInfo("America/Los_Angeles")
