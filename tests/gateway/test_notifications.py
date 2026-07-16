from __future__ import annotations

import pytest

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


def test_file_store_persists_messages(tmp_path) -> None:
    path = tmp_path / "event-messages.json"
    store = JsonFileEventMessageStore(path)
    store.set("incident:42", "om_event")

    assert JsonFileEventMessageStore(path).get("incident:42") == "om_event"
