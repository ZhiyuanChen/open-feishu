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

import builtins  # 'list' is a method here; annotations use builtins.list to avoid shadowing
from typing import Any

from chanfig import NestedDict

from .._namespace import Namespace
from .._url import quote_segment


class PinsNamespace(Namespace):
    r"""
    消息 Pin 接口命名空间。

    通过 `client.im.pins` 访问，封装飞书会话中 Pin（置顶标记）相关的服务端接口：将某条消息 Pin 到会话、
    取消 Pin、以及列举会话内的 Pin 消息。

    通常无需直接实例化，应通过 `client.im.pins` 访问。

    飞书文档:
        [Pin 概述](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/pin/create)
    """

    async def create(self, message_id: str) -> NestedDict:
        r"""
        将一条消息 Pin 到其所在会话。

        Args:
            message_id: 待 Pin 的消息 ID（`om_` 开头）。

        Returns:
            Pin 结果数据，含 `pin` 字段，内含 `message_id`、`chat_id`、`operator_id`、`create_time` 等信息。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [Pin 一条消息](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/pin/create)

        Examples:
            >>> await client.im.pins.create("om_xxx")  # doctest:+SKIP
            {'pin': {'message_id': 'om_xxx', 'chat_id': 'oc_xxx'}}
        """
        return await self._request_data("POST", "im/v1/pins", json={"message_id": message_id})

    async def delete(self, message_id: str) -> NestedDict:
        r"""
        移除一条消息的 Pin。

        Args:
            message_id: 待取消 Pin 的消息 ID。

        Returns:
            空数据体（接口成功时不返回额外字段）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [移除 Pin 消息](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/pin/delete)

        Examples:
            >>> await client.im.pins.delete("om_xxx")  # doctest:+SKIP
            {}
        """
        return await self._request_data("DELETE", f"im/v1/pins/{quote_segment(message_id)}")

    async def list(
        self,
        chat_id: str,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        page_size: int = 50,
        max_items: int | None = None,
    ) -> builtins.list[NestedDict]:
        r"""
        列举会话内的 Pin 消息。

        自动翻页并汇总指定会话中的 Pin 消息，可选按时间窗过滤。

        Args:
            chat_id: 会话 ID（`oc_` 开头）。
            start_time: 起始时间（毫秒时间戳，字符串）；为空时不限制。
            end_time: 结束时间（毫秒时间戳，字符串）；为空时不限制。
            page_size: 每页数量。默认为 50；超过 [feishu.consts.MAX_PAGE_SIZE][] 时由客户端收敛。
            max_items: 最多返回的 Pin 数量，`None` 表示不限制。默认为 `None`。

        Returns:
            Pin 消息对象列表（`data.items`），每项含 `message_id`、`chat_id`、`operator_id`、`create_time` 等；
            无 Pin 时返回空列表。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取群内 Pin 消息](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/pin/list)

        Examples:
            >>> await client.im.pins.list("oc_xxx")  # doctest:+SKIP
            [{'message_id': 'om_xxx', 'operator_id': 'ou_xxx'}]
        """
        params: dict[str, Any] = {"chat_id": chat_id}
        if start_time is not None:
            params["start_time"] = start_time
        if end_time is not None:
            params["end_time"] = end_time
        return await self._client.paginate_get("im/v1/pins", params=params, page_size=page_size, max_items=max_items)
