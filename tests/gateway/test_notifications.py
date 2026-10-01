from __future__ import annotations

from typing import Any

import pytest

from feishu.errors import FeishuError
from feishu.gateway.notifications import (
    InMemoryEventMessageStore,
    JsonFileEventMessageStore,
    upsert_interactive_card,
)


@pytest.mark.asyncio
async def test_upsert_updates_card(gateway_client) -> None:
    store = InMemoryEventMessageStore()
    first_card = {"body": {"elements": [{"content": "firing"}]}}
    resolved_card = {"body": {"elements": [{"content": "resolved"}]}}

    first = await upsert_interactive_card(
        gateway_client,
        "incident:42",
        first_card,
        "oc_ops",
        store=store,
        uuid_prefix="incident-",
    )
    second = await upsert_interactive_card(
        gateway_client,
        "incident:42",
        resolved_card,
        "oc_ops",
        store=store,
        uuid_prefix="incident-",
    )

    assert first.action == "created"
    assert second.action == "updated"
    assert first.message_id == second.message_id
    assert len(gateway_client.im.send.calls) == 1
    assert len(gateway_client.im.patch.calls) == 1
    send_args, _ = gateway_client.im.send.calls[0]
    assert send_args == ("oc_ops", first_card)
    assert gateway_client.im.patch.calls[0][0][1] == resolved_card


@pytest.mark.asyncio
async def test_upsert_sends_a_new_card_when_the_previous_message_is_too_old() -> None:
    class _Patch:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        async def __call__(self, *args: Any, **kwargs: Any) -> dict[str, str]:
            self.calls.append(args)
            raise FeishuError(230031, "Message can only be modified within 14 days after sending.")

    class _Send:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        async def __call__(self, *args: Any, **kwargs: Any) -> dict[str, str]:
            self.calls.append((args, kwargs))
            return {"message_id": "om_new"}

    client = type("Client", (), {})()
    client.im = type("IM", (), {"patch": _Patch(), "send": _Send()})()
    store = InMemoryEventMessageStore()
    store.set("incident:42", "om_old")

    delivery = await upsert_interactive_card(
        client,
        "incident:42",
        {"body": {"elements": [{"content": "firing"}]}},
        "oc_ops",
        store=store,
    )

    assert delivery.action == "created"
    assert delivery.message_id == "om_new"
    assert store.get("incident:42") == "om_new"
    assert client.im.patch.calls
    assert client.im.send.calls


def test_file_store_persists_messages(tmp_path) -> None:
    path = tmp_path / "event-messages.json"
    store = JsonFileEventMessageStore(path)
    store.set("incident:42", "om_event")

    assert JsonFileEventMessageStore(path).get("incident:42") == "om_event"


def test_file_store_treats_blank_file_as_empty(tmp_path) -> None:
    path = tmp_path / "event-messages.json"
    path.write_text("")
    store = JsonFileEventMessageStore(path)

    assert store.get("incident:42") is None
    store.set("incident:42", "om_event")

    assert JsonFileEventMessageStore(path).get("incident:42") == "om_event"
