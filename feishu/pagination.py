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

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from chanfig import NestedDict


async def iterate(
    fetch: Callable[[str | None], Awaitable[NestedDict]],
    *,
    max_items: int | None = None,
    early_stop: Callable[[list[Any], Any], bool] | None = None,
    items_key: str = "items",
) -> AsyncIterator[Any]:
    r"""
    逐条异步迭代飞书分页接口返回的全部条目。

    反复调用 `fetch(page_token)` 翻页，从每页响应体的 `data[items_key]`（默认 `items`）
    中逐条产出，并依据 `data.has_more` 与 `data.page_token` 决定是否继续翻页。可通过
    `max_items` 限制最多产出的条目数，或通过 `early_stop` 在满足条件时提前停止。
    为防止异常响应导致死循环，当 `has_more` 为真但下一页 `page_token` 为空或与上一页
    相同时，亦立即停止翻页。

    Args:
        fetch: 接受 `page_token`（首页为 `None`）并返回分页响应体的异步可调用对象，
            响应体需包含 `data[items_key]`、`data.has_more` 与 `data.page_token`。
        max_items: 最多产出的条目数；为 `None` 时不限制。
        early_stop: 形如 `(已收集列表, 当前条目) -> bool` 的判定函数，返回 `True`
            时立即停止迭代且不产出当前条目。
        items_key: 每页响应体中条目所在的字段名，默认 `items`；个别接口使用其他键名
            （如日历列表的 `calendar_list`），可据此指定。

    Yields:
        分页结果中的每一条目，按页内顺序依次产出。

    飞书文档:
        [分页查询](https://open.feishu.cn/document/server-docs/api-call-guide/calling-process/paging)

    Examples:
        >>> import asyncio
        >>> async def fetch(page_token):
        ...     if page_token is None:
        ...         return {"data": {"items": [1, 2], "has_more": True, "page_token": "p2"}}
        ...     return {"data": {"items": [3], "has_more": False}}
        >>> async def main():
        ...     return [item async for item in iterate(fetch)]
        >>> asyncio.run(main())
        [1, 2, 3]
        >>> async def capped():
        ...     return [item async for item in iterate(fetch, max_items=2)]
        >>> asyncio.run(capped())
        [1, 2]
    """
    collected = 0
    acc: list[Any] = []  # feeds early_stop only; left empty (no retention) when early_stop is None
    page_token: str | None = None
    while True:
        envelope = await fetch(page_token)
        data = envelope["data"]
        for item in data.get(items_key, []) or []:
            if early_stop is not None:
                if early_stop(acc, item):
                    return
                acc.append(item)
            yield item
            collected += 1
            if max_items is not None and collected >= max_items:
                return
        if not data.get("has_more"):
            return
        next_token = data.get("page_token")
        if not next_token or next_token == page_token:
            return
        page_token = next_token


async def paginate(
    fetch: Callable[[str | None], Awaitable[NestedDict]],
    *,
    max_items: int | None = None,
    early_stop: Callable[[list[Any], Any], bool] | None = None,
    items_key: str = "items",
) -> list[Any]:
    r"""
    遍历飞书分页接口并将全部条目收集为列表。

    等价于把 [feishu.pagination.iterate][] 产出的条目一次性收集到列表中，适用于
    无需流式处理、希望直接拿到完整结果的场景。

    Args:
        fetch: 接受 `page_token`（首页为 `None`）并返回分页响应体的异步可调用对象。
        max_items: 最多收集的条目数；为 `None` 时不限制。
        early_stop: 形如 `(已收集列表, 当前条目) -> bool` 的判定函数，返回 `True`
            时立即停止且不收集当前条目。
        items_key: 每页响应体中条目所在的字段名，默认 `items`；个别接口使用其他键名
            （如日历列表的 `calendar_list`），可据此指定。

    Returns:
        包含全部（或受 `max_items` / `early_stop` 限制后的）条目的列表。

    飞书文档:
        [分页查询](https://open.feishu.cn/document/server-docs/api-call-guide/calling-process/paging)

    Examples:
        >>> import asyncio
        >>> async def fetch(page_token):
        ...     if page_token is None:
        ...         return {"data": {"items": [1, 2], "has_more": True, "page_token": "p2"}}
        ...     return {"data": {"items": [3], "has_more": False}}
        >>> asyncio.run(paginate(fetch))
        [1, 2, 3]
        >>> asyncio.run(paginate(fetch, early_stop=lambda acc, item: item == 3))
        [1, 2]
        >>> async def fetch_calendars(page_token):
        ...     return {"data": {"calendar_list": [9, 8], "has_more": False}}
        >>> asyncio.run(paginate(fetch_calendars, items_key="calendar_list"))
        [9, 8]
    """
    return [item async for item in iterate(fetch, max_items=max_items, early_stop=early_stop, items_key=items_key)]
