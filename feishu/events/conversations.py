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

from collections.abc import Mapping
from typing import Any


def _message(event: Any) -> Mapping[str, Any]:
    body = getattr(event, "body", None)
    if not isinstance(body, Mapping):
        return {}
    message = body.get("message")
    return message if isinstance(message, Mapping) else {}


def is_group_message(event: Any) -> bool:
    """Identify explicitly typed group messages without guessing from the chat ID.

    Legacy events without ``chat_type`` retain the existing conversation and
    interruption behavior. Feishu uses ``p2p`` for direct chats; other nonempty
    types identify group conversations.
    """
    chat_type = str(_message(event).get("chat_type") or "").strip().lower()
    return bool(chat_type) and chat_type != "p2p"


def session_id_for_message(event: Any) -> str:
    """Derive the same history and durable execution lane from a message event.

    Direct chats share one lane, including replies. Each group root message
    starts a lane and its replies share that root's lane. Events without
    ``chat_type`` keep the legacy chat/root routing. A malformed group message
    needs at least a root, message, or event ID so it cannot join a shared lane.
    """
    message = _message(event)
    chat_id = str(message.get("chat_id") or "")
    message_id = str(message.get("message_id") or "")
    event_id = str(getattr(event, "event_id", "") or "")
    chat_type = str(message.get("chat_type") or "").strip().lower()
    if chat_type == "p2p":
        return chat_id or message_id or event_id
    root_id = str(message.get("root_id") or "")
    if is_group_message(event):
        branch_id = root_id or message_id or (f"event:{event_id}" if event_id else "")
        if not branch_id:
            raise ValueError("group message has no root, message, or event identity")
        return f"{chat_id}:{branch_id}" if chat_id else branch_id
    if chat_id:
        return f"{chat_id}:{root_id}" if root_id else chat_id
    return message_id or event_id
