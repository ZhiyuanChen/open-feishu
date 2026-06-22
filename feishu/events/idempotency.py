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
import time
from collections.abc import Callable
from typing import Protocol, runtime_checkable


@runtime_checkable
class SeenStore(Protocol):
    r"""
    事件去重存储的协议接口。

    飞书在投递超时时会重试推送，导致同一 `event_id` 多次到达。实现本协议即可为接收器
    （[create_event_route][feishu.events.receiver.create_event_route]、
    [create_card_route][feishu.events.receiver.create_card_route]）或
    [EventDispatcher][feishu.events.dispatcher.EventDispatcher] 提供幂等保证。
    通过 [InMemorySeenStore][feishu.events.idempotency.InMemorySeenStore] 即得到一个内置实现，
    生产环境可改用基于 Redis 等共享存储的实现。

    本协议使用 `runtime_checkable`，可用 `isinstance` 进行结构化校验。

    飞书文档:
        [接收事件](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case)
    """

    async def seen(self, event_id: str) -> bool:
        r"""
        查询 `event_id` 是否已被处理过。
        """
        ...

    async def mark(self, event_id: str) -> None:
        r"""
        将 `event_id` 标记为已处理。
        """
        ...


class InMemorySeenStore:
    r"""
    基于进程内存、带 TTL 的 [SeenStore][feishu.events.idempotency.SeenStore] 实现。

    将已处理的 `event_id` 连同过期时间存入字典，每次访问时清理过期项。适合单进程部署与测试；
    多副本部署时各进程内存互不共享，应改用基于共享存储的实现。

    Args:
        ttl: 记录的存活时长（秒），超过后视为未见过。默认 `3600`。
        now: 单调时钟函数，默认 `time.monotonic`，可注入以便测试。

    Examples:
        >>> import asyncio
        >>> store = InMemorySeenStore()
        >>> asyncio.run(store.seen("evt_1"))
        False
        >>> asyncio.run(store.mark("evt_1"))
        >>> asyncio.run(store.seen("evt_1"))
        True
    """

    def __init__(self, ttl: float = 3600.0, *, now: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl
        self._now = now
        self._store: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def add(self, event_id: str) -> bool:
        r"""
        原子地认领 `event_id`：此前未标记（或已过期）则标记并返回 `True`，否则返回 `False`。

        将 `seen` 检查与 `mark` 标记合并在同一把锁内完成，消除二者之间的检查-标记竞态——
        并发投递的重复事件中只有一个会得到 `True`。

        Args:
            event_id: 待认领的事件标识。

        Returns:
            首次认领返回 `True`（应处理），重复返回 `False`（应跳过）。
        """
        async with self._lock:
            self._purge()
            if event_id in self._store:
                return False
            self._store[event_id] = self._now() + self._ttl
            return True

    async def seen(self, event_id: str) -> bool:
        r"""
        查询 `event_id` 是否在 TTL 内被标记过。

        Args:
            event_id: 待查询的事件标识。

        Returns:
            已标记且未过期返回 `True`，否则返回 `False`。
        """
        async with self._lock:
            self._purge()
            return event_id in self._store

    async def mark(self, event_id: str) -> None:
        r"""
        标记 `event_id` 为已处理，并按 TTL 设置过期时间。

        Args:
            event_id: 待标记的事件标识。
        """
        async with self._lock:
            self._purge()
            self._store[event_id] = self._now() + self._ttl

    def _purge(self) -> None:
        now = self._now()
        expired = [k for k, exp in self._store.items() if exp <= now]
        for k in expired:
            del self._store[k]


async def claim(store: SeenStore, event_id: str) -> bool:
    r"""
    向 `store` 认领 `event_id`，返回是否为首次见到（应处理）。

    若 `store` 提供原子的 `add(event_id) -> bool`（如
    [InMemorySeenStore][feishu.events.idempotency.InMemorySeenStore]）则优先使用，从而在并发重复投递下
    也能保证「检查并标记」原子完成；否则回退到 `seen()` + `mark()` 两步（语义不变，其原子性由具体存储自行保证）。

    Args:
        store: 事件去重存储。
        event_id: 待认领的事件标识。

    Returns:
        首次见到返回 `True`（应处理该事件），重复返回 `False`（应跳过）。
    """
    add = getattr(store, "add", None)
    if callable(add):
        return bool(await add(event_id))
    if await store.seen(event_id):
        return False
    await store.mark(event_id)
    return True
