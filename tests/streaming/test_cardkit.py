from __future__ import annotations

import json as _json

import pytest
from chanfig import NestedDict

from feishu.streaming import _cardkit_spec as spec
from feishu.streaming.cardkit import stream_card
from tests.conftest import envelope

# Feishu enforces a hard cap of 10 streaming ops/s/card. Stated as a literal here
# (independent of the internal spec module) so the behavioral cap stands on its own.
MAX_OPS_PER_SEC = 10


class FakeClock:
    """Deterministic monotonic clock advanced manually by tests; no real time."""

    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def uuid_seq():
    """Deterministic uuid factory so tests can assert uniqueness."""
    n = {"i": 0}

    def factory():
        n["i"] += 1
        return f"uuid-{n['i']}"

    return factory


class LifecycleClient:
    """Fake client returning a card_id on create and recording the full call log."""

    def __init__(self):
        self.calls = []

    async def request(self, method, path, *, params=None, json=None, token_type="tenant", token=None, **kw):
        self.calls.append({"method": method, "path": path, "params": params, "json": json})
        if path == spec.CREATE_CARD_PATH and method == "POST":
            return NestedDict({"code": 0, "msg": "ok", "data": {"card_id": "card_42"}})
        return NestedDict({"code": 0, "msg": "ok", "data": {"message_id": "om_1"}})


async def gen(items):
    for it in items:
        yield it


@pytest.fixture
def fake():
    """A recording fake client; tests inspect ``fake.calls`` for the emitted wire log."""
    return LifecycleClient()


def run(client, tokens, *, clock=None, **kw):
    """Drive stream_card with deterministic clock/uuid and the common send target."""
    kw.setdefault("receive_id", "ou_user")
    kw.setdefault("element_id", "md")
    return stream_card(client, tokens, _now=clock or FakeClock(), _new_uuid=uuid_seq(), **kw)


def puts(client):
    return [c for c in client.calls if c["method"] == "PUT"]


class TestStreamCardLifecycle:
    async def test_full_lifecycle_order_and_payloads(self, fake):
        card_id = await run(fake, gen(["Hello", " ", "world"]), receive_id_type="open_id", debounce_s=0.0)
        assert card_id == "card_42"
        methods_paths = [(c["method"], c["path"]) for c in fake.calls]

        # 1) create entity with the card serialized as a JSON string in streaming mode
        assert methods_paths[0] == ("POST", spec.CREATE_CARD_PATH)
        create_body = fake.calls[0]["json"]
        assert create_body[spec.CREATE_CARD_TYPE_FIELD] == spec.CREATE_CARD_TYPE
        inner = _json.loads(create_body[spec.CREATE_CARD_DATA_FIELD])  # data is json.dumps(card)
        assert inner["config"][spec.STREAMING_MODE_KEY] is True

        # 2) send the interactive message referencing the entity by card_id
        assert methods_paths[1] == ("POST", spec.SEND_MESSAGE_PATH)
        send = fake.calls[1]
        assert send["params"]["receive_id_type"] == "open_id"
        assert send["json"]["receive_id"] == "ou_user"
        assert send["json"]["msg_type"] == spec.SEND_MSG_TYPE
        content = _json.loads(send["json"]["content"])
        assert content == {"type": spec.SEND_CARD_CONTENT_TYPE, "data": {"card_id": "card_42"}}

        # 3) last content PUT carries the FULL cumulative text (not a delta)
        put_body = puts(fake)[-1]["json"]
        assert put_body[spec.CONTENT_FIELD] == "Hello world"
        # Pin the literal wire keys the live CardKit API expects, independent of the SUT's own
        # spec.* symbols: a spec rename that drifted from the real contract is caught here.
        assert {"content", "sequence", "uuid"} <= put_body.keys()

        # 4) finalize PATCH settings with streaming_mode False
        assert methods_paths[-1] == ("PATCH", spec.settings_path("card_42"))
        assert "settings" in fake.calls[-1]["json"]  # literal wire-key pin for SETTINGS_FIELD
        settings = _json.loads(fake.calls[-1]["json"][spec.SETTINGS_FIELD])
        assert settings["config"]["streaming_mode"] is False  # literal wire-key pin

    async def test_header_and_template_reach_card(self, fake):
        await run(fake, gen(["hi"]), debounce_s=0.0, header="Title", template="green")
        inner = _json.loads(fake.calls[0]["json"][spec.CREATE_CARD_DATA_FIELD])
        assert inner["header"]["title"]["content"] == "Title"
        assert inner["header"]["template"] == "green"

    async def test_each_write_is_uuid_keyed(self, fake):
        # Every PUT carries a fresh uuid (replay/idempotency key the server dedups on).
        await run(fake, gen(["a", "b", "c"]), debounce_s=0.0)
        uuids = [c["json"][spec.UUID_FIELD] for c in puts(fake)]
        assert uuids and len(set(uuids)) == len(uuids)

    async def test_sequence_strictly_increases(self, fake):
        # Content + settings share one strictly-increasing counter; finalize is last/largest.
        await run(fake, gen(["a", "b", "c"]), debounce_s=0.0)
        seqs = [c["json"][spec.SEQUENCE_FIELD] for c in fake.calls if spec.SEQUENCE_FIELD in (c["json"] or {})]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)
        assert seqs[-1] == max(seqs)

    async def test_no_redundant_content_put(self, fake):
        # The forced final flush must not re-PUT text identical to the last streaming write.
        await run(fake, gen(["Hello", " world"]), debounce_s=0.0)
        contents = [c["json"][spec.CONTENT_FIELD] for c in puts(fake)]
        assert len(contents) == len(set(contents))


