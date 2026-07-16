from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest
import websockets

from feishu.errors import FeishuError
from feishu.events.dispatcher import EventDispatcher
from feishu.ws._frame import FRAME_TYPE_CONTROL, FRAME_TYPE_DATA, Frame, Header, decode_frame, encode_frame
from feishu.ws.client import WsClient

APP_ID = "cli_app"
APP_SECRET = "secret"

# A canned wss URL carrying the device_id / service_id query params the server appends.
ENDPOINT_URL = "wss://example.feishu.cn/ws?device_id=d1&service_id=7"


def _event_payload(event_type="im.message.receive_v1", event_id="e1"):
    return {"schema": "2.0", "header": {"event_type": event_type, "event_id": event_id}, "event": {}}


def _data_frame(payload: bytes, *, message_id="m1", sum_=1, seq=0, seq_id=42):
    headers = [Header("type", "event"), Header("message_id", message_id)]
    if sum_ > 1:
        headers += [Header("sum", str(sum_)), Header("seq", str(seq))]
    return Frame(seq_id=seq_id, service=7, method=FRAME_TYPE_DATA, headers=headers, payload=payload)


def _event_frame(event_type="im.message.receive_v1", event_id="e1", **kwargs):
    return encode_frame(_data_frame(json.dumps(_event_payload(event_type, event_id)).encode("utf-8"), **kwargs))


def _handshake_handler(**config):
    """Build a handshake handler whose ClientConfig is the supplied overrides on top of sane defaults."""
    client_config = {"ReconnectCount": 5, "ReconnectInterval": 30, "PingInterval": 60}
    client_config.update(config)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"code": 0, "msg": "ok", "data": {"URL": ENDPOINT_URL, "ClientConfig": client_config}}
        )

    return handler


_endpoint_handler = _handshake_handler()
# ReconnectInterval=0 + ReconnectNonce=0 keeps the reconnect test instant; ReconnectCount=1 bounds it.
_fast_endpoint_handler = _handshake_handler(ReconnectCount=1, ReconnectInterval=0, ReconnectNonce=0)


class FakeWebSocket:
    """Minimal websocket double: recv() drains queued frames then raises ConnectionClosed; send() records."""

    def __init__(self, incoming: list[bytes]):
        self._incoming = list(incoming)
        self.sent: list[bytes] = []
        self.closed = False

    async def recv(self) -> bytes:
        if self._incoming:
            return self._incoming.pop(0)
        raise websockets.ConnectionClosed(None, None)

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


class _FakeConn:
    """Async-context-manager double for the injected connect factory."""

    def __init__(self, ws):
        self._ws = ws

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _make_client(handler=None, *, dispatcher=None, **kwargs) -> WsClient:
    http_client = None
    if handler is not None:
        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WsClient(APP_ID, APP_SECRET, dispatcher or EventDispatcher(), http_client=http_client, **kwargs)


async def _serve(ws, *, dispatcher=None, handler=_endpoint_handler):
    """Drive one public handshake -> connect -> serve cycle over ``ws`` (no reconnect).

    ``ws`` delivers its queued frames then raises ConnectionClosed, so ``start()`` runs a
    single serve loop and returns. The public seam exercised in production.
    """
    client = _make_client(handler, dispatcher=dispatcher, connect=lambda url: _FakeConn(ws), auto_reconnect=False)
    await client.start()


class TestInit:
    @pytest.mark.parametrize(("app_id", "app_secret"), [("", APP_SECRET), (APP_ID, "")])
    def test_blank_credentials_raise(self, app_id, app_secret):
        with pytest.raises(ValueError):
            WsClient(app_id, app_secret, EventDispatcher())


