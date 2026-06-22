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


class ReactionsNamespace(Namespace):
    r"""
    消息表情回复（Reaction）接口命名空间。

    通过 `client.im.reactions` 访问，封装飞书消息表情回复相关的服务端接口：为消息添加表情回复、
    删除某条表情回复、以及列举一条消息上的表情回复。表情以 `emoji_type`（如 `SMILE`、`THUMBSUP`）标识。

    通常无需直接实例化，应通过 `client.im.reactions` 访问。

    飞书文档:
        [表情回复概述](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message-reaction/create)
    """

    async def create(self, message_id: str, emoji_type: str) -> NestedDict:
        r"""
        为一条消息添加表情回复。

        Args:
            message_id: 目标消息 ID（`om_` 开头）。
            emoji_type: 表情类型枚举值，如 `SMILE`、`THUMBSUP`、`OK` 等。

        Returns:
            添加结果数据，含 `reaction_id`、`operator`（操作者）、`action_time`、`reaction_type`
            （内含 `emoji_type`）等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [添加消息表情回复](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message-reaction/create)

        Examples:
            >>> await client.im.reactions.create("om_xxx", "THUMBSUP")  # doctest:+SKIP
            {'reaction_id': 'ZCaCIjUBVVdg...', 'reaction_type': {'emoji_type': 'THUMBSUP'}}
        """
        body = {"reaction_type": {"emoji_type": emoji_type}}
        return await self._request_data("POST", f"im/v1/messages/{quote_segment(message_id)}/reactions", json=body)

    async def delete(self, message_id: str, reaction_id: str) -> NestedDict:
        r"""
        删除一条消息表情回复。

        Args:
            message_id: 目标消息 ID。
            reaction_id: 待删除的表情回复 ID（添加表情回复时返回的 `reaction_id`）。

        Returns:
            被删除的表情回复数据，结构同添加时的返回。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [删除消息表情回复](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message-reaction/delete)

        Examples:
            >>> await client.im.reactions.delete("om_xxx", "ZCaCIjUBVVdg...")  # doctest:+SKIP
            {'reaction_id': 'ZCaCIjUBVVdg...', 'reaction_type': {'emoji_type': 'THUMBSUP'}}
        """
        return await self._request_data(
            "DELETE", f"im/v1/messages/{quote_segment(message_id)}/reactions/{quote_segment(reaction_id)}"
        )

    async def list(
        self,
        message_id: str,
        *,
        emoji_type: str | None = None,
        page_size: int = 50,
        max_items: int | None = None,
    ) -> builtins.list[NestedDict]:
        r"""
        列举一条消息上的表情回复。

        自动翻页并汇总目标消息的表情回复，可选按 `emoji_type` 过滤。

        Args:
            message_id: 目标消息 ID。
            emoji_type: 仅返回该表情类型的回复（对应查询参数 `reaction_type`）；为空时返回全部类型。
            page_size: 每页数量。默认为 50；超过 [feishu.consts.MAX_PAGE_SIZE][] 时由客户端收敛。
            max_items: 最多返回的表情回复数量，`None` 表示不限制。默认为 `None`。

        Returns:
            表情回复对象列表（`data.items`），每项含 `reaction_id`、`operator`、`action_time`、
            `reaction_type` 等字段；无表情回复时返回空列表。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取消息表情回复](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message-reaction/list)

        Examples:
            >>> await client.im.reactions.list("om_xxx", emoji_type="THUMBSUP")  # doctest:+SKIP
            [{'reaction_id': 'ZCaCIjUBVVdg...', 'operator': {'operator_id': 'ou_xxx'}}]
        """
        params: dict[str, Any] = {}
        if emoji_type is not None:
            params["reaction_type"] = emoji_type
        return await self._client.paginate_get(
            f"im/v1/messages/{quote_segment(message_id)}/reactions",
            params=params,
            page_size=page_size,
            max_items=max_items,
        )
