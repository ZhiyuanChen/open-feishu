from __future__ import annotations

import pytest
from chanfig import NestedDict

from feishu.gateway.notifications import (
    InMemoryEventMessageStore,
    JsonFileEventMessageStore,
    deterministic_uuid,
    upsert_interactive_card,
)


class _Recorder:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


class _StubIM:
    def __init__(self):
        self.send = _Recorder(NestedDict({"message_id": "om_event"}))
        self.patch = _Recorder(NestedDict({"message_id": "om_event"}))


class _StubClient:
    def __init__(self):
        self.im = _StubIM()


@pytest.mark.asyncio
async def test_upsert_interactive_card_creates_then_patches_same_event() -> None:
    client = _StubClient()
    store = InMemoryEventMessageStore()
    first_card = {"body": {"elements": [{"content": "firing"}]}}
    resolved_card = {"body": {"elements": [{"content": "resolved"}]}}

    first = await upsert_interactive_card(
        client,
        "incident:42",
        first_card,
        "oc_ops",
        store=store,
        uuid_prefix="incident-",
    )
    second = await upsert_interactive_card(
        client,
        "incident:42",
        resolved_card,
        "oc_ops",
        store=store,
        uuid_prefix="incident-",
    )

    assert first.action == "created"
    assert first.message_id == "om_event"
    assert second.action == "updated"
    assert second.message_id == "om_event"
    assert len(client.im.send.calls) == 1
    assert len(client.im.patch.calls) == 1
    send_args, send_kwargs = client.im.send.calls[0]
    assert send_args == ("oc_ops", first_card)
    assert send_kwargs["msg_type"] == "interactive"
    assert send_kwargs["uuid"].startswith("incident-")
    assert client.im.patch.calls[0][0] == ("om_event", resolved_card)


def test_deterministic_uuid_is_stable_and_uses_the_requested_prefix() -> None:
    assert deterministic_uuid("incident:42", prefix="incident-") == deterministic_uuid(
        "incident:42", prefix="incident-"
    )
    assert deterministic_uuid("incident:42", prefix="incident-").startswith("incident-")
    assert deterministic_uuid("incident:42", prefix="other-") != deterministic_uuid("incident:42", prefix="incident-")


def test_json_file_event_store_survives_a_new_instance(tmp_path) -> None:
    path = tmp_path / "event-messages.json"
    store = JsonFileEventMessageStore(path)
    store.set("incident:42", "om_event")

    assert JsonFileEventMessageStore(path).get("incident:42") == "om_event"
