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

import hmac
import json
import time
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..errors import FeishuCryptoError
from ..signature import SignatureVerifier
from .crypto import decrypt
from .dispatcher import EventDispatcher
from .envelope import Event
from .idempotency import InMemorySeenStore, SeenStore, claim

# Sentinel distinguishing "argument not supplied" (-> default InMemorySeenStore,
# so replay/idempotency protection is on out of the box) from an explicit
# ``seen_store=None`` (-> caller deliberately disables deduplication).
_DEFAULT_SEEN_STORE: Any = object()


def _resolve_seen_store(seen_store: Any) -> SeenStore | None:
    r"""将 ``seen_store`` 参数归一化为实际使用的存储实例。

    未显式传参（命中哨兵）时返回新的
    [InMemorySeenStore][feishu.events.idempotency.InMemorySeenStore]，从而默认开启去重/防重放；
    显式传入 ``None`` 表示主动关闭去重；其余情况原样返回调用方提供的存储。
    """
    if seen_store is _DEFAULT_SEEN_STORE:
        return InMemorySeenStore()
    return seen_store


def _build_verifier(
    encrypt_key: str | None,
    max_age_seconds: float | None,
    now: Callable[[], float],
) -> SignatureVerifier | None:
    r"""当配置了 ``encrypt_key`` 时构造带重放时间窗的签名校验器，否则返回 ``None``。"""
    if encrypt_key is None:
        return None
    return SignatureVerifier(encrypt_key, max_age_seconds=max_age_seconds, now=now)


async def _read_payload(
    request: Request,
    raw: bytes,
    encrypt_key: str | None,
    verifier: SignatureVerifier | None,
) -> tuple[dict, bool] | Response:
    """返回 ``(parsed_payload, sig_verified)``；失败时返回响应对象。

    只有在设置了 `verifier`（即配置了 `encrypt_key`）、请求携带 `X-Lark-Signature`、
    时间戳仍在新鲜度窗口内且原始请求体 MAC 匹配时，`sig_verified` 才为 `True`。
    校验委托给 [feishu.signature.SignatureVerifier][]，因此重放保护时间窗会生效；
    已超过 `max_age_seconds` 的已签名请求体会被拒绝。
    """
    sig_verified = False
    signature = request.headers.get("X-Lark-Signature")
    if verifier is not None and signature is not None:
        if not verifier.is_valid_request(raw, request.headers):
            return JSONResponse({"msg": "signature mismatch"}, status_code=401)
        sig_verified = True

    try:
        outer = json.loads(raw) if raw else {}
    except ValueError:
        return JSONResponse({"msg": "invalid json"}, status_code=400)
    if not isinstance(outer, dict):
        return JSONResponse({"msg": "invalid event payload"}, status_code=400)

    if "encrypt" in outer:
        if encrypt_key is None:
            return JSONResponse({"msg": "encrypted body but no encrypt_key"}, status_code=400)
        if verifier is not None and not sig_verified:
            return JSONResponse({"msg": "signature required"}, status_code=401)
        ciphertext = outer.get("encrypt")
        if not isinstance(ciphertext, str) or not ciphertext:
            return JSONResponse({"msg": "invalid encrypted body"}, status_code=400)
        try:
            plain = decrypt(encrypt_key, ciphertext)
            payload = json.loads(plain)
        except (FeishuCryptoError, ValueError, TypeError):
            return JSONResponse({"msg": "invalid encrypted body"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"msg": "invalid event payload"}, status_code=400)
        return payload, sig_verified
    return outer, sig_verified


def _verify_event_token(event: Event, verification_token: str | None) -> Response | None:
    r"""校验事件内层 token —— 不匹配时返回 401 响应，通过（或未配置 ``verification_token``）时返回 ``None``。"""
    if verification_token is None:
        return None
    if not hmac.compare_digest(str(event.token or ""), verification_token):
        return JSONResponse({"msg": "token mismatch"}, status_code=401)
    return None


def _verify_event_id(event: Event) -> Response | None:
    r"""校验事件携带 ``event_id`` —— 缺失时返回 400 响应，存在时返回 ``None``（去重由 ``claim`` 另行完成）。"""
    if not event.event_id:
        return JSONResponse({"msg": "missing event_id"}, status_code=400)
    return None


