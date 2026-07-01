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

r"""CardKit v1 流式卡片助手：将增量生成的 LLM 文本持续推送到一张已发送的卡片中。

整个生命周期由四次请求构成，全部经由 ``client.request`` 发出，因此自动继承令牌刷新、
重试与限流等底层处理：

1. ``POST cardkit/v1/cards``：创建一个 ``card_json`` 卡片实体（``streaming_mode=True``）。
2. ``POST im/v1/messages``：以 ``interactive`` 消息类型发送该卡片。
3. ``PUT  .../elements/{id}/content``：流式写入**完整的累积文本**（而非增量），
   并携带严格递增的 ``sequence`` 以及每次刷新独立生成的 ``uuid``。
4. ``PATCH .../settings``：收尾（``streaming_mode=False``）。该步骤**必须执行**：
   即使生产者抛出异常也会运行，因为卡片在 10 分钟后会自动关闭流式状态，
   未收尾的流会白白浪费卡片实体。

限流约定：内容写入与收尾补丁共享同一个单调递增的 ``sequence``
（由 `feishu.streaming.cardkit._SequenceCounter` 通过 asyncio 锁保证原子性）。
写入带去抖（debounce，默认 0.25 秒，约 4 次/秒，远低于飞书 10 次/秒/卡片的上限）：
只有在去抖间隔已过且缓冲区内容发生变化时才会真正发出写入，并在收尾前强制刷新一次。

测试注入接口：[feishu.streaming.cardkit.stream_card][] 接受 ``_now``/``_new_uuid``
参数（默认为模块级的 ``time.monotonic`` / ``_new_uuid``）。测试可传入手动时钟，
使去抖行为完全确定且不产生任何真实等待。

飞书文档:
    [流式更新卡片](https://open.feishu.cn/document/cardkit-v1/streaming-updates-openapi-overview)
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid as _uuid
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any

from ..cards.builder import Card
from ..im import infer_receive_id_type
from . import _cardkit_spec as spec

if TYPE_CHECKING:
    from ..client import FeishuClient


def _build_streaming_card(
    *,
    element_id: str,
    header: str | None = None,
    template: str | None = None,
    initial: str = "",
) -> dict[str, Any]:
    r"""构建一张处于流式模式、含单个 markdown 元素的卡片 2.0 字典。

    直接复用 [feishu.cards.builder.Card][] 的链式接口构建卡片，并将其置于流式模式
    （``streaming_mode=True``），其中唯一的 markdown 元素带有指定的 ``element_id``，
    供后续流式写入定位。

    Args:
        element_id: markdown 元素的 ID，流式写入据此定位要更新的元素。
        header: 卡片标题文本。为 ``None`` 时不渲染标题。
        template: 标题栏配色模板（如 ``blue``、``green``）。仅在提供 ``header`` 时生效，默认为 ``blue``。
        initial: markdown 元素的初始内容，默认为空字符串。

    Returns:
        卡片 2.0 的字典结构，可序列化后用于创建卡片实体。

    Examples:
        >>> card = _build_streaming_card(element_id="md", header="标题", initial="你好")
        >>> card["config"]["streaming_mode"]
        True
        >>> card["body"]["elements"][0]["element_id"]
        'md'
        >>> card["body"]["elements"][0]["content"]
        '你好'
        >>> card["header"]["title"]["content"]
        '标题'
    """
    card = Card()
    card.config(**{spec.STREAMING_MODE_KEY: True})
    if header is not None:
        card.header(header, template=template or "blue")
    card.markdown(initial, element_id=element_id)
    return card.to_dict()


# Injection seam (mirrors TokenManager(..., now=time.monotonic)): override
# `now` in tests to make debounce deterministic with NO real sleeping.
_now = time.monotonic


def _new_uuid() -> str:
    return str(_uuid.uuid4())


class _SequenceCounter:
    r"""同一张卡片所有 CardKit 写入共享的严格递增序号。

    设计要求每张卡片只用一个单调递增的 ``sequence``，每次写入（内容写入以及收尾的
    settings 补丁）都将其加一。递增操作在 ``asyncio.Lock`` 保护下原子完成，
    保证并发写入时序号不会重复或乱序。
    """

    def __init__(self, start: int = 1) -> None:
        self._value = start
        self._lock = asyncio.Lock()

    async def next(self) -> int:
        async with self._lock:
            value = self._value
            self._value += 1
            return value


class _Flusher:
    r"""向单个卡片元素写入**完整累积文本**的限流、幂等写入器。

    去抖是一道**闸门**而非阻塞：当去抖间隔尚未到达或文本未发生变化时，``write``
    直接返回 ``False`` 且不发起任何 HTTP 请求（除非 ``force=True``）。生产者的节奏
    由调用方 [feishu.streaming.cardkit.stream_card][] 控制，写入器只负责守住单卡片的
    写入频率上限。每次真正发出的写入都携带完整累积文本、严格递增的序号以及全新的 uuid。
    """

    def __init__(
        self,
        client: FeishuClient,
        *,
        card_id: str,
        element_id: str,
        counter: _SequenceCounter,
        debounce_s: float,
        now: Callable[[], float],
        new_uuid: Callable[[], str],
    ) -> None:
        self._client = client
        self._card_id = card_id
        self._element_id = element_id
        self._counter = counter
        self._debounce_s = debounce_s
        self._now = now
        self._new_uuid = new_uuid
        self._last_text: str | None = None
        self._last_flush_at: float | None = None

    async def write(self, full_text: str, *, force: bool = False) -> bool:
        if full_text == self._last_text:
            return False  # unchanged buffer -> skip (no redundant re-PUT, even when forced)
        if not force and self._last_flush_at is not None and (self._now() - self._last_flush_at) < self._debounce_s:
            return False  # within debounce window -> throttle
        body = {
            spec.CONTENT_FIELD: full_text,
            spec.SEQUENCE_FIELD: await self._counter.next(),
            spec.UUID_FIELD: self._new_uuid(),
        }
        await self._client.request("PUT", spec.content_path(self._card_id, self._element_id), json=body)
        self._last_text = full_text
        self._last_flush_at = self._now()
        return True


async def stream_card(
    client: FeishuClient,
    tokens: AsyncIterator[str],
    *,
    receive_id: str | None = None,
    receive_id_type: str | None = None,
    reply_to_message_id: str | None = None,
    element_id: str = "md",
    debounce_s: float = 0.25,
    header: str | None = None,
    template: str | None = None,
    _now: Callable[[], float] = _now,
    _new_uuid: Callable[[], str] = _new_uuid,
) -> str:
    r"""驱动 CardKit v1 流式生命周期，并返回创建出的 ``card_id``。

    先创建并发送一张初始流式卡片，随后将逐 token 累积的**完整文本**流式写入
    ``element_id`` 指定的 markdown 元素（带去抖限流、序号单调递增、每次写入以 uuid 标识），
    最后**始终**执行收尾（``streaming_mode=False``）。即使 token 生产者抛出异常也会收尾：
    卡片的流式状态在 10 分钟后会自动关闭，若不主动收尾，泄漏的开放流会白白浪费卡片实体。

    Args:
        client: 飞书客户端，须提供 ``async request(method, path, *, params, json)`` 方法。
        tokens: 逐段产出文本的异步迭代器，通常为 LLM 的流式输出。每段会被追加到累积文本上。
        receive_id: 消息接收者 ID，其类型由 ``receive_id_type`` 指定。发新消息时必填，且与
            ``reply_to_message_id`` 二者只能取其一。
        receive_id_type: 接收者 ID 类型，如 ``open_id``、``user_id``、``chat_id`` 等；为空时按 ``receive_id``
            前缀自动推断（与 IM 发送族一致），仅发新消息时适用。
        reply_to_message_id: 以回复形式发送时的目标消息 ``message_id``（``om_`` 开头）。提供时初始卡片
            经回复接口发出（在原消息所在会话内成串显示），此时 ``receive_id`` 应留空。
        element_id: 流式写入目标 markdown 元素的 ID，默认为 ``md``。
        debounce_s: 去抖间隔（秒），默认为 ``0.25``（约 4 次/秒，远低于飞书 10 次/秒/卡片的上限）。
        header: 卡片标题文本。为 ``None`` 时不渲染标题。
        template: 标题栏配色模板（如 ``blue``、``green``），仅在提供 ``header`` 时生效。
        _now: 单调时钟函数，仅供测试注入以使去抖确定化，生产调用请勿传入。
        _new_uuid: uuid 生成函数，仅供测试注入，生产调用请勿传入。

    Returns:
        创建出的卡片实体 ``card_id``。

    飞书文档:
        [创建卡片实体](https://open.feishu.cn/document/cardkit-v1/card/create)

        [流式更新文本](https://open.feishu.cn/document/cardkit-v1/card-element/content)

        [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)

    Examples:
        >>> async def tokens():
        ...     for tok in ["你好", "，", "世界"]:
        ...         yield tok
        >>> card_id = await client.stream_card(  # doctest: +SKIP
        ...     tokens(), receive_id="ou_xxx", receive_id_type="open_id"
        ... )
        >>> card_id  # doctest: +SKIP
        'card_42'
    """
    if (receive_id is None) == (reply_to_message_id is None):
        raise ValueError("stream_card requires exactly one of receive_id or reply_to_message_id")
    counter = _SequenceCounter()

    # 1) Create the card entity.
    card_json = _build_streaming_card(element_id=element_id, header=header, template=template)
    create = await client.request(
        "POST",
        spec.CREATE_CARD_PATH,
        json={spec.CREATE_CARD_TYPE_FIELD: spec.CREATE_CARD_TYPE, spec.CREATE_CARD_DATA_FIELD: json.dumps(card_json)},
    )
    card_id = create["data"]["card_id"]

    # 2) Send the interactive message referencing the entity — fresh, or in reply position.
    content = json.dumps({"type": spec.SEND_CARD_CONTENT_TYPE, "data": {"card_id": card_id}})
    if reply_to_message_id is not None:
        await client.request(
            "POST",
            spec.reply_message_path(reply_to_message_id),
            json={"msg_type": spec.SEND_MESSAGE_TYPE, "content": content},
        )
    else:
        assert receive_id is not None  # guaranteed by the receive_id / reply_to_message_id XOR check above
        rid_type = receive_id_type or infer_receive_id_type(receive_id)
        await client.request(
            "POST",
            spec.SEND_MESSAGE_PATH,
            params={"receive_id_type": rid_type},
            json={"receive_id": receive_id, "msg_type": spec.SEND_MESSAGE_TYPE, "content": content},
        )

    # 3) Stream cumulative text. Finalize is mandatory -> try/finally.
    flusher = _Flusher(
        client,
        card_id=card_id,
        element_id=element_id,
        counter=counter,
        debounce_s=debounce_s,
        now=_now,
        new_uuid=_new_uuid,
    )
    accumulated = ""
    try:
        async for tok in tokens:
            accumulated += tok
            await flusher.write(accumulated)  # debounced; may skip
    finally:
        # One mandatory final flush of whatever we accumulated, then finalize.
        if accumulated:
            await flusher.write(accumulated, force=True)
        await client.request(
            "PATCH",
            spec.settings_path(card_id),
            json={
                spec.SETTINGS_FIELD: json.dumps({"config": {spec.STREAMING_MODE_KEY: False}}),
                spec.SEQUENCE_FIELD: await counter.next(),
            },
        )
    return card_id