class TestStreamCardReply:
    async def test_reply_uses_reply_endpoint(self, fake):
        card_id = await run(fake, gen(["Hi"]), receive_id=None, reply_to_message_id="om_inbound", debounce_s=0.0)
        assert card_id == "card_42"
        # Card is delivered in reply position: recipient implied, no receive_id_type.
        send = fake.calls[1]
        assert (send["method"], send["path"]) == ("POST", "im/v1/messages/om_inbound/reply")
        assert send["path"] == spec.reply_message_path("om_inbound")
        assert send["params"] is None
        assert "receive_id" not in send["json"]
        assert send["json"]["msg_type"] == spec.SEND_MSG_TYPE

    async def test_receive_id_type_inferred_from_prefix(self, fake):
        # No receive_id_type given -> inferred from the oc_ prefix (parity with im.send).
        await run(fake, gen(["Hi"]), receive_id="oc_chat", debounce_s=0.0)
        assert fake.calls[1]["params"]["receive_id_type"] == "chat_id"

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param({"receive_id": None}, id="neither"),
            pytest.param({"reply_to_message_id": "om_1"}, id="both"),
        ],
    )
    async def test_requires_exactly_one_target(self, fake, kwargs):
        # Exactly one of receive_id / reply_to_message_id; neither or both is a usage
        # error raised before any request is issued.
        with pytest.raises(ValueError):
            await run(fake, gen(["x"]), **kwargs)
        assert fake.calls == []


class TestStreamCardFinalizeAlwaysRuns:
    async def test_finalizes_on_empty_stream(self, fake):
        card_id = await run(fake, gen([]), debounce_s=0.0)
        assert card_id == "card_42"
        last = fake.calls[-1]
        assert (last["method"], last["path"]) == ("PATCH", spec.settings_path("card_42"))
        assert puts(fake) == []  # no content PUT for an empty stream

    async def test_finalizes_when_producer_raises(self, fake):
        async def boom():
            yield "partial"
            raise RuntimeError("upstream LLM died")

        with pytest.raises(RuntimeError, match="upstream LLM died"):
            await run(fake, boom(), debounce_s=0.0)
        # finalize MUST still have run (mandatory finalize; streaming auto-closes in 10m).
        last = fake.calls[-1]
        assert (last["method"], last["path"]) == ("PATCH", spec.settings_path("card_42"))

    async def test_final_flush_despite_long_debounce(self, fake):
        # A long debounce + never-advancing clock throttles intermediate tokens away,
        # but the final cumulative text MUST still be flushed once.
        await run(fake, gen(["x", "y", "z"]), debounce_s=1000.0)
        assert puts(fake)[-1]["json"][spec.CONTENT_FIELD] == "xyz"


class TestStreamCardThrottle:
    async def test_debounce_caps_flush_rate(self, fake):
        # 100 tokens across a simulated 1.0s window with the default 0.25s debounce:
        # only writes spaced >= debounce apart get through -> under the 10 ops/s cap.
        clock = FakeClock()

        async def paced():
            for i in range(100):
                clock.advance(0.01)  # 100 * 0.01 = 1.0s total simulated
                yield f"{i} "

        await run(fake, paced(), clock=clock, debounce_s=0.25)
        assert len(puts(fake)) <= MAX_OPS_PER_SEC
        assert puts(fake)[-1]["json"][spec.CONTENT_FIELD].strip().endswith("99")

    async def test_never_drops_final_content(self, fake):
        # Debounce drops intermediate writes, but the last content is the full text.
        clock = FakeClock()

        async def paced():
            for _i in range(20):
                clock.advance(0.05)
                yield "x"

        await run(fake, paced(), clock=clock, debounce_s=0.25)
        assert puts(fake)[-1]["json"][spec.CONTENT_FIELD] == "x" * 20


def _stream_responder(request):
    if request.url.path.endswith("/cardkit/v1/cards") and request.method == "POST":
        return envelope({"card_id": "card_99"})
    return envelope({"message_id": "om_1"})


class TestClientStreamCardDelegation:
    async def test_client_delegates_and_returns_card_id(self, client_factory, recorder):
        client = client_factory(recorder=recorder, responder=_stream_responder)
        try:
            card_id = await client.stream_card(
                gen(["Hi", " there"]), receive_id="ou_user", receive_id_type="open_id", debounce_s=0.0
            )
            assert card_id == "card_99"
            paths = [path for (_method, path, _params, _body) in recorder]
            assert any(p.endswith("/cardkit/v1/cards") for p in paths)  # create
            assert any(p.endswith("/im/v1/messages") for p in paths)  # send
            assert any("/elements/md/content" in p for p in paths)  # stream
            assert any(p.endswith("/cardkit/v1/cards/card_99/settings") for p in paths)  # finalize
        finally:
            await client.aclose()
