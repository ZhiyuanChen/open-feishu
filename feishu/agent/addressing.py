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

import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ..im.inbound import is_mentioned

MessageFilter = Callable[[Any], bool | Awaitable[bool]]

logger = logging.getLogger("feishu")


def addressed_group_messages(
    *,
    client: Any | None = None,
    bot_open_id: str | None = None,
    bot_union_id: str | None = None,
    wake_on_app_reply: bool = True,
) -> MessageFilter:
    r"""Create a filter that wakes on direct chats, bot mentions, or replies to app messages."""

    async def allows(event: Any) -> bool:
        message = _message(event)
        if not _looks_like_group_message(message):
            return True
        if (bot_open_id or bot_union_id) and is_mentioned(dict(message), open_id=bot_open_id, union_id=bot_union_id):
            return True
        if wake_on_app_reply and client is not None and await _replies_to_app_message(client, message):
            return True
        return False

    return allows


async def message_filter_allows(message_filter: MessageFilter | None, event: Any) -> bool:
    if message_filter is None:
        return True
    allowed = message_filter(event)
    if inspect.isawaitable(allowed):
        return bool(await allowed)
    return bool(allowed)


def _message(event: Any) -> Mapping[str, Any]:
    body = getattr(event, "body", None) or {}
    message = body.get("message") or {}
    return message if isinstance(message, Mapping) else {}


def _looks_like_group_message(message: Mapping[str, Any]) -> bool:
    chat_type = str(message.get("chat_type") or "").strip().lower()
    if chat_type == "p2p":
        return False
    if chat_type:
        return True
    return str(message.get("chat_id") or "").startswith("oc_")


async def _replies_to_app_message(client: Any, message: Mapping[str, Any]) -> bool:
    for message_id in _reply_target_ids(message):
        try:
            parent = await client.im.get(message_id)
        except Exception:  # noqa: BLE001 - routing should fail closed when parent lookup fails
            logger.debug("could not resolve reply target message %s", message_id, exc_info=True)
            continue
        parent = _first_message(parent)
        if _sender_type(parent) == "app":
            return True
    return False


def _reply_target_ids(message: Mapping[str, Any]) -> tuple[str, ...]:
    current_id = str(message.get("message_id") or "")
    seen: list[str] = []
    for key in ("parent_id", "root_id"):
        value = str(message.get(key) or "")
        if value and value != current_id and value not in seen:
            seen.append(value)
    return tuple(seen)


def _sender_type(message: Mapping[str, Any]) -> str:
    sender = message.get("sender") or {}
    if not isinstance(sender, Mapping):
        return ""
    return str(sender.get("sender_type") or "").strip().lower()


def _first_message(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    items = value.get("items")
    if isinstance(items, list) and items and isinstance(items[0], Mapping):
        return items[0]
    return value


__all__ = ["MessageFilter", "addressed_group_messages", "message_filter_allows"]
