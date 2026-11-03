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
from dataclasses import dataclass
from typing import Any, Literal, cast

from ..auth import user_from_identity_keys
from ..events.conversations import is_group_message
from ..events.envelope import Event
from ..events.jobs import current_event_effect, current_event_job
from .session import PendingApproval, PendingAuthorization

_MESSAGE_CONTEXT_FIELDS = ("chat_type", "root_id", "parent_id", "thread_id")


@dataclass(frozen=True)
class ScheduledTurn:
    """A trusted scheduled turn, independent of Feishu inbound events."""

    chat_id: str
    user: Mapping[str, str]
    session_id: str
    run_id: str
    policy: Literal["read_only", "confirm_writes"]
    tenant_key: str | None = None
    timezone: str | None = None
    lease_token: str | None = None

    def __post_init__(self) -> None:
        session = self.session_id.split("::", 1)[-1]
        if (
            not self.chat_id
            or not self.tenant_key
            or not self.run_id
            or not session.startswith("scheduled:")
            or session.rsplit(":", 1)[-1] != self.run_id
        ):
            raise ValueError("scheduled turn requires a chat, run and scheduled session")
        if not any(self.user.get(key) for key in ("user_id", "open_id", "union_id")):
            raise ValueError("scheduled turn requires an authenticated owner")
        if self.policy not in ("read_only", "confirm_writes"):
            raise ValueError("unsupported scheduled policy")


def turn_message(event: Any) -> Mapping[str, Any]:
    """Get routing data from a scheduled turn or an incoming event."""
    if isinstance(event, ScheduledTurn):
        return {"chat_id": event.chat_id}
    return event.body.get("message") or {}


def _message_context(message: Mapping[str, Any]) -> dict[str, str]:
    return {key: value for key in _MESSAGE_CONTEXT_FIELDS if isinstance(value := message.get(key), str) and value}


def pending_message_extra(event: Any) -> dict[str, Any]:
    """Save only message routing, excluding content and identity supplied by callbacks."""
    if isinstance(event, ScheduledTurn):
        return {"scheduled_policy": event.policy, "scheduled_run_id": event.run_id}
    message = turn_message(event)
    context = _message_context(message)
    return {"message_context": context} if context else {}


def event_from_pending(pending: PendingApproval | PendingAuthorization) -> Event | ScheduledTurn:
    """Restore original routing; pending fields remain authoritative for target and owner."""
    if pending.session_id.split("::", 1)[-1].startswith("scheduled:"):
        policy = pending.extra.get("scheduled_policy")
        run_id = pending.extra.get("scheduled_run_id")
        if not isinstance(policy, str) or not isinstance(run_id, str):
            raise ValueError("scheduled continuation is missing policy or run")
        from .scheduled_policy import current_scheduled_lease

        return ScheduledTurn(
            chat_id=pending.chat_id or "",
            user=user_from_identity_keys(pending.owner_user_keys),
            session_id=pending.session_id,
            run_id=run_id,
            policy=cast(Literal["read_only", "confirm_writes"], policy),
            tenant_key=pending.tenant_key,
            lease_token=current_scheduled_lease(run_id),
        )
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
    message = turn_message(event)
    message_id = message.get("message_id")
    async with current_event_effect():
        if (
            not isinstance(event, ScheduledTurn)
            and is_group_message(event)
            and isinstance(message_id, str)
            and message_id
        ):
            result = await client.im.reply(message_id, card, msg_type="interactive")
        else:
            result = await client.im.send(
                message.get("chat_id"), card, msg_type="interactive", receive_id_type="chat_id"
            )
        job = current_event_job()
        if job is not None and job.lease_token:
            data = result.get("data") if isinstance(result, Mapping) else None
            if not isinstance(result, Mapping) or not (
                result.get("message_id") or (isinstance(data, Mapping) and data.get("message_id"))
            ):
                raise RuntimeError("card delivery response has no message ID")
        return result
