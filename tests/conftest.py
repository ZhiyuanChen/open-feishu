from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any, Callable

import httpx
import pytest

from feishu import FeishuClient

# A handler maps an httpx.Request to an httpx.Response (httpx.MockTransport contract).
Handler = Callable[[httpx.Request], httpx.Response]
# A responder maps an httpx.Request to the JSON envelope dict the API would return.
Responder = Callable[[httpx.Request], dict]

# Default credentials for a mock client; values are arbitrary but stable.
APP_ID = "cli_test"
APP_SECRET = "secret"


def envelope(data: Any = None, *, code: int = 0, msg: str = "ok", **extra: Any) -> dict:
    """Build a Feishu API envelope ``{"code", "msg", "data", **extra}``.

    Usage: ``envelope({"chat_id": "oc_1"})`` -> ``{"code": 0, "msg": "ok", "data": {"chat_id": "oc_1"}}``
    """
    out: dict = {"code": code, "msg": msg}
    if data is not None:
        out["data"] = data
    out.update(extra)
    return out


class RequestRecorder(list):
    """Records ``(method, path, params, body)`` per non-token request for boundary assertions.

    A ``list`` subclass, so ``recorder[-1]`` is the last call and ``len(recorder)`` the count.
    Usage: ``method, path, params, body = recorder[-1]``
    """

    def record(self, request: httpx.Request) -> None:
        """Append the decoded ``(method, path, params, json-body)`` tuple for ``request``."""
        self.append(
            (
                request.method,
                request.url.path,
                dict(request.url.params),
                json.loads(request.content or b"{}"),
            )
        )

    @property
    def last(self) -> tuple:
        """The most recently recorded ``(method, path, params, body)`` tuple."""
        return self[-1]