class TestStart:
    async def test_handshake_url_reaches_connect(self):
        # The wss URL returned by the handshake must reach the connect factory verbatim.
        seen = {}

        def connect(url):
            seen["url"] = url
            return _FakeConn(FakeWebSocket([]))  # empty -> recv raises immediately, serve exits

        client = _make_client(_endpoint_handler, connect=connect, auto_reconnect=False)
        await client.start()
        assert seen["url"] == ENDPOINT_URL

    @pytest.mark.parametrize("auto_reconnect", [True, False])
    async def test_handshake_business_error_raises(self, auto_reconnect):
        # A non-zero handshake code is a config error: it propagates immediately, with no retry.
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(200, json={"code": 1, "msg": "denied"})

        client = _make_client(handler, connect=lambda url: _FakeConn(FakeWebSocket([])), auto_reconnect=auto_reconnect)
        with pytest.raises(FeishuError) as exc:
            await client.start()
        assert exc.value.code == 1
        assert calls["n"] == 1

    @pytest.mark.parametrize(("auto_reconnect", "expected"), [(True, 2), (False, 1)])
    async def test_reconnect_count_honoured(self, auto_reconnect, expected):
        # auto_reconnect off -> one connect; on -> initial + ReconnectCount(=1) reconnects, then exhausted.
        calls = {"n": 0}

        def connect(url):
            calls["n"] += 1
            return _FakeConn(FakeWebSocket([]))  # empty -> recv raises ConnectionClosed immediately

        client = _make_client(_fast_endpoint_handler, connect=connect, auto_reconnect=auto_reconnect)
        await client.start()
        assert calls["n"] == expected

    async def test_transient_5xx_is_retried(self):
        async def no_sleep(_delay: float) -> None:
            await asyncio.sleep(0)

        calls = {"handshake": 0, "connect": 0}
        success = _handshake_handler(ReconnectCount=0, ReconnectInterval=0, ReconnectNonce=0)

        def handler(request):
            calls["handshake"] += 1
            if calls["handshake"] == 1:
                return httpx.Response(503, text="overloaded")  # transient 5xx
            return success(request)

        def connect(url):
            calls["connect"] += 1
            return _FakeConn(FakeWebSocket([]))

        client = _make_client(handler, connect=connect, auto_reconnect=True, sleep=no_sleep)
        await client.start()
        assert calls["handshake"] == 2  # first 503 retried, second succeeded
        assert calls["connect"] == 1  # connected only after the successful handshake

    async def test_5xx_raises_when_reconnect_disabled(self):
        client = _make_client(
            lambda r: httpx.Response(503, text="overloaded"),
            connect=lambda url: _FakeConn(FakeWebSocket([])),
            auto_reconnect=False,
        )
        with pytest.raises(FeishuError):  # no retry when reconnect disabled
            await client.start()

    async def test_connect_error_retried(self):
        # A transient failure establishing the socket after a good handshake is retried on the
        # reconnect budget, not propagated out of start().
        calls = {"n": 0}

        def connect(url):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("connection refused")  # transient connect failure
            return _FakeConn(FakeWebSocket([]))

        client = _make_client(_fast_endpoint_handler, connect=connect, auto_reconnect=True)
        await client.start()  # must NOT raise
        assert calls["n"] == 2  # first connect failed (retried), second served then budget exhausted

    async def test_connect_error_raises_when_reconnect_disabled(self):
        def connect(url):
            raise OSError("connection refused")

        client = _make_client(_endpoint_handler, connect=connect, auto_reconnect=False)
        with pytest.raises(OSError):  # not retried when reconnect disabled (mirrors handshake)
            await client.start()


class TestFragmentBuffer:
    async def test_incomplete_fragments_are_bounded(self):
        # Memory guard: incomplete fragments whose later parts never arrive must not accumulate
        # without bound. Drive the public start()/serve loop; only the assertion inspects the buffer.
        # 5 distinct messages, each delivering only the FIRST of 2 fragments (never completes).
        frames = [encode_frame(_data_frame(b'{"x":1}', message_id=f"m{i}", sum_=2, seq=0)) for i in range(5)]
        ws = FakeWebSocket(frames)
        client = _make_client(
            _endpoint_handler,
            connect=lambda url: _FakeConn(ws),
            auto_reconnect=False,
            max_partial_messages=3,
        )
        await client.start()
        assert len(client._fragments) == 3  # oldest two incomplete messages evicted


