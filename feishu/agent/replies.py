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

"""Keep turn UI and durable continuations attached to the originating message."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..events.conversations import is_group_message
from ..events.envelope import Event
from .session import PendingApproval, PendingAuthorization

_MESSAGE_CONTEXT_FIELDS = ("chat_type", "root_id", "parent_id", "thread_id")


def _message_context(message: Mapping[str, Any]) -> dict[str, str]:
    return {key: value for key in _MESSAGE_CONTEXT_FIELDS if isinstance(value := message.get(key), str) and value}


def pending_message_extra(event: Any) -> dict[str, Any]:
    """Save only message routing, excluding content and identity supplied by callbacks."""
    message = event.body.get("message") or {}
    context = _message_context(message)
    return {"message_context": context} if context else {}


def event_from_pending(pending: PendingApproval | PendingAuthorization) -> Event:
    """Restore original routing; pending fields remain authoritative for target and owner."""
    context = pending.extra.get("message_context")
    message: dict[str, Any] = _message_context(context) if isinstance(context, Mapping) else {}
    message.update(message_id=pending.created_message_id, chat_id=pending.chat_id)
    return Event.from_payload(
        {
            "header": {"event_type": "im.message.receive_v1", "tenant_key": pending.tenant_key},
            "event": {"message": message},
        }
    )


async def send_turn_card(client: Any, event: Any, card: dict[str, Any]) -> Any:
    """Group cards reply to the original message; private chats retain ordinary sends."""
    message = event.body.get("message") or {}
    message_id = message.get("message_id")
    if is_group_message(event) and isinstance(message_id, str) and message_id:
        # Leave reply_in_thread unset: Feishu retains the replied message's existing form.
        return await client.im.reply(message_id, card, msg_type="interactive")
    return await client.im.send(message.get("chat_id"), card, msg_type="interactive", receive_id_type="chat_id")
