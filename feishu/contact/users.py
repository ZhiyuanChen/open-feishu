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
from collections.abc import Iterable
from typing import Any

from chanfig import NestedDict

from .._envelope import _data
from .._namespace import Namespace
from .._url import quote_segment


class UsersNamespace(Namespace):
    r"""
    用户接口命名空间。

    通过 `client.contact.users` 访问，封装飞书通讯录中用户相关的服务端接口，包括查询、批量查询、
    创建、更新与删除用户等能力。读取方法返回飞书原始数据体；如需规整结构，可由调用方显式套用
    [feishu.contact.normalize.normalize_user][]。

    通常无需直接实例化，应通过 `client.contact.users` 访问。

    飞书文档:
        [通讯录 / 用户](https://open.feishu.cn/document/server-docs/contact-v3/user/field-overview)
    """

    async def batch_get(
        self,
        user_ids: Iterable[str],
        *,
        user_id_type: str = "open_id",
        department_id_type: str = "open_department_id",
    ) -> builtins.list[NestedDict]:
        r"""
        通过用户 ID 批量获取用户信息。

        `user_ids` 按 `user_id_type` 解释（`open_id` / `union_id` / `user_id`），作为重复的查询参数发送。
        飞书限制单次请求最多 50 个 ID，超过时直接抛出 [ValueError][] 而不发起注定失败的请求。各字段是否被
        填充取决于应用所申请的数据权限（邮箱、手机号、部门、员工信息等字段级权限）。

        Args:
            user_ids: 用户 ID 列表，单次最多 50 个。
            user_id_type: 用户 ID 类型，可选 `open_id`、`union_id`、`user_id`。默认为 `open_id`。
            department_id_type: 部门 ID 类型，可选 `open_department_id`、`department_id`。默认为 `open_department_id`。

        Returns:
            飞书原始用户对象列表（`data.items`）。如需规整结构，可对每项套用
            [feishu.contact.normalize.normalize_user][]。

        Raises:
            ValueError: 当传入的用户 ID 超过 50 个时抛出。
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [批量获取用户信息](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/contact-v3/user/batch)

        Examples:
            >>> users = await client.contact.users.batch_get(["u1", "u2"])  # doctest: +SKIP
            >>> [u["name"] for u in users]  # doctest: +SKIP
            ['Bob', 'Amy']
        """
        user_ids = list(user_ids)
        if len(user_ids) > 50:
            raise ValueError(f"batch_get accepts at most 50 user_ids per call, got {len(user_ids)}")
        params = {
            "user_ids": user_ids,
            "user_id_type": user_id_type,
            "department_id_type": department_id_type,
        }
        envelope = await self._client.request("GET", "contact/v3/users/batch", params=params)
        return [NestedDict(u) for u in _data(envelope)["items"]]

    async def batch_get_id(
        self,
        *,
        emails: builtins.list[str] | None = None,
        mobiles: builtins.list[str] | None = None,
        include_resigned: bool = False,
    ) -> NestedDict:
        r"""
        通过邮箱或手机号批量查询用户 ID。

        Args:
            emails: 待查询的邮箱列表。默认为 `None`。
            mobiles: 待查询的手机号列表。默认为 `None`。
            include_resigned: 是否在结果中包含离职用户。默认为 `False`。

        Returns:
            飞书返回的 `data` 数据体，其中 `user_list` 给出每个邮箱/手机号对应的用户 ID。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [通过手机号或邮箱获取用户 ID](https://open.feishu.cn/document/server-docs/contact-v3/user/batch_get_id)

        Examples:
            >>> data = await client.contact.users.batch_get_id(emails=["alice@example.com"])  # doctest: +SKIP
            >>> data["user_list"]  # doctest: +SKIP
            [{'email': 'alice@example.com', 'user_id': 'u1'}]
        """
        body = {"emails": emails or [], "mobiles": mobiles or [], "include_resigned": include_resigned}
        return await self._request_data("POST", "contact/v3/users/batch_get_id", json=body)

    async def create(
        self,
        user: dict[str, Any],
        *,
        user_id_type: str | None = None,
        department_id_type: str | None = None,
    ) -> NestedDict:
        r"""
        创建用户。

        将 `user` 作为请求体发送至创建用户接口。仅在显式传入时附带 `user_id_type` /
        `department_id_type` 查询参数，未设置时省略。

        Args:
            user: 用户数据，作为请求体发送。
            user_id_type: 用户 ID 类型，可选 `open_id`、`union_id`、`user_id`；为空时省略该查询参数。
            department_id_type: 部门 ID 类型，可选 `open_department_id`、`department_id`；为空时省略该查询参数。

        Returns:
            飞书返回的 `data` 数据体，包含新建用户的信息。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [创建用户](https://open.feishu.cn/document/server-docs/contact-v3/user/create)

        Examples:
            >>> created = await client.contact.users.create({"name": "Bob", "department_ids": ["0"]})  # doctest: +SKIP
            >>> created["user"]["user_id"]  # doctest: +SKIP
            'u1'
        """
        params: dict[str, Any] = {}
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        if department_id_type is not None:
            params["department_id_type"] = department_id_type
        return await self._request_data("POST", "contact/v3/users", params=params or None, json=user)

    async def delete(self, user_id: str, *, user_id_type: str | None = None) -> NestedDict:
        r"""
        删除用户（离职）。

        仅在显式传入时附带 `user_id_type` 查询参数，未设置时省略。

        Args:
            user_id: 用户 ID。
            user_id_type: 用户 ID 类型，可选 `open_id`、`union_id`、`user_id`；为空时省略该查询参数。

        Returns:
            飞书返回的 `data` 数据体（通常为空）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [删除用户](https://open.feishu.cn/document/server-docs/contact-v3/user/delete)

        Examples:
            >>> await client.contact.users.delete("u1")  # doctest: +SKIP
            {}
        """
        params: dict[str, Any] = {}
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        return await self._request_data("DELETE", f"contact/v3/users/{quote_segment(user_id)}", params=params or None)

    async def get(
        self, user_id: str, *, user_id_type: str = "open_id", department_id_type: str = "open_department_id"
    ) -> NestedDict:
        r"""
        获取单个用户信息。

        Args:
            user_id: 用户 ID。
            user_id_type: 用户 ID 类型，可选 `open_id`、`union_id`、`user_id`。默认为 `open_id`。
            department_id_type: 部门 ID 类型，可选 `open_department_id`、`department_id`。默认为 `open_department_id`。

        Returns:
            飞书返回的 `data` 数据体，其中 `user` 为原始用户对象。如需规整结构，可对 `data["user"]`
            套用 [feishu.contact.normalize.normalize_user][]。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取单个用户信息](https://open.feishu.cn/document/server-docs/contact-v3/user/get)

        Examples:
            >>> data = await client.contact.users.get("u1")  # doctest: +SKIP
            >>> data["user"]["name"]  # doctest: +SKIP
            'Bob'
        """
        return await self._request_data(
            "GET",
            f"contact/v3/users/{quote_segment(user_id)}",
            params={"user_id_type": user_id_type, "department_id_type": department_id_type},
        )

    async def list(
        self,
        department_id: str = "0",
        *,
        user_id_type: str = "open_id",
        department_id_type: str = "open_department_id",
        page_size: int = 50,
        max_items: int | None = None,
    ) -> builtins.list[NestedDict]:
        r"""
        获取部门下的用户列表。

        基于 `find_by_department` 接口自动翻页并将每一页的原始结果汇总返回。单次请求的 `page_size` 受
        飞书限制，超过 50 时会被收敛为 50。

        Args:
            department_id: 部门 ID。默认为 `"0"`，即根部门。
            user_id_type: 用户 ID 类型，可选 `open_id`、`union_id`、`user_id`。默认为 `open_id`。
            department_id_type: 部门 ID 类型，可选 `open_department_id`、`department_id`。默认为 `open_department_id`。
            page_size: 每页数量，最大为 50。默认为 50。
            max_items: 最多返回的用户数量，`None` 表示不限制。默认为 `None`。

        Returns:
            飞书原始用户对象列表。如需规整结构，可对每项套用
            [feishu.contact.normalize.normalize_user][]。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取部门直属用户列表](https://open.feishu.cn/document/server-docs/contact-v3/user/find_by_department)

        Examples:
            >>> users = await client.contact.users.list(department_id="od-1")  # doctest: +SKIP
            >>> [u["user_id"] for u in users]  # doctest: +SKIP
            ['u1', 'u2']
        """

        return await self._client.paginate_get(
            "contact/v3/users/find_by_department",
            params={
                "department_id": department_id,
                "user_id_type": user_id_type,
                "department_id_type": department_id_type,
            },
            page_size=page_size,
            max_items=max_items,
        )

    async def search(
        self,
        query: str,
        *,
        page_size: int = 20,
        max_items: int | None = None,
    ) -> builtins.list[NestedDict]:
        r"""
        按关键词搜索用户。

        在调用方所在组织内按 `query` 搜索用户并自动翻页汇总结果，无法搜索组织外用户或离职用户。
        该接口仅支持以 `user_access_token` 调用。

        Args:
            query: 搜索关键词。
            page_size: 每页数量。默认为 20；超过 [feishu.consts.MAX_PAGE_SIZE][] 时由客户端收敛。
            max_items: 最多返回的用户数量，`None` 表示不限制。默认为 `None`。

        Returns:
            飞书原始用户对象列表（`data.users`），每项通常包含 `open_id`、`name`、`avatar` 等字段；
            无匹配时返回空列表。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [搜索用户](https://open.feishu.cn/document/server-docs/contact-v3/user/search-users)

        Examples:
            >>> me = client.as_user("u-xxx")  # doctest: +SKIP
            >>> users = await me.contact.users.search("Bob")  # doctest: +SKIP
            >>> [u["open_id"] for u in users]  # doctest: +SKIP
            ['ou_1', 'ou_2']
        """
        return await self._client.paginate_get(
            "search/v1/user",
            params={"query": query},
            page_size=page_size,
            max_items=max_items,
            items_key="users",
        )

    async def update(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        user_id_type: str | None = None,
        department_id_type: str | None = None,
    ) -> NestedDict:
        r"""
        更新用户信息。

        以 PATCH 方式将 `user` 作为请求体发送至更新用户接口，仅更新传入的字段。仅在显式传入时附带
        `user_id_type` / `department_id_type` 查询参数，未设置时省略。

        Args:
            user_id: 用户 ID。
            user: 待更新的用户字段，作为请求体发送。
            user_id_type: 用户 ID 类型，可选 `open_id`、`union_id`、`user_id`；为空时省略该查询参数。
            department_id_type: 部门 ID 类型，可选 `open_department_id`、`department_id`；为空时省略该查询参数。

        Returns:
            飞书返回的 `data` 数据体，包含更新后的用户信息。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [修改用户部分信息](https://open.feishu.cn/document/server-docs/contact-v3/user/patch)

        Examples:
            >>> updated = await client.contact.users.update("u1", {"name": "Bobby"})  # doctest: +SKIP
            >>> updated["user"]["name"]  # doctest: +SKIP
            'Bobby'
        """
        params: dict[str, Any] = {}
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        if department_id_type is not None:
            params["department_id_type"] = department_id_type
        return await self._request_data(
            "PATCH", f"contact/v3/users/{quote_segment(user_id)}", params=params or None, json=user
        )