def create_event_route(
    dispatcher: EventDispatcher,
    *,
    path: str = "/feishu/event",
    encrypt_key: str | None = None,
    verification_token: str | None = None,
    seen_store: Any = _DEFAULT_SEEN_STORE,
    max_age_seconds: float | None = 300,
    now: Callable[[], float] = time.time,
) -> Route:
    r"""
    创建处理飞书事件推送的 Starlette POST 路由。

    该端点的处理流程：

    1. 先读取原始请求体字节（签名校验与 AES 解密都依赖未经改动的原始字节）。
    2. 当配置了 `encrypt_key` 且请求头包含 `X-Lark-Signature` 时，
       经 [SignatureVerifier][feishu.signature.SignatureVerifier] 校验签名
       **并校验时间戳新鲜度**（重放时间窗），记录校验结果。
    3. 若请求体被 `encrypt` 包裹，则必须先通过签名校验再解密，避免解密错误成为鉴权前 oracle。
    4. 处理未加密的 `url_verification` 握手：握手通过内层 `verification_token` 鉴权。
    5. 其余正常事件：当配置了 `encrypt_key` 时，签名必须存在、时间戳在
       `max_age_seconds` 时间窗内且校验通过；缺失签名或时间戳过期将返回 401
       （防止 Webhook 注入与重放攻击绕过）。
    6. 通过 `seen_store` 去重（默认即开启），其余事件以后台任务（BackgroundTask）
       异步分发，端点立即返回 `200 {}`。

    Args:
        dispatcher: 事件分发器。
        path: 路由路径，默认 `/feishu/event`。
        encrypt_key: 应用配置的 Encrypt Key；设置后启用解密与签名强校验。
        verification_token: 握手校验 Token；设置后会校验 `url_verification` 的内层 token。
        seen_store: 事件去重存储；缺省时使用新建的
            [InMemorySeenStore][feishu.events.idempotency.InMemorySeenStore]，
            从而开箱即用地提供去重/防重放保护；显式传入 `None` 可关闭去重，
            也可传入自定义实现（如基于 Redis 的共享存储）。
        max_age_seconds: 签名请求允许的最大时延（秒），默认 `300`；用于拒绝过期的
            重放请求。设为 `None` 可关闭新鲜度校验（但绝不会跳过 MAC 校验）。
        now: 返回当前 epoch 秒的可调用对象，默认 [time.time][]；可注入以编写确定性测试。

    Returns:
        可挂载到 Starlette 应用的 `starlette.routing.Route`。

    飞书文档:
        [接收事件](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case)

    Examples:
        >>> from starlette.applications import Starlette
        >>> dispatcher = EventDispatcher()
        >>> route = create_event_route(dispatcher, encrypt_key="ek_secret")  # doctest: +SKIP
        >>> app = Starlette(routes=[route])  # doctest: +SKIP
    """
    store = _resolve_seen_store(seen_store)
    verifier = _build_verifier(encrypt_key, max_age_seconds, now)

    async def endpoint(request: Request) -> Response:
        raw = await request.body()
        result = await _read_payload(request, raw, encrypt_key, verifier)
        if isinstance(result, Response):
            return result
        payload, sig_verified = result

        if payload.get("type") == "url_verification":
            # Handshake: authenticated by inner token, not by MAC signature.
            if verification_token is not None and not hmac.compare_digest(
                str(payload.get("token", "")), verification_token
            ):
                return JSONResponse({"msg": "token mismatch"}, status_code=401)
            return JSONResponse({"challenge": payload.get("challenge")})

        # Normal events require MAC authentication when encrypt_key is configured.
        if encrypt_key is not None and not sig_verified:
            return JSONResponse({"msg": "signature required"}, status_code=401)

        event = Event.from_payload(payload)
        token_error = _verify_event_token(event, verification_token)
        if token_error is not None:
            return token_error
        id_error = _verify_event_id(event)
        if id_error is not None:
            return id_error
        if store is not None and not await claim(store, event.event_id):
            return JSONResponse({})

        return JSONResponse({}, background=BackgroundTask(dispatcher.dispatch, event))

    return Route(path, endpoint, methods=["POST"])


