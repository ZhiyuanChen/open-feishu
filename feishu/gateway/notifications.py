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

import hashlib
import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class EventMessageStore(Protocol):
    r"""Stores the Feishu message ID associated with an event ID."""

    def get(self, event_id: str) -> str | None:
        r"""Return the Feishu message ID previously sent for ``event_id``."""
        ...

    def set(self, event_id: str, message_id: str) -> None:
        r"""Persist the Feishu message ID for ``event_id``."""
        ...


class InMemoryEventMessageStore:
    r"""Process-local ``event_id -> message_id`` store."""

    def __init__(self) -> None:
        self._messages: dict[str, str] = {}

    def get(self, event_id: str) -> str | None:
        return self._messages.get(event_id)

    def set(self, event_id: str, message_id: str) -> None:
        self._messages[event_id] = message_id


class JsonFileEventMessageStore:
    r"""Small JSON-file store for ``event_id -> message_id`` state."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def get(self, event_id: str) -> str | None:
        return self._read().get(event_id)

    def set(self, event_id: str, message_id: str) -> None:
        with self._lock:
            data = self._read()
            data[event_id] = message_id
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    def _read(self) -> dict[str, str]:
        with self._lock:
            if not self.path.exists():
                return {}
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict):
                return {}
            return {str(key): str(value) for key, value in data.items() if isinstance(value, str)}


@dataclass(frozen=True)
class InteractiveCardDelivery:
    r"""Result of creating or updating an event-keyed interactive card."""

    action: str
    event_id: str
    message_id: str | None


def deterministic_uuid(event_id: str, *, prefix: str = "event-") -> str:
    r"""Return a deterministic Feishu idempotency UUID for an event."""
    return prefix + hashlib.sha256(event_id.encode()).hexdigest()[:32]


async def upsert_interactive_card(
    client: Any,
    event_id: str,
    card: dict[str, Any],
    receive_id: str,
    *,
    store: EventMessageStore,
    receive_id_type: str = "chat_id",
    uuid_prefix: str = "event-",
) -> InteractiveCardDelivery:
    r"""Create an interactive card, or patch the prior card for the same event.

    The ``store`` provides durable ``event_id -> message_id`` state. A repeat
    event patches the previously delivered Feishu card rather than posting a
    duplicate message.
    """
    message_id = store.get(event_id)
    if message_id:
        data = await client.im.patch(message_id, card)
        return InteractiveCardDelivery("updated", event_id, _message_id(data) or message_id)

    data = await client.im.send(
        receive_id,
        card,
        receive_id_type=receive_id_type,
        msg_type="interactive",
        uuid=deterministic_uuid(event_id, prefix=uuid_prefix),
    )
    message_id = _message_id(data)
    if message_id:
        store.set(event_id, message_id)
    return InteractiveCardDelivery("created", event_id, message_id)


def _message_id(data: Any) -> str | None:
    if isinstance(data, Mapping):
        message_id = data.get("message_id")
        if isinstance(message_id, str) and message_id:
            return message_id
    return None
