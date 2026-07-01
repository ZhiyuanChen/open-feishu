import json

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from feishu.events import (
    EventDispatcher,
    InMemorySeenStore,
    create_event_app,
)
from feishu.events.receiver import create_card_route, create_event_route
from tests.conftest import encrypt_event, signed_event

ENCRYPT_KEY = "ek_secret"
VTOKEN = "verify_tok"


def encrypt(plaintext_dict):
    return encrypt_event(ENCRYPT_KEY, plaintext_dict)


def event_payload(event_id="e1", event_type="im.message.receive_v1", **header):
    """A schema-2.0 event-webhook body."""
    hdr = {"event_type": event_type}
    if event_id is not None:
        hdr["event_id"] = event_id
    hdr.update(header)
    return {"schema": "2.0", "header": hdr, "event": {}}


def recording_dispatcher(event_type, *, returns=None):
    """An EventDispatcher whose handler records the event_ids it sees.

    Returns ``(dispatcher, seen_ids)`` where ``seen_ids`` is the live list.
    """
    d = EventDispatcher()
    seen_ids = []

    @d.on(event_type)
    async def handle(event):
        seen_ids.append(event.event_id)
        return returns

    return d, seen_ids


def event_client(dispatcher, **kwargs):
    return TestClient(Starlette(routes=[create_event_route(dispatcher, **kwargs)]))


def card_client(dispatcher, **kwargs):
    return TestClient(Starlette(routes=[create_card_route(dispatcher, **kwargs)]))


