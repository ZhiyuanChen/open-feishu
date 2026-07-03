# OpenFeishu
# Copyright (C) 2024-Present  DanLing

# This file is part of OpenFeishu.

# OpenFeishu is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.

# OpenFeishu is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# For additional terms and clarifications, please refer to our License FAQ at:
# <https://multimolecule.danling.org/about/license-faq>.

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .llm import Message, ToolCall, ToolResultPart, ToolUsePart

AUTH_CARD_SENT_NOTE = (
    "user authorization required; an interactive authorization card with an authorize button was sent "
    "to the user. Briefly ask them to tap it to authorize, then you'll continue. Do NOT output any URL."
)

AWAITING_APPROVAL_NOTE = "[Awaiting your confirmation — this action has not been performed yet.]"
AWAITING_AUTHORIZATION_NOTE = "[Awaiting user authorization — this tool has not been performed yet.]"
INTERRUPTED_TOOL_NOTE = "[Interrupted by a newer user message — this tool was not completed.]"


def suspension_progress_note(suspension: str) -> str:
    r"""把挂起原因映射为进度卡片的最终状态文案。"""
    if suspension == "authorization":
        from .oauth import AWAITING_AUTHORIZATION_PROGRESS_TEXT

        return AWAITING_AUTHORIZATION_PROGRESS_TEXT
    from .approval import AWAITING_APPROVAL_PROGRESS_TEXT

    return AWAITING_APPROVAL_PROGRESS_TEXT


def tool_calls_after(history: list[Message], tool_call_id: str) -> list[ToolCall]:
    r"""返回与 ``tool_call_id`` 位于同一条 assistant 消息中、且排在其后的工具调用。"""
    for message in history:
        if message.role != "assistant":
            continue
        after = False
        calls: list[ToolCall] = []
        for part in message.content:
            if not isinstance(part, ToolUsePart):
                continue
            if after:
                calls.append(
                    ToolCall(
                        id=part.id,
                        name=part.name,
                        arguments=json.dumps(part.arguments, ensure_ascii=False),
                    )
                )
            elif part.id == tool_call_id:
                after = True
        if after:
            return calls
    return []


def replace_tool_result(history: list[Message], tool_call_id: str, new_part: ToolResultPart) -> bool:
    r"""把历史中匹配 `tool_call_id` 的工具结果原地替换为 `new_part`，命中返回 `True`。"""
    for message in history:
        if message.role != "tool":
            continue
        for index, part in enumerate(message.content):
            if isinstance(part, ToolResultPart) and part.tool_call_id == tool_call_id:
                message.content[index] = new_part
                return True
    return False


def authorization_card_message_id(extra: Mapping[str, Any] | None) -> str | None:
    r"""从挂起授权的扩展字段里取出授权卡片消息 ID。"""
    if not extra:
        return None
    message_id = extra.get("auth_card_message_id")
    return str(message_id) if message_id else None


def authorization_complete_card() -> dict[str, Any]:
    r"""构造授权完成后的中性卡片，用于无法 recall 时的降级 patch。"""
    from ..cards.builder import Card

    return Card().header("授权已完成", template="green").markdown("授权已完成，我会回到原对话继续处理。").to_dict()
