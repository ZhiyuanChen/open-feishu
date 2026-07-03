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

import asyncio
import base64
import json
import logging
import random
from collections.abc import Awaitable
from contextlib import AbstractAsyncContextManager, suppress
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import httpx

from ..consts import resolve_base_url
from ..errors import FeishuError, FeishuServerError
from ..events.dispatcher import EventDispatcher
from ..events.envelope import Event
from ._frame import FRAME_TYPE_CONTROL, FRAME_TYPE_DATA, Frame, Header, decode_frame, encode_frame
from .model import ClientConfig, client_config_from_dict

# The handshake endpoint lives at the site root, not under the Open API prefix (/open-apis).
_ENDPOINT_PATH = "/callback/ws/endpoint"

# Card callbacks must encode their result (toast / updated card) in the ACK frame, so they run synchronously:
# dispatch first, then ACK. Other events, especially `im.message.receive_v1`, ACK immediately and dispatch in
# the background; otherwise slow handlers trigger Feishu's at-least-once redelivery and duplicate replies.
_SYNC_ACK_EVENT_TYPES = frozenset({"card.action.trigger"})
_CARD_ACK_TIMEOUT_SECONDS = 1.5
_CARD_ACK_TIMEOUT_RESULT = {"toast": {"type": "info", "content": "处理中…"}}

# Maximum number of incomplete messages held in the fragment reassembly buffer. Drop the oldest partial
# message when exceeded so a missing upstream fragment cannot grow memory without bound.
_MAX_PARTIAL_MESSAGES = 1024

# Injectable websocket connector type: given a wss URL, return an async context manager whose value exposes
# recv() and send() coroutines.
Connect = Callable[[str], AbstractAsyncContextManager[Any]]


def _default_connect(url: str) -> AbstractAsyncContextManager[Any]:
    r"""默认 websocket 连接器：懒加载 `websockets`，缺失时给出明确的安装提示。"""
    try:
        import websockets
    except ImportError as exc:  # pragma: no cover - optional dependency missing branch
        raise ImportError(
            "WsClient 需要可选依赖 websockets；请执行 `pip install open-feishu[ws]` 后再使用长连接。"
        ) from exc
    # max_size=None disables the single-frame size cap; event payloads can be large after the handshake.
    return websockets.connect(url, max_size=None)