class TestEventRoute:
    def test_challenge_echo(self):
        client = event_client(EventDispatcher(), verification_token=VTOKEN)
        resp = client.post(
            "/feishu/event",
            json={"type": "url_verification", "challenge": "chal_123", "token": VTOKEN},
        )
        assert resp.status_code == 200
        assert resp.json() == {"challenge": "chal_123"}

    def test_rejects_bad_token(self):
        client = event_client(EventDispatcher(), verification_token=VTOKEN)
        resp = client.post(
            "/feishu/event",
            json={"type": "url_verification", "challenge": "chal_123", "token": "WRONG"},
        )
        assert resp.status_code == 401

    def test_signed_encrypted_handshake_succeeds(self):
        body = {"encrypt": encrypt({"type": "url_verification", "challenge": "c9", "token": VTOKEN})}
        raw, headers = signed_event(body, encrypt_key=ENCRYPT_KEY)
        client = event_client(EventDispatcher(), encrypt_key=ENCRYPT_KEY, verification_token=VTOKEN)
        resp = client.post("/feishu/event", content=raw, headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"challenge": "c9"}

    def test_unsigned_encrypted_handshake_rejected_before_decrypt(self):
        body = {"encrypt": encrypt({"type": "url_verification", "challenge": "c9", "token": "wrong"})}
        client = event_client(EventDispatcher(), encrypt_key=ENCRYPT_KEY, verification_token=VTOKEN)
        resp = client.post("/feishu/event", json=body)
        assert resp.status_code == 401
        assert resp.json() == {"msg": "signature required"}

    def test_signature_mismatch_returns_401(self):
        body = {"encrypt": encrypt({"type": "url_verification", "challenge": "c", "token": VTOKEN})}
        raw = json.dumps(body).encode("utf-8")
        client = event_client(EventDispatcher(), encrypt_key=ENCRYPT_KEY, verification_token=VTOKEN)
        resp = client.post(
            "/feishu/event",
            content=raw,
            headers={
                "X-Lark-Signature": "deadbeef",  # wrong
                "X-Lark-Request-Timestamp": "1700000000",
                "X-Lark-Request-Nonce": "nonce1",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 401

    def test_valid_signature_dispatches(self):
        d, received = recording_dispatcher("im.message.receive_v1")
        raw, headers = signed_event({"encrypt": encrypt(event_payload("e1", token=VTOKEN))}, encrypt_key=ENCRYPT_KEY)
        client = event_client(d, encrypt_key=ENCRYPT_KEY, verification_token=VTOKEN)
        resp = client.post("/feishu/event", content=raw, headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {}
        # BackgroundTask runs after the response is sent; TestClient drains it.
        assert received == ["e1"]

    def test_plain_event_dispatches(self):
        d, calls = recording_dispatcher("im.message.receive_v1")
        client = event_client(d, seen_store=None)
        assert client.post("/feishu/event", json=event_payload("plain1")).status_code == 200
        assert calls == ["plain1"]

    def test_normal_event_requires_matching_verification_token_when_configured(self):
        d, calls = recording_dispatcher("im.message.receive_v1")
        client = event_client(d, verification_token=VTOKEN, seen_store=None)

        bad = client.post("/feishu/event", json=event_payload("plain_bad", token="wrong"))
        assert bad.status_code == 401
        assert calls == []

        good = client.post("/feishu/event", json=event_payload("plain_good", token=VTOKEN))
        assert good.status_code == 200
        assert calls == ["plain_good"]

    def test_rejects_unsigned_event(self):
        # Security: when encrypt_key is set, a normal event that omits X-Lark-Signature
        # must be rejected with 401 — it must NOT be decrypted and dispatched.
        d, dispatched = recording_dispatcher("im.message.receive_v1")
        body = {"encrypt": encrypt(event_payload("e_nosig"))}
        client = event_client(d, encrypt_key=ENCRYPT_KEY, verification_token=VTOKEN)
        resp = client.post("/feishu/event", json=body)  # No X-Lark-Signature header
        assert resp.status_code == 401
        assert dispatched == []  # handler must NOT have run

    @pytest.mark.parametrize(
        "store_kwargs, expected_calls",
        [
            pytest.param({}, ["d"], id="default-store-dedups"),
            pytest.param({"seen_store": InMemorySeenStore()}, ["d"], id="shared-store-dedups"),
            pytest.param({"seen_store": None}, ["d", "d"], id="none-disables-dedup"),
        ],
    )
    def test_dedup_policy(self, store_kwargs, expected_calls):
        # Out-of-the-box and with an explicit store the route dedups duplicate deliveries;
        # seen_store=None opts out so both deliveries dispatch.
        d, calls = recording_dispatcher("im.message.receive_v1")
        client = event_client(d, **store_kwargs)
        payload = event_payload("d")
        assert client.post("/feishu/event", json=payload).status_code == 200
        assert client.post("/feishu/event", json=payload).status_code == 200
        assert calls == expected_calls

    def test_missing_event_id_is_rejected_before_dispatch(self):
        d, calls = recording_dispatcher("im.message.receive_v1")
        client = event_client(d, seen_store=InMemorySeenStore())
        payload = event_payload(event_id=None)  # no event_id field -> ""
        assert client.post("/feishu/event", json=payload).status_code == 400
        assert calls == []

    @pytest.mark.parametrize(
        "route_kwargs, clock_offset, expected_status, expected_calls",
        [
            # Replay protection: timestamp aged out of the default 300s window -> 401, no dispatch.
            pytest.param({}, 600, 401, [], id="stale-rejected"),
            # Same body within the window is accepted.
            pytest.param({}, 100, 200, ["ev"], id="fresh-accepted"),
            # max_age_seconds=None disables the replay window but still enforces the MAC.
            pytest.param({"max_age_seconds": None}, 99999, 200, ["ev"], id="max-age-none-accepts-old"),
        ],
    )
    def test_freshness_window(self, route_kwargs, clock_offset, expected_status, expected_calls):
        d, dispatched = recording_dispatcher("im.message.receive_v1")
        signed_at = 1700000000
        raw, headers = signed_event(
            {"encrypt": encrypt(event_payload("ev"))}, encrypt_key=ENCRYPT_KEY, timestamp=str(signed_at)
        )
        client = event_client(d, encrypt_key=ENCRYPT_KEY, now=lambda: float(signed_at) + clock_offset, **route_kwargs)
        resp = client.post("/feishu/event", content=raw, headers=headers)
        assert resp.status_code == expected_status
        assert dispatched == expected_calls

    def test_get_is_rejected(self):
        client = event_client(EventDispatcher())
        assert client.get("/feishu/event").status_code == 405

    def test_invalid_json_body_returns_400(self):
        client = event_client(EventDispatcher())
        resp = client.post(
            "/feishu/event",
            content=b"this is not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert "msg" in resp.json()

    @pytest.mark.parametrize("body", [[], "not an object", 42])
    def test_non_object_json_body_returns_400(self, body):
        client = TestClient(
            Starlette(routes=[create_event_route(EventDispatcher())]),
            raise_server_exceptions=False,
        )
        resp = client.post("/feishu/event", json=body)
        assert resp.status_code == 400
        assert resp.json() == {"msg": "invalid event payload"}

    def test_encrypted_without_key_400(self):
        # Body contains an "encrypt" field but no encrypt_key is configured on the route.
        client = event_client(EventDispatcher())
        resp = client.post("/feishu/event", json={"encrypt": "somebase64blob"})
        assert resp.status_code == 400
        assert "msg" in resp.json()

    def test_bad_encrypted_body_returns_400(self):
        app = Starlette(routes=[create_event_route(EventDispatcher(), encrypt_key=ENCRYPT_KEY)])
        client = TestClient(app, raise_server_exceptions=False)
        raw, headers = signed_event({"encrypt": "not-valid-ciphertext"}, encrypt_key=ENCRYPT_KEY)
        resp = client.post("/feishu/event", content=raw, headers=headers)
        assert resp.status_code == 400
        assert "msg" in resp.json()

    def test_encrypt_null_is_controlled_response(self):
        app = Starlette(routes=[create_event_route(EventDispatcher(), encrypt_key=ENCRYPT_KEY)])
        client = TestClient(app, raise_server_exceptions=False)
        raw, headers = signed_event({"encrypt": None}, encrypt_key=ENCRYPT_KEY)
        resp = client.post("/feishu/event", content=raw, headers=headers)
        assert resp.status_code == 400
        assert "msg" in resp.json()


class TestCardRoute:
    CARD_KEY = "ek_card_secret"
    CARD_VTOKEN = "card_vtoken"

    def _encrypt(self, plaintext_dict):
        return encrypt_event(self.CARD_KEY, plaintext_dict)

    def card_payload(self, event_id="c1", **event):
        return {
            "schema": "2.0",
            "header": {"event_type": "card.action.trigger", "event_id": event_id},
            "event": event,
        }

    @pytest.mark.parametrize(
        "returns, expected_body",
        [
            pytest.param(
                {"toast": {"type": "success", "content": "done"}, "card": {"schema": "2.0"}},
                {"toast": {"type": "success", "content": "done"}, "card": {"schema": "2.0"}},
                id="returns-toast-and-card",
            ),
            pytest.param(None, {}, id="returns-none-empty-body"),
        ],
    )
    def test_returns_result_sync(self, returns, expected_body):
        d, _ = recording_dispatcher("card.action.trigger", returns=returns)
        client = card_client(d)
        resp = client.post("/feishu/card", json=self.card_payload("c1", action={"value": {"x": 1}}))
        assert resp.status_code == 200
        assert resp.json() == expected_body

    @pytest.mark.parametrize(
        "token, expected_status",
        [
            pytest.param("vt", 200, id="good-token"),
            pytest.param("WRONG", 401, id="bad-token"),
        ],
    )
    def test_token_check(self, token, expected_status):
        client = card_client(EventDispatcher(), verification_token="vt")
        resp = client.post(
            "/feishu/card",
            json={"type": "url_verification", "challenge": "z", "token": token},
        )
        assert resp.status_code == expected_status
        if expected_status == 200:
            assert resp.json() == {"challenge": "z"}

    def test_normal_card_requires_matching_verification_token_when_configured(self):
        d, calls = recording_dispatcher("card.action.trigger", returns={"toast": {"type": "info", "content": "ok"}})
        client = card_client(d, verification_token=self.CARD_VTOKEN, seen_store=None)

        bad_payload = self.card_payload("card_bad")
        bad_payload["header"]["token"] = "wrong"
        bad = client.post("/feishu/card", json=bad_payload)
        assert bad.status_code == 401
        assert calls == []

        good_payload = self.card_payload("card_good")
        good_payload["header"]["token"] = self.CARD_VTOKEN
        good = client.post("/feishu/card", json=good_payload)
        assert good.status_code == 200
        assert calls == ["card_good"]

    def test_signed_encrypted_handshake_succeeds(self):
        body = {
            "encrypt": self._encrypt({"type": "url_verification", "challenge": "card_chal", "token": self.CARD_VTOKEN})
        }
        raw, headers = signed_event(body, encrypt_key=self.CARD_KEY)
        client = card_client(EventDispatcher(), encrypt_key=self.CARD_KEY, verification_token=self.CARD_VTOKEN)
        resp = client.post("/feishu/card", content=raw, headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"challenge": "card_chal"}

    def test_unsigned_encrypted_handshake_rejected_before_decrypt(self):
        body = {"encrypt": self._encrypt({"type": "url_verification", "challenge": "card_chal", "token": "wrong"})}
        client = card_client(EventDispatcher(), encrypt_key=self.CARD_KEY, verification_token=self.CARD_VTOKEN)
        resp = client.post("/feishu/card", json=body)
        assert resp.status_code == 401
        assert resp.json() == {"msg": "signature required"}

    def test_rejects_unsigned(self):
        # Security: card.action.trigger with encrypt_key set but no signature -> 401, no dispatch.
        d, dispatched = recording_dispatcher("card.action.trigger", returns={"toast": {}, "card": {}})
        body = {"encrypt": self._encrypt(self.card_payload("card_nosig"))}
        client = card_client(d, encrypt_key=self.CARD_KEY, verification_token=self.CARD_VTOKEN)
        resp = client.post("/feishu/card", json=body)  # No X-Lark-Signature header
        assert resp.status_code == 401
        assert dispatched == []

    def test_rejects_bad_signature(self):
        # Security: card.action.trigger with wrong signature -> 401, no dispatch.
        d, dispatched = recording_dispatcher("card.action.trigger", returns={})
        body = {"encrypt": self._encrypt(self.card_payload("card_badsig"))}
        raw = json.dumps(body).encode("utf-8")
        client = card_client(d, encrypt_key=self.CARD_KEY, verification_token=self.CARD_VTOKEN)
        resp = client.post(
            "/feishu/card",
            content=raw,
            headers={
                "X-Lark-Signature": "deadbeef",  # wrong
                "X-Lark-Request-Timestamp": "1700000000",
                "X-Lark-Request-Nonce": "nonce1",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 401
        assert dispatched == []

    @pytest.mark.parametrize(
        "clock_offset, expected_status, expected_calls",
        [
            # Valid signature, fresh timestamp: handler runs and returns its dict synchronously.
            pytest.param(0, 200, ["card_sig"], id="fresh-dispatches"),
            # Replay protection: aged out of the default 300s window -> 401, no dispatch.
            pytest.param(600, 401, [], id="stale-rejected"),
        ],
    )
    def test_signed_request_freshness(self, clock_offset, expected_status, expected_calls):
        returns = {"toast": {"type": "info", "content": "ok"}}
        d, dispatched = recording_dispatcher("card.action.trigger", returns=returns)
        signed_at = 1700000000
        payload = self.card_payload("card_sig")
        payload["header"]["token"] = self.CARD_VTOKEN
        raw, headers = signed_event(
            {"encrypt": self._encrypt(payload)},
            encrypt_key=self.CARD_KEY,
            timestamp=str(signed_at),
        )
        client = card_client(
            d,
            encrypt_key=self.CARD_KEY,
            verification_token=self.CARD_VTOKEN,
            now=lambda: float(signed_at) + clock_offset,
        )
        resp = client.post("/feishu/card", content=raw, headers=headers)
        assert resp.status_code == expected_status
        assert dispatched == expected_calls
        if expected_status == 200:
            assert resp.json() == returns

    def test_get_rejected(self):
        client = card_client(EventDispatcher())
        assert client.get("/feishu/card").status_code == 405

    @pytest.mark.parametrize("body", [[], "not an object", 42])
    def test_non_object_json_body_returns_400(self, body):
        client = TestClient(
            Starlette(routes=[create_card_route(EventDispatcher())]),
            raise_server_exceptions=False,
        )
        resp = client.post("/feishu/card", json=body)
        assert resp.status_code == 400
        assert resp.json() == {"msg": "invalid event payload"}

    def test_missing_event_id_is_rejected_before_dispatch(self):
        d, calls = recording_dispatcher("card.action.trigger", returns={"toast": {"type": "info", "content": "ok"}})
        payload = self.card_payload(event_id=None)
        client = card_client(d, seen_store=InMemorySeenStore())
        resp = client.post("/feishu/card", json=payload)
        assert resp.status_code == 400
        assert calls == []

    def test_encrypt_null_is_controlled_response(self):
        app = Starlette(routes=[create_card_route(EventDispatcher(), encrypt_key=self.CARD_KEY)])
        client = TestClient(app, raise_server_exceptions=False)
        raw, headers = signed_event({"encrypt": None}, encrypt_key=self.CARD_KEY)
        resp = client.post("/feishu/card", content=raw, headers=headers)
        assert resp.status_code == 400
        assert "msg" in resp.json()

    @pytest.mark.parametrize(
        "store_kwargs, expect_dedup",
        [
            pytest.param({}, True, id="default-store-dedups"),
            pytest.param({"seen_store": InMemorySeenStore()}, True, id="shared-store-dedups"),
            pytest.param({"seen_store": None}, False, id="none-disables-dedup"),
        ],
    )
    def test_dedup_policy(self, store_kwargs, expect_dedup):
        # On a duplicate delivery a deduping store runs the handler once and the second
        # response is empty {}; seen_store=None runs the handler on every delivery.
        result = {"toast": {"type": "info", "content": "ok"}}
        d, calls = recording_dispatcher("card.action.trigger", returns=result)
        client = card_client(d, **store_kwargs)
        payload = self.card_payload("dc")

        resp1 = client.post("/feishu/card", json=payload)
        assert resp1.status_code == 200
        assert resp1.json() == result

        resp2 = client.post("/feishu/card", json=payload)
        assert resp2.status_code == 200
        if expect_dedup:
            assert resp2.json() == {}
            assert calls == ["dc"]
        else:
            assert resp2.json() == result
            assert calls == ["dc", "dc"]


class TestEventApp:
    def test_mounts_both_routes(self):
        d, _ = recording_dispatcher("card.action.trigger", returns={"toast": {"type": "success", "content": "ok"}})
        app = create_event_app(d, verification_token="vt", seen_store=InMemorySeenStore())
        assert isinstance(app, Starlette)
        client = TestClient(app)

        # event route handshake
        r1 = client.post("/feishu/event", json={"type": "url_verification", "challenge": "a", "token": "vt"})
        assert r1.json() == {"challenge": "a"}

        # card route synchronous return
        r2 = client.post("/feishu/card", json=event_payload("c1", event_type="card.action.trigger", token="vt"))
        assert r2.json() == {"toast": {"type": "success", "content": "ok"}}

    def test_card_seen_store_dedup(self):
        # create_event_app with card_seen_store wired dedups card deliveries end-to-end.
        result = {"toast": {"type": "success", "content": "app-done"}, "card": {}}
        d, calls = recording_dispatcher("card.action.trigger", returns=result)
        app = create_event_app(d, card_seen_store=InMemorySeenStore())
        client = TestClient(app)
        payload = event_payload("app_dedup_card_1", event_type="card.action.trigger")

        resp1 = client.post("/feishu/card", json=payload)
        assert resp1.status_code == 200
        assert resp1.json() == result

        resp2 = client.post("/feishu/card", json=payload)
        assert resp2.status_code == 200
        assert resp2.json() == {}
        assert calls == ["app_dedup_card_1"]

    def test_can_omit_card_route(self):
        app = create_event_app(EventDispatcher(), card_path=None)
        client = TestClient(app)
        assert client.post("/feishu/card", json={}).status_code == 404

    def test_events_public_exports(self):
        import feishu.events as ev

        for name in [
            "Event",
            "EventDispatcher",
            "SeenStore",
            "InMemorySeenStore",
            "verify_signature",
            "decrypt",
            "create_event_route",
            "create_card_route",
            "create_event_app",
        ]:
            assert hasattr(ev, name), name