class TestServe:
    async def test_event_dispatched_and_acked(self):
        # One data event -> handler fires, and exactly one ACK echoes the frame metadata back on the wire.
        dispatcher = EventDispatcher()
        seen = []

        @dispatcher.on("im.message.receive_v1")
        async def handle(event):
            seen.append(event.event_id)

        ws = FakeWebSocket([_event_frame()])
        await _serve(ws, dispatcher=dispatcher)

        assert seen == ["e1"]
        assert len(ws.sent) == 1
        ack = decode_frame(ws.sent[0])
        assert ack.method == FRAME_TYPE_DATA
        assert ack.seq_id == 42  # echoes the inbound frame's seq_id
        assert ack.header("message_id") == "m1"
        response = json.loads(ack.payload.decode("utf-8"))
        assert response["code"] == 200
        assert "data" not in response  # handler returned None

    async def test_card_result_is_base64_in_ack(self):
        dispatcher = EventDispatcher()
        result = {"toast": {"type": "success", "content": "ok"}}

        @dispatcher.on("card.action.trigger")
        async def on_card(event):
            return result

        ws = FakeWebSocket([_event_frame("card.action.trigger", "c1")])
        await _serve(ws, dispatcher=dispatcher)

        assert len(ws.sent) == 1
        response = json.loads(decode_frame(ws.sent[0]).payload.decode("utf-8"))
        assert json.loads(base64.b64decode(response["data"]).decode("utf-8")) == result

    async def test_slow_card_action_gets_processing_ack_and_keeps_running(self):
        dispatcher = EventDispatcher()
        started = asyncio.Event()
        release = asyncio.Event()
        finished = []

        @dispatcher.on("card.action.trigger")
        async def on_card(event):
            started.set()
            await release.wait()
            finished.append(event.event_id)
            return {"toast": {"type": "success", "content": "done"}}

        ws = FakeWebSocket([_event_frame("card.action.trigger", "c_slow")])
        client = _make_client(
            _endpoint_handler,
            dispatcher=dispatcher,
            connect=lambda url: _FakeConn(ws),
            auto_reconnect=False,
            card_ack_timeout=0.01,
        )
        task = asyncio.create_task(client.start())

        await asyncio.wait_for(started.wait(), timeout=1)
        for _ in range(20):
            if ws.sent:
                break
            await asyncio.sleep(0.01)

        assert len(ws.sent) == 1
        response = json.loads(decode_frame(ws.sent[0]).payload.decode("utf-8"))
        ack = json.loads(base64.b64decode(response["data"]).decode("utf-8"))
        assert ack == {"toast": {"type": "info", "content": "处理中…"}}
        assert finished == []

        release.set()
        await asyncio.wait_for(task, timeout=1)
        assert finished == ["c_slow"]
        assert len(ws.sent) == 1

    async def test_zero_card_ack_timeout_acknowledges_before_dispatch_finishes(self):
        dispatcher = EventDispatcher()
        started = asyncio.Event()
        release = asyncio.Event()
        finished = []
        order = []

        @dispatcher.on("card.action.trigger")
        async def on_card(event):
            order.append("handler_start")
            started.set()
            await release.wait()
            finished.append(event.event_id)
            return {"toast": {"type": "success", "content": "done"}}

        class OrderedWebSocket(FakeWebSocket):
            async def send(self, data: bytes) -> None:
                order.append("ack_send")
                await super().send(data)

        ws = OrderedWebSocket([_event_frame("card.action.trigger", "c_zero")])
        client = _make_client(
            _endpoint_handler,
            dispatcher=dispatcher,
            connect=lambda url: _FakeConn(ws),
            auto_reconnect=False,
            card_ack_timeout=0,
        )
        task = asyncio.create_task(client.start())

        for _ in range(20):
            if ws.sent:
                break
            await asyncio.sleep(0.01)

        assert len(ws.sent) == 1
        response = json.loads(decode_frame(ws.sent[0]).payload.decode("utf-8"))
        ack = json.loads(base64.b64decode(response["data"]).decode("utf-8"))
        assert ack == {"toast": {"type": "info", "content": "处理中…"}}
        assert order[0] == "ack_send"
        assert finished == []

        await asyncio.wait_for(started.wait(), timeout=1)
        release.set()
        await asyncio.wait_for(task, timeout=1)
        for _ in range(20):
            if finished:
                break
            await asyncio.sleep(0.01)
        assert finished == ["c_zero"]

    async def test_zero_card_ack_timeout_drains_background_dispatch_after_connection_closes(self):
        dispatcher = EventDispatcher()
        started = asyncio.Event()
        release = asyncio.Event()
        finished = []

        @dispatcher.on("card.action.trigger")
        async def on_card(event):
            started.set()
            await release.wait()
            finished.append(event.event_id)
            return {"toast": {"type": "success", "content": "done"}}

        ws = FakeWebSocket([_event_frame("card.action.trigger", "c_close")])
        client = _make_client(
            _endpoint_handler,
            dispatcher=dispatcher,
            connect=lambda url: _FakeConn(ws),
            auto_reconnect=False,
            card_ack_timeout=0,
        )
        task = asyncio.create_task(client.start())

        for _ in range(20):
            if ws.sent:
                break
            await asyncio.sleep(0.01)

        assert len(ws.sent) == 1
        await asyncio.wait_for(started.wait(), timeout=1)
        await asyncio.sleep(0)
        assert not task.done()

        release.set()
        await asyncio.wait_for(task, timeout=1)
        assert finished == ["c_close"]

    async def test_control_frame_is_not_acked(self):
        dispatcher = EventDispatcher()
        seen = []

        @dispatcher.on("im.message.receive_v1")
        async def handle(event):
            seen.append(event.event_id)

        pong = Frame(method=FRAME_TYPE_CONTROL, headers=[Header("type", "pong")])
        ws = FakeWebSocket([encode_frame(pong), _event_frame()])
        await _serve(ws, dispatcher=dispatcher)

        # Only the data event is dispatched and acked; the control pong yields no ACK.
        assert seen == ["e1"]
        assert len(ws.sent) == 1

    async def test_fragments_reassemble_into_one_event(self):
        dispatcher = EventDispatcher()
        seen = []

        @dispatcher.on("im.message.receive_v1")
        async def handle(event):
            seen.append(event.event_id)

        full = json.dumps(_event_payload(event_id="frag")).encode("utf-8")
        half = len(full) // 2
        first = _data_frame(full[:half], message_id="big", sum_=2, seq=0)
        second = _data_frame(full[half:], message_id="big", sum_=2, seq=1)
        ws = FakeWebSocket([encode_frame(first), encode_frame(second)])
        await _serve(ws, dispatcher=dispatcher)

        # Two fragments reassemble into one event -> the handler fires once and one ACK is sent.
        assert seen == ["frag"]
        assert len(ws.sent) == 1

    async def test_slow_handler_does_not_stall_intake(self):
        # The slow handler blocks on `gate`, which only the fast handler releases.
        # With sequential dispatch the recv loop would await the slow handler forever
        # (the fast frame never gets read) -> deadlock. Concurrent dispatch lets the
        # fast frame be handled while the slow one is still awaiting, so both finish.
        dispatcher = EventDispatcher()
        gate = asyncio.Event()
        order = []

        @dispatcher.on("im.message.receive_v1")
        async def handle(event):
            if event.event_id == "slow":
                await gate.wait()
                order.append("slow")
            else:
                order.append("fast")
                gate.set()

        slow = _event_frame(event_id="slow", message_id="m1", seq_id=1)
        fast = _event_frame(event_id="fast", message_id="m2", seq_id=2)
        ws = FakeWebSocket([slow, fast])
        await _serve(ws, dispatcher=dispatcher)

        assert order == ["fast", "slow"]
        assert len(ws.sent) == 2

    async def test_ping_carries_service_id_from_url(self):
        # Observable equivalent of "service_id parsed from the wss URL == 7": the client
        # tags every outgoing ping control frame with that service id. PingInterval=0 makes
        # the ping loop emit once; the websocket closes only after that send is recorded.
        sent_one = asyncio.Event()

        class PingObservingWebSocket:
            def __init__(self):
                self.sent = []

            async def recv(self):
                await sent_one.wait()
                raise websockets.ConnectionClosed(None, None)

            async def send(self, data):
                self.sent.append(data)
                sent_one.set()

            async def close(self):
                pass

        ws = PingObservingWebSocket()
        client = _make_client(
            _handshake_handler(PingInterval=0), connect=lambda url: _FakeConn(ws), auto_reconnect=False
        )
        await client.start()

        assert ws.sent, "expected at least one outgoing ping frame"
        ping = decode_frame(ws.sent[0])
        assert ping.method == FRAME_TYPE_CONTROL
        assert ping.header("type") == "ping"
        assert ping.service == 7

    async def test_pong_updates_ping_cadence(self):
        # The pong refreshes the ping interval; observe the delay the ping loop schedules
        # for its NEXT heartbeat (the public-observable cadence). The handshake sets 60s initially.
        recorded: list[float] = []
        saw_new_interval = asyncio.Event()

        async def recording_sleep(delay: float) -> None:
            recorded.append(delay)
            if delay == 90.0:
                saw_new_interval.set()
            await asyncio.sleep(0)  # yield without waiting so the ping loop keeps iterating

        pong = Frame(
            method=FRAME_TYPE_CONTROL,
            headers=[Header("type", "pong")],
            payload=json.dumps({"PingInterval": 90}).encode("utf-8"),
        )

        class _PongThenWaitWebSocket:
            """Delivers the config-bearing pong, then keeps the connection open until the
            ping loop has scheduled a sleep at the new 90s interval, then closes."""

            def __init__(self) -> None:
                self.sent: list[bytes] = []
                self._delivered = False

            async def recv(self) -> bytes:
                if not self._delivered:
                    self._delivered = True
                    return encode_frame(pong)
                await saw_new_interval.wait()
                raise websockets.ConnectionClosed(None, None)

            async def send(self, data: bytes) -> None:
                self.sent.append(data)

            async def close(self) -> None:
                pass

        ws = _PongThenWaitWebSocket()
        client = _make_client(
            _endpoint_handler,
            connect=lambda url: _FakeConn(ws),
            auto_reconnect=False,
            sleep=recording_sleep,
        )
        await client.start()

        # The heartbeat cadence after the pong is the observable 90s the loop scheduled, and it sticks.
        assert 90.0 in recorded
        assert recorded[-1] == 90.0