def create_card_route(
    dispatcher: EventDispatcher,
    *,
    path: str = "/feishu/card",
    encrypt_key: str | None = None,
    verification_token: str | None = None,
    seen_store: Any = _DEFAULT_SEEN_STORE,
    max_age_seconds: float | None = 300,
    now: Callable[[], float] = time.time,
) -> Route:
    r"""
    创建处理飞书卡片交互回调的 Starlette POST 路由。

    与 [create_event_route][feishu.events.receiver.create_event_route] 不同，本路由会
    **同步**等待分发器执行（不使用后台任务），并将处理函数返回的 `{toast, card}` 字典
    作为同步 JSON 响应返回，以满足飞书对卡片交互约 3 秒的响应时限。当处理函数返回 `None` 时，
    响应为 `200 {}`。

    安全模型与 [create_event_route][feishu.events.receiver.create_event_route] 一致：

    * 未加密的 `url_verification` 握手通过内层 `verification_token` 鉴权。
    * 加密请求与其余事件在配置了 `encrypt_key` 时，必须携带并经
      [SignatureVerifier][feishu.signature.SignatureVerifier] 通过 `X-Lark-Signature`
      校验，且时间戳须在 `max_age_seconds` 时间窗内（防重放）；缺失、过期或非法签名将返回
      401，且处理函数不会被调用。

    由于飞书在超时时会重试卡片回调，命中 `seen_store` 的重复事件会直接返回 `{}`，避免重复触发副作用。

    Args:
        dispatcher: 事件分发器。
        path: 路由路径，默认 `/feishu/card`。
        encrypt_key: 应用配置的 Encrypt Key；设置后启用解密与签名强校验。
        verification_token: 握手校验 Token；设置后会校验 `url_verification` 的内层 token。
        seen_store: 事件去重存储；缺省时使用新建的
            [InMemorySeenStore][feishu.events.idempotency.InMemorySeenStore]，
            从而开箱即用地提供去重/防重放保护；显式传入 `None` 可关闭去重，
            也可传入自定义实现。
        max_age_seconds: 签名请求允许的最大时延（秒），默认 `300`；用于拒绝过期的
            重放请求。设为 `None` 可关闭新鲜度校验（但绝不会跳过 MAC 校验）。
        now: 返回当前 epoch 秒的可调用对象，默认 [time.time][]；可注入以编写确定性测试。

    Returns:
        可挂载到 Starlette 应用的 `starlette.routing.Route`。

    飞书文档:
        [卡片回传交互](https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-callback-communication)

    Examples:
        >>> dispatcher = EventDispatcher()
        >>> route = create_card_route(dispatcher, encrypt_key="ek_secret")  # doctest: +SKIP
    """
    store = _resolve_seen_store(seen_store)
    verifier = _build_verifier(encrypt_key, max_age_seconds, now)

    async def endpoint(request: Request) -> Response:
        raw = await request.body()
        result = await _read_payload(request, raw, encrypt_key, verifier)
        if isinstance(result, Response):
            return result
        payload, sig_verified = result

        if payload.get("type") == "url_verification":
            # Handshake: authenticated by inner token, not by MAC signature.
            if verification_token is not None and not hmac.compare_digest(
                str(payload.get("token", "")), verification_token
            ):
                return JSONResponse({"msg": "token mismatch"}, status_code=401)
            return JSONResponse({"challenge": payload.get("challenge")})

        # Real card events require MAC authentication when encrypt_key is configured.
        if encrypt_key is not None and not sig_verified:
            return JSONResponse({"msg": "signature required"}, status_code=401)

        event = Event.from_payload(payload)
        token_error = _verify_event_token(event, verification_token)
        if token_error is not None:
            return token_error
        id_error = _verify_event_id(event)
        if id_error is not None:
            return id_error
        # Feishu retries card-action callbacks on timeout; returning {} prevents re-running side effects.
        if store is not None and not await claim(store, event.event_id):
            return JSONResponse({})

        handler_result = await dispatcher.dispatch(event)
        return JSONResponse(handler_result if handler_result is not None else {})

    return Route(path, endpoint, methods=["POST"])


def create_event_app(
    dispatcher: EventDispatcher,
    *,
    event_path: str = "/feishu/event",
    card_path: str | None = "/feishu/card",
    encrypt_key: str | None = None,
    verification_token: str | None = None,
    seen_store: Any = _DEFAULT_SEEN_STORE,
    card_seen_store: Any = _DEFAULT_SEEN_STORE,
    max_age_seconds: float | None = 300,
    now: Callable[[], float] = time.time,
) -> Starlette:
    r"""
    返回一个可独立运行、处理飞书 Webhook 推送的 Starlette 应用。

    始终在 `event_path` 挂载事件路由；当 `card_path` 不为 `None` 时，额外在该路径挂载卡片回调路由。
    全部安全与路由逻辑分别委托给 [create_event_route][feishu.events.receiver.create_event_route]
    与 [create_card_route][feishu.events.receiver.create_card_route]。默认启用去重；仅在传入
    `encrypt_key` 时启用签名校验与新鲜度（防重放）时间窗。

    Args:
        dispatcher: 事件分发器。
        event_path: 事件路由路径，默认 `/feishu/event`。
        card_path: 卡片回调路由路径，默认 `/feishu/card`；为 `None` 时不挂载卡片路由。
        encrypt_key: 应用配置的 Encrypt Key；设置后启用解密与签名强校验。
        verification_token: 握手校验 Token；设置后会校验 `url_verification` 的内层 token。
        seen_store: 事件路由的去重存储；缺省时各路由自建
            [InMemorySeenStore][feishu.events.idempotency.InMemorySeenStore]，
            显式传入 `None` 关闭去重，也可传入自定义实现。
        card_seen_store: 卡片路由的去重存储；语义同 `seen_store`。
        max_age_seconds: 签名请求允许的最大时延（秒），默认 `300`；设为 `None` 关闭新鲜度校验。
        now: 返回当前 epoch 秒的可调用对象，默认 [time.time][]；可注入以编写确定性测试。

    Returns:
        已挂载相应路由的 `starlette.applications.Starlette` 应用。

    飞书文档:
        [接收事件](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case)

    Examples:
        >>> dispatcher = EventDispatcher()
        >>> app = create_event_app(dispatcher, encrypt_key="ek_secret")  # doctest: +SKIP
    """
    routes = [
        create_event_route(
            dispatcher,
            path=event_path,
            encrypt_key=encrypt_key,
            verification_token=verification_token,
            seen_store=seen_store,
            max_age_seconds=max_age_seconds,
            now=now,
        )
    ]
    if card_path is not None:
        routes.append(
            create_card_route(
                dispatcher,
                path=card_path,
                encrypt_key=encrypt_key,
                verification_token=verification_token,
                seen_store=card_seen_store,
                max_age_seconds=max_age_seconds,
                now=now,
            )
        )
    return Starlette(routes=routes)