class AsyncRecorder:
    def __init__(self, result: dict[str, str]) -> None:
        self.result = result
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def __call__(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        self.calls.append((args, kwargs))
        return self.result


class GatewayClient:
    def __init__(self) -> None:
        self.im = type(
            "IM",
            (),
            {
                "send": AsyncRecorder({"message_id": "om_card"}),
                "patch": AsyncRecorder({"message_id": "om_card"}),
            },
        )()


@pytest.fixture
def gateway_client() -> GatewayClient:
    return GatewayClient()


def token_handler(request: httpx.Request) -> httpx.Response | None:
    """Short-circuit the tenant_access_token fetch; returns ``None`` for any other request.

    Usage: inside a custom handler, ``return token_handler(request) or my_response``.
    """
    if request.url.path.endswith("tenant_access_token/internal"):
        return httpx.Response(200, json={"code": 0, "msg": "ok", "tenant_access_token": "t", "expire": 7200})
    return None


def make_client(
    handler: Handler | None = None,
    *,
    recorder: RequestRecorder | None = None,
    responder: Responder | None = None,
    app_id: str = APP_ID,
    app_secret: str = APP_SECRET,
) -> FeishuClient:
    """Build a ``FeishuClient`` over ``httpx.MockTransport``, auto-stubbing the token fetch.

    Pass exactly one of ``handler`` (full ``request -> Response``) or ``responder``
    (``request -> envelope dict``, wrapped in a 200 JSON response). If ``recorder`` is
    given, every non-token request is recorded on it. With neither callback, every
    non-token request returns ``envelope({})``.

    Usage: ``client = make_client(recorder=rec, responder=lambda r: envelope({"chat_id": "oc_1"}))``
    """
    if handler is not None and responder is not None:
        raise ValueError("pass either handler or responder, not both")

    def _handler(request: httpx.Request) -> httpx.Response:
        token = token_handler(request)
        if token is not None:
            return token
        if recorder is not None:
            recorder.record(request)
        if handler is not None:
            return handler(request)
        if responder is not None:
            return httpx.Response(200, json=responder(request))
        return httpx.Response(200, json=envelope({}))

    return FeishuClient(app_id, app_secret, transport=httpx.AsyncClient(transport=httpx.MockTransport(_handler)))


def paginated_responder(
    pages: list[list[Any]],
    *,
    data_key: str = "items",
    token_prefix: str = "p",
) -> Responder:
    """Build a responder that serves ``pages`` one per call: ``has_more`` until the last page.

    Each call returns ``envelope({data_key: <page>, "has_more": bool, "page_token": ...})``;
    non-final pages carry ``page_token`` ``"p2"``, ``"p3"`` ... so callers can assert forwarding.
    Usage: ``responder = paginated_responder([[{"chat_id": "oc_1"}], [{"chat_id": "oc_2"}]])``
    """
    state = {"call": 0}

    def responder(request: httpx.Request) -> dict:
        idx = state["call"]
        state["call"] = idx + 1
        page = pages[idx] if idx < len(pages) else []
        has_more = idx < len(pages) - 1
        data: dict = {data_key: page, "has_more": has_more}
        if has_more:
            data["page_token"] = f"{token_prefix}{idx + 2}"
        return envelope(data)

    return responder


@pytest.fixture
def recorder() -> RequestRecorder:
    """A fresh ``RequestRecorder`` for capturing outbound requests.

    Usage: ``def test_x(recorder, client_factory): client = client_factory(recorder=recorder)``
    """
    return RequestRecorder()


@pytest.fixture
def client_factory() -> Callable[..., FeishuClient]:
    """The ``make_client`` factory, for tests that parametrize the handler before building.

    Usage: ``client = client_factory(recorder=recorder, responder=my_responder)``
    """
    return make_client


@pytest.fixture
async def client(recorder: RequestRecorder):
    """A ready ``FeishuClient`` wired to the ``recorder`` fixture; auto-closed after the test.

    Returns ``envelope({})`` for every non-token request unless reconfigured via the factory.
    Usage: ``async def test_x(client, recorder): await client.im.get("om_1")``
    """
    c = make_client(recorder=recorder)
    try:
        yield c
    finally:
        await c.aclose()


def sign_event(encrypt_key: str, timestamp: str, nonce: str, raw_body: bytes) -> str:
    """Compute the ``X-Lark-Signature`` for an event webhook body.

    Usage: ``sig = sign_event(ENCRYPT_KEY, ts, nonce, raw)``
    """
    return hashlib.sha256((timestamp + nonce + encrypt_key).encode("utf-8") + raw_body).hexdigest()


def encrypt_event(encrypt_key: str, plaintext: dict) -> str:
    """AES-CBC encrypt ``plaintext`` into the base64 blob Feishu puts in ``{"encrypt": ...}``.

    Usage: ``body = {"encrypt": encrypt_event(ENCRYPT_KEY, {"type": "url_verification", ...})}``
    """
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad

    data = json.dumps(plaintext).encode("utf-8")
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    iv = b"\x00" * 16
    ct = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(data, AES.block_size))
    return base64.b64encode(iv + ct).decode("ascii")


def signed_event(
    body: dict,
    *,
    encrypt_key: str,
    timestamp: str | None = None,
    nonce: str = "nonce1",
) -> tuple[bytes, dict]:
    """Build ``(raw_body, headers)`` for a signed event webhook POST.

    ``headers`` contains a valid ``X-Lark-Signature`` plus timestamp/nonce and JSON content-type.
    ``timestamp`` defaults to the current epoch second so the signed request stays within the
    receiver's default freshness/replay window (``max_age_seconds=300``); pass an explicit older
    value to exercise stale-timestamp rejection.
    Usage: ``raw, headers = signed_event({"encrypt": ...}, encrypt_key=ENCRYPT_KEY)``
    """
    if timestamp is None:
        timestamp = str(int(time.time()))
    raw = json.dumps(body).encode("utf-8")
    headers = {
        "X-Lark-Signature": sign_event(encrypt_key, timestamp, nonce, raw),
        "X-Lark-Request-Timestamp": timestamp,
        "X-Lark-Request-Nonce": nonce,
        "Content-Type": "application/json",
    }
    return raw, headers
