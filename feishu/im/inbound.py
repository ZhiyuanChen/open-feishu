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

r"""
飞书入站消息的无状态读取助手。

提供从飞书消息体（`im.message.receive_v1` 事件中的 `message` 对象，或
[feishu.im.messages.IMNamespace.get][] 返回的消息数据）中提取信息的纯函数：
[feishu.im.inbound.message_text][] 提取可读文本，
[feishu.im.inbound.is_mentioned][] 判断机器人是否被提及。
"""

from __future__ import annotations

import json
from typing import Any


def is_mentioned(message: dict[str, Any], *, open_id: str | None = None, union_id: str | None = None) -> bool:
    r"""
    判断消息是否提及（@）了指定用户。

    遍历消息的 `mentions` 数组，若其中任一条目的 `id` 匹配给定的 `open_id` 或 `union_id`，
    则返回 `True`。不同事件类型与接口版本下，条目的 `id` 既可能是同时含 `open_id`、`union_id`
    的字典，也可能是单一字符串，因此对两种形态都进行匹配。

    Args:
        message: 飞书消息体字典，通常含 `mentions` 数组。
        open_id: 待匹配的用户 open ID；为空表示不按 open ID 匹配。
        union_id: 待匹配的用户 union ID；为空表示不按 union ID 匹配。

    Returns:
        消息提及了指定用户时返回 `True`，否则返回 `False`。

    飞书文档:
        [接收消息](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)

    Examples:
        >>> message = {
        ...     "content": '{"text":"@_user_1 hi"}',
        ...     "mentions": [{"key": "@_user_1", "id": {"open_id": "ou_bot", "union_id": "on_bot"}, "name": "Bot"}],
        ... }
        >>> is_mentioned(message, open_id="ou_bot")
        True
        >>> is_mentioned(message, open_id="ou_other")
        False
        >>> is_mentioned(message, union_id="on_bot")
        True
        >>> is_mentioned({"mentions": []}, open_id="ou_bot")
        False
    """
    return any(
        _mention_matches(mention, open_id=open_id, union_id=union_id) for mention in message.get("mentions") or []
    )


def message_text(message: dict[str, Any]) -> str:
    r"""
    从飞书消息体中提取可读文本。

    解析消息体内的 `content` JSON：对 `text` 类型读取 `content['text']`；对富文本 `post` 类型
    （`content` 含 `content`/`elements` 二维数组）将各段文本以空行拼接，若含 `title` 则以
    Markdown 二级标题形式前置。随后用消息的 `mentions` 数组将文本中的 `@_user_N` 占位符替换为
    `@<姓名>`。

    Args:
        message: 飞书消息体字典，含 `message_type`/`msg_type`、`content`，可选 `mentions`。

    Returns:
        提取并解析后的文本；无法解析时返回空字符串。

    飞书文档:
        [接收消息](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)

    Examples:
        >>> text_message = {
        ...     "message_type": "text",
        ...     "content": '{"text":"@_user_1 你好"}',
        ...     "mentions": [{"key": "@_user_1", "name": "小明"}],
        ... }
        >>> message_text(text_message)
        '@小明 你好'
        >>> post_message = {
        ...     "message_type": "post",
        ...     "content": '{"title":"标题","content":[[{"tag":"text","text":"正文"}]]}',
        ... }
        >>> message_text(post_message)
        '## 标题\n\n正文'
    """
    raw = message.get("content")
    if not raw:
        return ""
    content = json.loads(raw) if isinstance(raw, str) else raw
    text = _content_text(content)
    for mention in message.get("mentions") or []:
        key = mention.get("key")
        if key:
            text = text.replace(key, f"@{mention.get('name', '')}")
    return text


def _content_text(content: dict[str, Any]) -> str:
    if "text" in content:
        return content["text"] or ""
    rich = content.get("content") or content.get("elements")
    if not rich:
        return ""
    text = "\n\n".join(
        element["text"] for line in rich for element in line if isinstance(element, dict) and "text" in element
    )
    title = content.get("title")
    if title:
        return f"## {title}\n\n{text}"
    return text


def _mention_matches(mention: dict[str, Any], *, open_id: str | None, union_id: str | None) -> bool:
    ident = mention.get("id")
    if isinstance(ident, dict):
        if open_id is not None and ident.get("open_id") == open_id:
            return True
        if union_id is not None and ident.get("union_id") == union_id:
            return True
        return False
    if open_id is not None and ident == open_id:
        return True
    if union_id is not None and ident == union_id:
        return True
    return False
