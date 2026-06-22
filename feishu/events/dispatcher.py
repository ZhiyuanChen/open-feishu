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

import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

from .envelope import Event
from .idempotency import SeenStore, claim

Handler = Callable[[Event], Awaitable[object]]
ErrorHandler = Callable[[Exception, Event], Awaitable[object]]


class EventDispatcher:
    r"""
    将飞书事件按类型分发给已注册的异步处理函数。

    通过 [on][feishu.events.dispatcher.EventDispatcher.on] 装饰器按事件类型注册处理函数，
    使用 `"*"` 注册可匹配所有事件的兜底处理函数。
    [dispatch][feishu.events.dispatcher.EventDispatcher.dispatch] 会先运行精确匹配的处理函数，
    再运行兜底处理函数。

    单个处理函数抛出的异常会被捕获并记录，不会中断其余处理函数（含 `"*"` 兜底）；可通过
    [on_error][feishu.events.dispatcher.EventDispatcher.on_error] 注册全局异常处理函数集中上报。

    若构造时传入 [SeenStore][feishu.events.idempotency.SeenStore]，分发前会基于
    [Event.event_id][feishu.events.envelope.Event.event_id] 去重，避免飞书重试导致重复处理。

    Args:
        seen_store: 事件去重存储；为 `None` 时不做去重。
        logger: 处理函数异常的日志器；缺省使用名为 `feishu` 的日志器。

    飞书文档:
        [订阅事件](https://open.feishu.cn/document/server-docs/event-subscription-guide/overview)

    Examples:
        >>> import asyncio
        >>> from feishu.events.envelope import Event
        >>> dispatcher = EventDispatcher()
        >>> seen = []
        >>> @dispatcher.on("im.message.receive_v1")
        ... async def handle(event):
        ...     seen.append(event.event_id)
        ...
        >>> event = Event.from_payload(
        ...     {"schema": "2.0", "header": {"event_type": "im.message.receive_v1", "event_id": "evt_42"}, "event": {}}
        ... )
        >>> asyncio.run(dispatcher.dispatch(event))
        >>> seen
        ['evt_42']
    """

    def __init__(self, *, seen_store: SeenStore | None = None, logger: logging.Logger | None = None):
        self._handlers: dict[str, list[Handler]] = {}
        self._seen_store = seen_store
        self._logger = logger or logging.getLogger("feishu")
        self._error_handler: ErrorHandler | None = None

    def on(self, event_type: str) -> Callable[[Handler], Handler]:
        r"""
        注册指定事件类型的处理函数（装饰器）。

        同一类型可注册多个处理函数，按注册顺序执行。使用 `"*"` 注册兜底处理函数，
        它会在所有事件的精确匹配处理函数之后执行。

        Args:
            event_type: 事件类型，如 `im.message.receive_v1`；`"*"` 表示匹配全部。

        Returns:
            接收处理函数并将其注册的装饰器；原函数会被原样返回。

        Examples:
            >>> dispatcher = EventDispatcher()
            >>> @dispatcher.on("*")
            ... async def fallback(event):
            ...     return None
            ...
            >>> fallback.__name__
            'fallback'
        """

        def register(handler: Handler) -> Handler:
            self._handlers.setdefault(event_type, []).append(handler)
            return handler

        return register

    def on_error(self, handler: ErrorHandler) -> ErrorHandler:
        r"""
        注册全局异常处理函数（仿 Slack Bolt 的 `@app.error`）。

        当任一事件处理函数抛出异常时，框架会先记录日志，再调用此处理函数用于集中上报或返回兜底结果。
        处理函数接收异常与对应事件；若其返回非 `None` 值且本次分发尚无其他结果，则作为返回值
        （例如卡片回调可借此返回错误 toast）。后注册的会覆盖先注册的。

        Args:
            handler: 形如 `async def(exc: Exception, event: Event) -> dict | None` 的异常处理函数。

        Returns:
            原处理函数（便于作装饰器使用）。

        Examples:
            >>> dispatcher = EventDispatcher()
            >>> @dispatcher.on_error
            ... async def report(exc, event):
            ...     return None
            ...
            >>> report.__name__
            'report'
        """
        self._error_handler = handler
        return handler

    async def dispatch(self, event: Event) -> dict | None:
        r"""
        将事件分发给匹配的处理函数并返回结果。

        先执行与 [event.event_type][feishu.events.envelope.Event.event_type] 精确匹配的处理函数，
        再执行以 `"*"` 注册的兜底处理函数。若配置了去重存储且事件已处理过，则直接返回 `None`，
        不执行任何处理函数。

        单个处理函数抛出的异常会被捕获、记录，并交由可选的全局异常处理函数处理，随后继续执行其余
        处理函数，确保一个出错的处理函数不会中断其他处理函数或丢失卡片回调的 ACK。

        返回值取第一个非 `None` 的处理函数结果，通常用于卡片回调返回 `{"toast": ..., "card": ...}`。

        Args:
            event: 待分发的事件。

        Returns:
            首个非 `None` 的处理函数返回值；若已被去重或所有处理函数均返回 `None`，则返回 `None`。

        Examples:
            >>> import asyncio
            >>> from feishu.events.envelope import Event
            >>> dispatcher = EventDispatcher()
            >>> @dispatcher.on("card.action.trigger")
            ... async def on_card(event):
            ...     return {"toast": {"type": "success", "content": "ok"}}
            ...
            >>> event = Event.from_payload(
            ...     {"schema": "2.0", "header": {"event_type": "card.action.trigger", "event_id": "c1"}, "event": {}}
            ... )
            >>> asyncio.run(dispatcher.dispatch(event))
            {'toast': {'type': 'success', 'content': 'ok'}}
        """
        if self._seen_store is not None and not await claim(self._seen_store, event.event_id):
            return None

        handlers = list(self._handlers.get(event.event_type, ()))
        handlers += list(self._handlers.get("*", ()))
        result: dict[str, Any] | None = None
        for handler in handlers:
            try:
                value = await handler(event)
            except Exception as exc:  # noqa: BLE001 - isolate: one failing handler must not abort the rest
                value = await self._on_handler_error(exc, event)
            if value is not None and result is None:
                result = cast(dict, value)
        return result

    async def _on_handler_error(self, exc: Exception, event: Event) -> object:
        r"""记录处理函数异常；若注册了全局异常处理函数则调用它，并可由其返回兜底结果。"""
        self._logger.error("event handler failed for %s (%s)", event.event_type, event.event_id, exc_info=exc)
        if self._error_handler is None:
            return None
        try:
            return await self._error_handler(exc, event)
        except Exception:  # noqa: BLE001 - the error handler itself must never break dispatch
            self._logger.exception("event error-handler failed for %s", event.event_type)
            return None
