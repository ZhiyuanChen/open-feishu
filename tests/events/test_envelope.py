import pytest

from feishu.events.envelope import Event

EVENT_2_0 = {
    "schema": "2.0",
    "header": {
        "event_id": "evt_2",
        "event_type": "im.message.receive_v1",
        "create_time": "1700000000000",
        "tenant_key": "tk_1",
        "app_id": "cli_app",
        "token": "vtok",
    },
    "event": {"message": {"chat_id": "oc_x", "content": '{"text":"hi"}'}},
}

EVENT_1_0 = {
    "uuid": "u_1",
    "token": "vtok_legacy",
    "ts": "1699999999",
    "type": "event_callback",
    "event": {"type": "message", "chat_id": "oc_legacy", "text_without_at_bot": "hi"},
}


class TestAccessors:
    @pytest.mark.parametrize(
        "payload, schema, event_type, event_id, create_time, tenant_key, app_id, token",
        [
            (EVENT_2_0, "2.0", "im.message.receive_v1", "evt_2", "1700000000000", "tk_1", "cli_app", "vtok"),
            (EVENT_1_0, "1.0", "message", "u_1", "1699999999", None, None, "vtok_legacy"),
        ],
        ids=["2.0", "1.0"],
    )
    def test_routes_fields_by_schema(
        self, payload, schema, event_type, event_id, create_time, tenant_key, app_id, token
    ):
        ev = Event.from_payload(payload)
        assert ev.schema_version == schema
        assert ev.event_type == event_type
        assert ev.event_id == event_id
        assert ev.create_time == create_time
        assert ev.tenant_key == tenant_key
        assert ev.app_id == app_id
        assert ev.token == token

    @pytest.mark.parametrize(
        "payload, chat_id",
        [(EVENT_2_0, "oc_x"), (EVENT_1_0, "oc_legacy")],
        ids=["2.0", "1.0"],
    )
    def test_body_is_event_field(self, payload, chat_id):
        ev = Event.from_payload(payload)
        body = ev.body
        # 2.0 nests under message; 1.0 puts chat_id directly on the event body.
        assert (body.get("message") or body)["chat_id"] == chat_id


class TestSchemaDetection:
    def test_detects_by_header(self):
        ev = Event.from_payload({"header": {"event_type": "card.action.trigger", "event_id": "e9"}, "event": {}})
        assert ev.schema_version == "2.0"
        assert ev.event_type == "card.action.trigger"
        assert ev.event_id == "e9"


class TestMissingFields:
    def test_optional_fields_default(self):
        ev = Event.from_payload({"schema": "2.0", "header": {"event_type": "x"}, "event": {}})
        assert ev.event_type == "x"
        assert ev.event_id == ""  # required-ish but absent -> empty string, never KeyError
        assert ev.create_time is None
        assert ev.tenant_key is None
        assert ev.app_id is None
        assert ev.token is None

    def test_body_indexable_when_absent(self):
        payload = {"schema": "2.0", "header": {"event_type": "x"}}
        ev = Event.from_payload(payload)
        assert ev.body.get("anything") is None
        assert ev.raw == payload
