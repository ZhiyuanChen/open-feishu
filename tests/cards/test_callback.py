"""``parse_action`` and the ``CardAction`` view over card-action callback payloads."""

import pytest

from feishu.cards.callback import CardAction, parse_action


def _payload():
    return {
        "schema": "2.0",
        "header": {"event_type": "card.action.trigger"},
        "event": {
            "operator": {"tenant_key": "t", "user_id": "u1", "open_id": "ou_1", "union_id": "on_1"},
            "token": "c-update-token",
            "action": {
                "value": {"__approval__": "ap_1", "decision": "approve"},
                "tag": "button",
                "name": "approve",
                "form_value": {"field": "x"},
            },
            "context": {"open_message_id": "om_1", "open_chat_id": "oc_1"},
        },
    }


class _WithBody:
    body = _payload()["event"]


class TestParseAction:
    def test_parses_full_event(self):
        a = parse_action(_payload())
        assert isinstance(a, CardAction)
        assert a.value == {"__approval__": "ap_1", "decision": "approve"}
        assert a.token == "c-update-token"
        assert a.message_id == "om_1"
        assert a.chat_id == "oc_1"
        assert a.tag == "button"
        assert a.name == "approve"
        assert a.form_value == {"field": "x"}

    @pytest.mark.parametrize(
        "event",
        [
            _payload(),  # full event payload
            _payload()["event"],  # already-unwrapped event node
            _WithBody(),  # object exposing a mapping .body
        ],
    )
    def test_accepts_event_forms(self, event):
        a = parse_action(event)
        assert a.token == "c-update-token"
        assert a.message_id == "om_1"

    def test_rejects_unextractable(self):
        with pytest.raises(TypeError):
            parse_action(object())

    def test_missing_fields_default_safely(self):
        a = parse_action({"event": {"action": {}}})
        assert a.value == {}
        assert a.form_value == {}
        assert a.tag is None
        assert a.name is None
        assert a.open_id is None
        assert a.token is None


class TestOperatorAccessors:
    @pytest.mark.parametrize(
        "attr, expected",
        [("open_id", "ou_1"), ("user_id", "u1"), ("union_id", "on_1")],
    )
    def test_ids(self, attr, expected):
        assert getattr(parse_action(_payload()), attr) == expected

    def test_operator_mapping(self):
        assert parse_action(_payload()).operator["open_id"] == "ou_1"