class WsClient:
    r"""
    飞书长连接（WebSocket）事件客户端。

    作为 Webhook 接收器（[create_event_app][feishu.events.receiver.create_event_app] 等）的替代方案：
    无需公网回调地址，应用主动与飞书建立一条持久 WebSocket 连接，事件经该连接推送，
    处理结果通过 ACK 帧回传，对标 Slack 的 Socket Mode。

    连接生命周期由 [start][feishu.ws.client.WsClient.start] 驱动：握手 -> 建连 -> 收发循环，
    断线后按 [ClientConfig][feishu.ws.model.ClientConfig] 自动重连。事件解析与分发完全复用
    [EventDispatcher][feishu.events.dispatcher.EventDispatcher]，因此 Webhook 与长连接两种接入
    方式可共用同一套处理函数；分发结果会被编码进 ACK，供卡片回调等场景返回 `{toast, card}`。

    为便于测试，HTTP 客户端与 websocket 连接器均可注入，默认实现仅在真正需要时才创建/导入。

    Args:
        app_id: 应用 App ID。
        app_secret: 应用 App Secret。
        dispatcher: 事件分发器。
        region: 区域标识，`feishu` 或 `lark`，默认 `feishu`。
        base_url: 自定义基础地址，传入时优先于 `region`。
        auto_reconnect: 断线后是否自动重连，默认 `True`。
        logger: 自定义日志器，缺省使用名为 `feishu` 的日志器。
        http_client: 注入的 `httpx.AsyncClient`，用于握手；为 `None` 时每次握手临时创建并关闭。
        connect: 注入的 websocket 连接器；为 `None` 时懒加载 `websockets`。
        sleep: 注入的休眠函数，缺省使用 [asyncio.sleep][]。
        max_partial_messages: 分片重组缓冲区可保留的未完成消息数量。

    Raises:
        ValueError: 当 `app_id` 或 `app_secret` 为空时抛出。

    飞书文档:
        [事件概述](https://open.feishu.cn/document/server-docs/event-subscription-guide/overview)

    Examples:
        >>> from feishu.events.dispatcher import EventDispatcher
        >>> dispatcher = EventDispatcher()
        >>> @dispatcher.on("im.message.receive_v1")
        ... async def on_message(event):
        ...     print(event.event_id)
        ...
        >>> ws = WsClient("cli_app", "secret", dispatcher)
        >>> import asyncio
        >>> asyncio.run(ws.start())  # doctest: +SKIP
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        dispatcher: EventDispatcher,
        *,
        region: str = "feishu",
        base_url: str | None = None,
        auto_reconnect: bool = True,
        logger: logging.Logger | None = None,
        http_client: httpx.AsyncClient | None = None,
        connect: Connect | None = None,
        sleep: Callable[[float], Awaitable[Any]] | None = None,
        max_partial_messages: int = _MAX_PARTIAL_MESSAGES,
        card_ack_timeout: float | None = _CARD_ACK_TIMEOUT_SECONDS,
    ) -> None:
        if not app_id:
            raise ValueError("app_id must not be empty")
        if not app_secret:
            raise ValueError("app_secret must not be empty")
        if max_partial_messages < 1:
            raise ValueError("max_partial_messages must be positive")
        if card_ack_timeout is not None and card_ack_timeout <= 0:
            raise ValueError("card_ack_timeout must be positive or None")
        self._app_id = app_id
        self._app_secret = app_secret
        self._dispatcher = dispatcher
        self._base_url = resolve_base_url(region, base_url)
        self._auto_reconnect = auto_reconnect
        self.logger = logger or logging.getLogger("feishu")
        self._http_client = http_client
        self._connect: Connect = connect or _default_connect
        self._sleep = sleep or asyncio.sleep
        self._max_partial_messages = max_partial_messages
        self._card_ack_timeout = card_ack_timeout

        # Populated after the handshake: service frame field comes from the wss URL's service_id query param.
        self._service_id = 0
        self._ping_interval = ClientConfig().ping_interval
        # Fragment reassembly buffer: message_id -> {seq: chunk}. This first version has no TTL eviction;
        # if the upstream misses a fragment, the entry stays until a later timeout cleanup is added.
        self._fragments: dict[str, dict[int, bytes]] = {}
        # Runtime control.
        self._stopped = False
        self._websocket: Any = None

    async def _handshake(self) -> tuple[str, ClientConfig]:
        r"""
        执行握手，换取 wss 连接地址与客户端配置。

        向 `{base_url}/callback/ws/endpoint` POST 应用凭据（注意该端点不在 Open API 前缀下，
        因此使用裸 httpx 而非 SDK 传输层）。成功后从返回的 wss URL 中解析出 `service_id`
        并记录为后续出站帧的 `service` 字段。

        Returns:
            `(wss_url, client_config)` 二元组。

        Raises:
            FeishuServerError: 当握手返回 5xx 时抛出（可重试，由 [start][feishu.ws.client.WsClient.start] 退避重连）。
            FeishuError: 当响应 `code` 非 0 时抛出（如鉴权/配置错误，不可重试）。
        """
        client = self._http_client or httpx.AsyncClient()
        try:
            resp = await client.post(
                f"{self._base_url}{_ENDPOINT_PATH}",
                headers={"locale": "zh"},
                json={"AppID": self._app_id, "AppSecret": self._app_secret},
            )
            # 5xx is a transient server-side failure -> surface as FeishuServerError so start() retries.
            if resp.status_code >= 500:
                raise FeishuServerError(resp.status_code, f"handshake failed: HTTP {resp.status_code}")
            payload = resp.json()
        finally:
            if self._http_client is None:
                await client.aclose()

        code = payload.get("code", -1)
        if code != 0:
            raise FeishuError(code, payload.get("msg", ""), raw=payload)

        data = payload.get("data") or {}
        url = data["URL"]
        self._service_id = _parse_service_id(url)
        config = client_config_from_dict(data.get("ClientConfig") or {})
        self._ping_interval = config.ping_interval
        return url, config

    def _ping_frame(self) -> Frame:
        r"""构造一个心跳（ping）控制帧。"""
        return Frame(
            seq_id=0,
            log_id=0,
            service=self._service_id,
            method=FRAME_TYPE_CONTROL,
            headers=[Header("type", "ping")],
        )

    async def _ping_loop(self, websocket: Any, send_lock: asyncio.Lock) -> None:
        r"""后台任务：按 `ping_interval` 周期发送心跳控制帧，直至被取消或连接断开。

        连接在心跳发送期间断开会使 `websocket.send` 抛出异常；此处安静退出（`_serve` 会感知断开并触发
        重连），避免该后台任务的异常无人取回而触发 asyncio 告警。取消（CancelledError）正常向上传播。
        """
        try:
            while True:
                await self._sleep(self._ping_interval)
                async with send_lock:
                    await websocket.send(encode_frame(self._ping_frame()))
        except Exception:  # noqa: BLE001 - a drop mid-ping is expected; exit quietly, don't leak the task exc
            self.logger.debug("ws ping loop stopped", exc_info=True)

    def _handle_control(self, frame: Frame) -> None:
        r"""处理控制帧（心跳回复）：若回复携带 ClientConfig 则刷新心跳间隔。"""
        if frame.payload:
            # Keep the existing interval when a heartbeat reply has no valid ClientConfig.
            with suppress(ValueError, KeyError):
                self._ping_interval = client_config_from_dict(json.loads(frame.payload.decode("utf-8"))).ping_interval

    async def _send_frame(self, websocket: Any, frame: Frame, send_lock: asyncio.Lock) -> None:
        r"""在 `send_lock` 保护下回送一帧（ACK），保证并发任务间的发送不交错。"""
        async with send_lock:
            await websocket.send(encode_frame(frame))

    def _reassemble(self, frame: Frame) -> bytes | None:
        r"""
        按 `sum`/`seq` 头重组分片帧。

        `sum <= 1` 时帧自身即完整载荷，直接返回其 `payload`。否则按 `message_id` 缓存各 `seq`
        分片，集齐 `sum` 个后按序拼接并清理缓冲返回；尚未集齐时返回 `None`。

        Args:
            frame: 数据帧。

        Returns:
            完整的载荷字节；分片尚未集齐时返回 `None`。
        """
        total = int(frame.header("sum") or "1")
        payload = frame.payload or b""
        if total <= 1:
            return payload

        message_id = frame.header("message_id") or ""
        seq = int(frame.header("seq") or "0")
        chunks = self._fragments.get(message_id)
        if chunks is None:
            if len(self._fragments) >= self._max_partial_messages:
                # Drop the oldest unfinished message (dict insertion order) to keep growth bounded.
                self._fragments.pop(next(iter(self._fragments)), None)
            chunks = self._fragments[message_id] = {}
        chunks[seq] = payload
        if len(chunks) < total:
            return None

        ordered = b"".join(chunks[i] for i in range(total))
        del self._fragments[message_id]
        return ordered

    async def _serve(self, websocket: Any) -> None:
        r"""
        在已建立的连接上收发，直至连接关闭。

        启动心跳后台任务并循环接收、解码帧。控制帧（心跳）与分片重组在收发循环内同步处理；
        每条完整消息的分发与 ACK 回送则派生独立任务并发执行，从而避免某个耗时的处理函数阻塞
        后续帧的接收（所有发送经 `send_lock` 串行化以保证帧不交错）。连接关闭（websockets 抛出
        `ConnectionClosed`）时退出循环，并在 `finally` 中取消心跳、等待在途分发任务收尾。

        Args:
            websocket: 已连接的 websocket 对象。
        """
        import websockets

        self._websocket = websocket
        send_lock = asyncio.Lock()
        ping_task = asyncio.ensure_future(self._ping_loop(websocket, send_lock))
        pending: set[asyncio.Task[None]] = set()
        try:
            while True:
                try:
                    raw = await websocket.recv()
                except websockets.ConnectionClosed:
                    break
                if isinstance(raw, str):
                    raw = raw.encode("utf-8")
                frame = decode_frame(raw)
                if frame.method == FRAME_TYPE_CONTROL:
                    self._handle_control(frame)
                    continue
                if frame.method != FRAME_TYPE_DATA:
                    continue
                # Reassemble fragments synchronously in this loop to avoid competing writes to the fragment
                # buffer. Only complete-message dispatch and ACK sending run in separate tasks.
                payload = self._reassemble(frame)
                if payload is None:
                    continue
                task = asyncio.ensure_future(self._handle_frame(websocket, frame, payload, send_lock))
                pending.add(task)
                task.add_done_callback(pending.discard)
        finally:
            ping_task.cancel()
            # Wait for in-flight dispatch tasks after close; their ACKs may fail on the closed connection.
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            self._websocket = None

    async def _handle_frame(self, websocket: Any, frame: Frame, payload: bytes, send_lock: asyncio.Lock) -> None:
        r"""
        处理一条完整入站消息：解析事件、回送 ACK、交由分发器处理（作为独立任务并发执行）。

        卡片回调（[_SYNC_ACK_EVENT_TYPES][feishu.ws.client._SYNC_ACK_EVENT_TYPES]）须把处理结果
        （toast / 更新后的卡片）编码进 ACK 帧，故「先分发、后 ACK」；其余事件（尤其是可能触发慢速
        Agent 循环的 `im.message.receive_v1`）则「先即时 ACK、再后台分发」，避免慢处理函数迟迟不 ACK
        被飞书按「至少一次」语义重投而重复处理同一条消息。

        作为脱离收发循环的独立任务运行，其异常不会冒泡到 `_serve`；因此在此捕获并记录
        （载荷解析失败、处理函数异常或连接已关闭导致的发送失败），避免在连接存活期间被静默吞掉。
        """
        try:
            event = Event.from_payload(json.loads(payload.decode("utf-8")))
            if event.event_type in _SYNC_ACK_EVENT_TYPES:
                # Card actions: the ACK carries the toast / updated card, so dispatch first.
                result, pending_dispatch = await self._dispatch_card_action_for_ack(event)
                await self._send_frame(websocket, _build_ack(frame, result), send_lock)
                if pending_dispatch is not None:
                    await pending_dispatch
            else:
                # ACK immediately so the broker can't redeliver while a slow handler runs, then dispatch.
                await self._send_frame(websocket, _build_ack(frame, None), send_lock)
                await self._dispatcher.dispatch(event)
        except Exception:  # noqa: BLE001 - a per-message task failure must be logged, not silently dropped
            self.logger.exception("ws ack/dispatch failed for frame seq_id=%s", frame.seq_id)

    async def _dispatch_card_action_for_ack(
        self, event: Event
    ) -> tuple[dict[str, Any] | None, asyncio.Task[dict | None] | None]:
        r"""Dispatch a card action, but do not let a slow handler delay the Feishu ACK indefinitely."""
        task = asyncio.ensure_future(self._dispatcher.dispatch(event))
        if self._card_ack_timeout is None:
            return await task, None
        try:
            result = await asyncio.wait_for(asyncio.shield(task), timeout=self._card_ack_timeout)
            return result, None
        except asyncio.TimeoutError:
            return _CARD_ACK_TIMEOUT_RESULT, task

    async def start(self) -> None:
        r"""
        启动长连接并阻塞运行，直至 [aclose][feishu.ws.client.WsClient.aclose] 被调用。

        循环执行「握手 -> 建连 -> 收发」；连接断开后，若开启了自动重连且仍有重连次数，
        则等待 `reconnect_interval` 秒后重试（`reconnect_count == -1` 表示无限重连）。
        重连次数耗尽则停止。握手本身的瞬时失败（网络错误 / 5xx）同样按此退避重试，并计入重连预算；
        鉴权 / 配置等不可重试错误则直接抛出。

        Examples:
            >>> ws = WsClient("cli_app", "secret", EventDispatcher())
            >>> import asyncio
            >>> asyncio.run(ws.start())  # doctest: +SKIP
        """
        # Transient connect/serve failures are retried on the reconnect budget below, not escaped.
        # websockets is optional; include its base exception only when importable so its connect /
        # WS-upgrade / abnormal-close errors count too (ImportError from a missing dep still propagates).
        transient: tuple[type[BaseException], ...] = (OSError, asyncio.TimeoutError)
        try:
            import websockets

            transient += (websockets.WebSocketException,)
        except ImportError:
            pass

        attempts = 0
        config = ClientConfig()  # defaults until the first successful handshake (drives early backoff)
        while not self._stopped:
            try:
                url, config = await self._handshake()
            except (httpx.RequestError, FeishuServerError) as exc:
                # Transient handshake failure (network blip / 5xx): back off and retry rather than
                # aborting the whole connection loop. Non-transient errors (auth/config) propagate.
                if self._stopped or not self._auto_reconnect:
                    raise
                if config.reconnect_count != -1 and attempts >= config.reconnect_count:
                    self.logger.warning("ws handshake retries exhausted (%d)", config.reconnect_count)
                    raise
                attempts += 1
                self.logger.warning("ws handshake failed (%s); retrying", exc)
                await self._sleep(self._reconnect_delay(config))
                continue
            try:
                async with self._connect(url) as websocket:
                    await self._serve(websocket)
            except transient as exc:
                # Transient failure opening/serving the socket after a good handshake (DNS blip,
                # refused, WS-upgrade error, abnormal close). Retry on the same reconnect budget
                # below rather than escaping start(); non-transient errors (bugs/auth) still propagate.
                if self._stopped or not self._auto_reconnect:
                    raise
                self.logger.warning("ws connection failed (%s); retrying", exc)

            if self._stopped or not self._auto_reconnect:
                return
            if config.reconnect_count != -1 and attempts >= config.reconnect_count:
                self.logger.warning("ws reconnect attempts exhausted (%d)", config.reconnect_count)
                return
            attempts += 1
            await self._sleep(self._reconnect_delay(config))

    def _reconnect_delay(self, config: ClientConfig) -> float:
        r"""重连等待时长：在 `reconnect_interval` 之上叠加 `[0, reconnect_nonce)` 的随机抖动，避免雪崩式重连。"""
        return config.reconnect_interval + random.uniform(0, config.reconnect_nonce)

    async def aclose(self) -> None:
        r"""
        请求停止：置位停止标志使 [start][feishu.ws.client.WsClient.start] 的循环退出，并关闭活动连接。
        """
        self._stopped = True
        websocket = self._websocket
        if websocket is not None:
            await websocket.close()


def _parse_service_id(url: str) -> int:
    r"""从 wss URL 的查询串中解析 `service_id`；缺失或非法时返回 0。"""
    values = parse_qs(urlparse(url).query).get("service_id")
    if not values:
        return 0
    try:
        return int(values[0])
    except ValueError:
        return 0


def _build_ack(frame: Frame, result: dict[str, Any] | None) -> Frame:
    r"""
    依据入站数据帧与分发结果构造 ACK 帧。

    复制入站帧的 `seq_id`/`log_id`/`service`/`method` 及其 `message_id`/`type` 头，追加
    `biz_rt` 头，并将响应体写入 `payload`。当分发结果非 `None` 时，结果会被 JSON 序列化后
    再 base64 编码塞入 `data` 字段（飞书长连接 ACK 约定）。

    Args:
        frame: 入站数据帧。
        result: 分发器返回值；为 `None` 时 ACK 不含 `data`。

    Returns:
        待回送的 ACK 帧。
    """
    headers = [Header(key, frame.header(key) or "") for key in ("type", "message_id") if frame.header(key) is not None]
    headers.append(Header("biz_rt", "0"))
    response: dict[str, Any] = {"code": 200, "headers": {}}
    if result is not None:
        response["data"] = base64.b64encode(json.dumps(result).encode("utf-8")).decode("ascii")
    return Frame(
        seq_id=frame.seq_id,
        log_id=frame.log_id,
        service=frame.service,
        method=frame.method,
        headers=headers,
        payload=json.dumps(response).encode("utf-8"),
    )
