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

import builtins  # 'list' is a method here; bare list[...] in later annotations resolves to it, so use builtins.list
from typing import Any

from chanfig import NestedDict

from .._namespace import Namespace
from .._url import quote_segment


class ChatsNamespace(Namespace):
    r"""
    群组接口命名空间。

    封装飞书群组相关的服务端接口，包括创建、查询、更新、解散群组，以及添加、移除、列举群成员等能力。

    通常无需直接实例化，应通过 `client.im.chats` 访问。

    飞书文档:
        [群组管理概述](https://open.feishu.cn/document/server-docs/group/chat/intro)
    """

    async def add_members(
        self, chat_id: str, id_list: builtins.list[str], *, member_id_type: str = "open_id"
    ) -> NestedDict:
        r"""
        将用户或机器人拉入群组。

        Args:
            chat_id: 群 ID。
            id_list: 要添加的成员 ID 列表。
            member_id_type: 成员 ID 类型，默认为 `open_id`。

        Returns:
            接口返回的数据，可能包含无效或失败的成员 ID 信息。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [将用户或机器人拉入群聊](https://open.feishu.cn/document/server-docs/group/chat-member/create)

        Examples:
            >>> await client.im.chats.add_members("oc_chat1", ["ou_user1", "ou_user2"])  # doctest:+SKIP
            {'invalid_id_list': [], ...}  # noqa: E501
        """
        return await self._request_data(
            "POST",
            f"im/v1/chats/{quote_segment(chat_id)}/members",
            params={"member_id_type": member_id_type},
            json={"id_list": id_list},
        )

    async def create(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        user_id_list: builtins.list[str] | None = None,
        bot_id_list: builtins.list[str] | None = None,
        chat_mode: str = "group",
        user_id_type: str = "open_id",
        **opts: Any,
    ) -> NestedDict:
        r"""
        创建群组。

        仅将显式传入的字段写入请求体，未设置的字段会被省略。额外的关键字参数（`opts`）中值为
        `None` 的项也会被忽略，其余项原样并入请求体。

        Args:
            name: 群名称。
            description: 群描述。
            user_id_list: 初始群成员的用户 ID 列表。
            bot_id_list: 初始群机器人的 ID 列表。
            chat_mode: 群模式，默认为 `group`。
            user_id_type: 用户 ID 类型，默认为 `open_id`。
            **opts: 其他创建参数，例如 `avatar`、`owner_id`、`chat_type` 等；值为 `None` 时忽略。

        Returns:
            创建后的群组数据，包含 `chat_id` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [创建群](https://open.feishu.cn/document/server-docs/group/chat/create)

        Examples:
            >>> await client.im.chats.create(name="My Chat", user_id_list=["ou_abc"])  # doctest:+SKIP
            {'chat_id': 'oc_test123', ...}  # noqa: E501
        """
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if user_id_list is not None:
            body["user_id_list"] = user_id_list
        if bot_id_list is not None:
            body["bot_id_list"] = bot_id_list
        if chat_mode is not None:
            body["chat_mode"] = chat_mode
        body.update({k: v for k, v in opts.items() if v is not None})
        return await self._request_data("POST", "im/v1/chats", params={"user_id_type": user_id_type}, json=body)

    async def disband(self, chat_id: str) -> NestedDict:
        r"""
        解散群组。

        Args:
            chat_id: 群 ID。

        Returns:
            接口返回的数据（通常为空）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [解散群](https://open.feishu.cn/document/server-docs/group/chat/delete)

        Examples:
            >>> await client.im.chats.disband("oc_chat1")  # doctest:+SKIP
            {}
        """
        return await self._request_data("DELETE", f"im/v1/chats/{quote_segment(chat_id)}")

    async def get(self, chat_id: str, *, user_id_type: str = "open_id") -> NestedDict:
        r"""
        获取群信息。

        Args:
            chat_id: 群 ID。
            user_id_type: 返回的用户 ID 类型，默认为 `open_id`。

        Returns:
            群组数据。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取群信息](https://open.feishu.cn/document/server-docs/group/chat/get)

        Examples:
            >>> await client.im.chats.get("oc_chat1")  # doctest:+SKIP
            {'chat_id': 'oc_test123', ...}  # noqa: E501
        """
        return await self._request_data(
            "GET", f"im/v1/chats/{quote_segment(chat_id)}", params={"user_id_type": user_id_type}
        )

    async def list(
        self, *, user_id_type: str = "open_id", page_size: int = 50, max_items: int | None = None
    ) -> builtins.list[NestedDict]:
        r"""
        获取用户或机器人所在的群列表。

        自动翻页并将各页结果拼接为单个列表返回。`page_size` 会被限制在
        [feishu.consts.MAX_PAGE_SIZE][] 以内。

        Args:
            user_id_type: 返回的用户 ID 类型，默认为 `open_id`。
            page_size: 每页条数，默认为 50，超过上限时按上限截断。
            max_items: 最多返回的条数；为空表示返回全部。

        Returns:
            群组数据列表。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取用户或机器人所在的群列表](https://open.feishu.cn/document/server-docs/group/chat/list)

        Examples:
            >>> await client.im.chats.list()  # doctest:+SKIP
            [{'chat_id': 'oc_1', ...}, {'chat_id': 'oc_2', ...}]  # noqa: E501
        """
        return await self._client.paginate_get(
            "im/v1/chats",
            params={"user_id_type": user_id_type},
            page_size=page_size,
            max_items=max_items,
        )

    async def list_members(
        self, chat_id: str, *, member_id_type: str = "open_id", page_size: int = 50, max_items: int | None = None
    ) -> builtins.list[NestedDict]:
        r"""
        获取群成员列表。

        自动翻页并将各页结果拼接为单个列表返回。`page_size` 会被限制在
        [feishu.consts.MAX_PAGE_SIZE][] 以内。

        Args:
            chat_id: 群 ID。
            member_id_type: 成员 ID 类型，默认为 `open_id`。
            page_size: 每页条数，默认为 50，超过上限时按上限截断。
            max_items: 最多返回的条数；为空表示返回全部。

        Returns:
            群成员数据列表。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取群成员列表](https://open.feishu.cn/document/server-docs/group/chat-member/get)

        Examples:
            >>> await client.im.chats.list_members("oc_chat1")  # doctest:+SKIP
            [{'member_id': 'ou_1', ...}, {'member_id': 'ou_2', ...}]  # noqa: E501
        """
        return await self._client.paginate_get(
            f"im/v1/chats/{quote_segment(chat_id)}/members",
            params={"member_id_type": member_id_type},
            page_size=page_size,
            max_items=max_items,
        )

    async def remove_members(
        self, chat_id: str, id_list: builtins.list[str], *, member_id_type: str = "open_id"
    ) -> NestedDict:
        r"""
        将用户或机器人移出群组。

        Args:
            chat_id: 群 ID。
            id_list: 要移除的成员 ID 列表。
            member_id_type: 成员 ID 类型，默认为 `open_id`。

        Returns:
            接口返回的数据，可能包含无效或失败的成员 ID 信息。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [将用户或机器人移出群聊](https://open.feishu.cn/document/server-docs/group/chat-member/delete)

        Examples:
            >>> await client.im.chats.remove_members("oc_chat1", ["ou_user1"])  # doctest:+SKIP
            {'invalid_id_list': [], ...}  # noqa: E501
        """
        return await self._request_data(
            "DELETE",
            f"im/v1/chats/{quote_segment(chat_id)}/members",
            params={"member_id_type": member_id_type},
            json={"id_list": id_list},
        )

    async def update(
        self,
        chat_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        user_id_type: str = "open_id",
        **opts: Any,
    ) -> NestedDict:
        r"""
        更新群信息。

        仅将显式传入的字段写入请求体，未设置的字段会被省略。额外的关键字参数（`opts`）中值为
        `None` 的项也会被忽略。

        Args:
            chat_id: 群 ID。
            name: 新的群名称。
            description: 新的群描述。
            user_id_type: 用户 ID 类型，默认为 `open_id`。
            **opts: 其他可更新字段，例如 `avatar`、`owner_id` 等；值为 `None` 时忽略。

        Returns:
            更新后的群组数据。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [更新群信息](https://open.feishu.cn/document/server-docs/group/chat/update)

        Examples:
            >>> await client.im.chats.update("oc_chat1", name="New Name")  # doctest:+SKIP
            {'chat_id': 'oc_chat1', ...}  # noqa: E501
        """
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        body.update({k: v for k, v in opts.items() if v is not None})
        return await self._request_data(
            "PUT", f"im/v1/chats/{quote_segment(chat_id)}", params={"user_id_type": user_id_type}, json=body
        )
