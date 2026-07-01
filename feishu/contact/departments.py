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


class DepartmentsNamespace(Namespace):
    r"""
    部门接口命名空间。

    通过 `client.contact.departments` 访问，封装飞书通讯录中部门相关的服务端接口，包括部门信息查询、
    子部门遍历、上级部门链展开，以及创建、更新与删除部门等能力。读取方法返回飞书原始数据体；如需规整
    结构，可由调用方显式套用 [feishu.contact.normalize.normalize_department][]。

    通常无需直接实例化，应通过 `client.contact.departments` 访问。

    飞书文档:
        [通讯录 / 部门](https://open.feishu.cn/document/server-docs/contact-v3/department/field-overview)
    """

    async def create(
        self,
        department: dict[str, Any],
        *,
        department_id_type: str | None = None,
        user_id_type: str | None = None,
    ) -> NestedDict:
        r"""
        创建部门。

        将 `department` 作为请求体发送至创建部门接口。仅在显式传入时附带 `department_id_type` /
        `user_id_type` 查询参数，未设置时省略。

        Args:
            department: 部门数据，作为请求体发送。
            department_id_type: 部门 ID 类型，可选 `open_department_id`、`department_id`；为空时省略该查询参数。
            user_id_type: 用户 ID 类型，可选 `open_id`、`union_id`、`user_id`；为空时省略该查询参数。

        Returns:
            飞书返回的 `data` 数据体，包含新建部门的信息。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [创建部门](https://open.feishu.cn/document/server-docs/contact-v3/department/create)

        Examples:
            >>> dept = {"name": "Eng", "parent_department_id": "0"}
            >>> created = await client.contact.departments.create(dept)  # doctest: +SKIP
            >>> created["department"]["open_department_id"]  # doctest: +SKIP
            'od-1'
        """
        params: dict[str, Any] = {}
        if department_id_type is not None:
            params["department_id_type"] = department_id_type
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        return await self._request_data("POST", "contact/v3/departments", params=params or None, json=department)

    async def delete(self, department_id: str, *, department_id_type: str | None = None) -> NestedDict:
        r"""
        删除部门。

        仅在显式传入时附带 `department_id_type` 查询参数，未设置时省略。

        Args:
            department_id: 部门 ID。
            department_id_type: 部门 ID 类型，可选 `open_department_id`、`department_id`；为空时省略该查询参数。

        Returns:
            飞书返回的 `data` 数据体（通常为空）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [删除部门](https://open.feishu.cn/document/server-docs/contact-v3/department/delete)

        Examples:
            >>> await client.contact.departments.delete("od-1")  # doctest: +SKIP
            {}
        """
        params: dict[str, Any] = {}
        if department_id_type is not None:
            params["department_id_type"] = department_id_type
        return await self._request_data(
            "DELETE", f"contact/v3/departments/{quote_segment(department_id)}", params=params or None
        )

    async def expand_ids(
        self, department_ids: builtins.list[str], *, department_id_type: str = "open_department_id"
    ) -> builtins.list[str]:
        r"""
        将一组部门 ID 展开为包含其全部上级部门的去重列表。

        对每个传入的部门，先保留自身，再追加其由
        [feishu.contact.departments.DepartmentsNamespace.parent_ids][] 解析出的上级部门链；
        最终按首次出现顺序去重，常用于将部门授权范围向上扩展。

        Args:
            department_ids: 起始部门 ID 列表。
            department_id_type: 部门 ID 类型，可选 `open_department_id`、`department_id`。默认为 `open_department_id`。

        Returns:
            包含各起始部门及其所有上级部门的 ID 列表，已按首次出现顺序去重。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取父部门信息](https://open.feishu.cn/document/server-docs/contact-v3/department/parent)

        Examples:
            >>> expanded = await client.contact.departments.expand_ids(["od-a", "od-b"])  # doctest: +SKIP
            >>> expanded  # doctest: +SKIP
            ['od-a', 'od-mid', 'od-root', 'od-b']
        """
        result: builtins.list[str] = []
        for department_id in department_ids:
            result.append(department_id)
            result.extend(await self.parent_ids(department_id, department_id_type=department_id_type))
        seen, unique = set(), []
        for expanded_id in result:
            if expanded_id not in seen:
                seen.add(expanded_id)
                unique.append(expanded_id)
        return unique

    async def get(self, department_id: str, *, department_id_type: str = "open_department_id") -> NestedDict:
        r"""
        获取单个部门信息。

        Args:
            department_id: 部门 ID。
            department_id_type: 部门 ID 类型，可选 `open_department_id`、`department_id`。默认为 `open_department_id`。

        Returns:
            飞书返回的 `data` 数据体，其中 `department` 为原始部门对象。如需规整结构，可对
            `data["department"]` 套用 [feishu.contact.normalize.normalize_department][]。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取单个部门信息](https://open.feishu.cn/document/server-docs/contact-v3/department/get)

        Examples:
            >>> data = await client.contact.departments.get("od-1")  # doctest: +SKIP
            >>> data["department"]["name"]  # doctest: +SKIP
            'Engineering'
        """
        return await self._request_data(
            "GET",
            f"contact/v3/departments/{quote_segment(department_id)}",
            params={"department_id_type": department_id_type},
        )

    async def list(
        self,
        department_id: str = "0",
        *,
        department_id_type: str = "open_department_id",
        fetch_child: bool = True,
        page_size: int = 50,
        max_items: int | None = None,
    ) -> builtins.list[NestedDict]:
        r"""
        获取子部门列表。

        自动翻页并将每一页的原始结果汇总返回。单次请求的 `page_size` 受飞书限制，超过 50 时会被收敛为 50。

        Args:
            department_id: 父部门 ID。默认为 `"0"`，即根部门。
            department_id_type: 部门 ID 类型，可选 `open_department_id`、`department_id`。默认为 `open_department_id`。
            fetch_child: 是否递归获取所有下级部门。默认为 `True`。
            page_size: 每页数量，最大为 50。默认为 50。
            max_items: 最多返回的部门数量，`None` 表示不限制。默认为 `None`。

        Returns:
            飞书原始部门对象列表。如需规整结构，可对每项套用
            [feishu.contact.normalize.normalize_department][]。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取子部门列表](https://open.feishu.cn/document/server-docs/contact-v3/department/children)

        Examples:
            >>> children = await client.contact.departments.list("od-root")  # doctest: +SKIP
            >>> [d["department_id"] for d in children]  # doctest: +SKIP
            ['d1', 'd2']
        """

        return await self._client.paginate_get(
            f"contact/v3/departments/{quote_segment(department_id)}/children",
            params={"department_id_type": department_id_type, "fetch_child": fetch_child},
            page_size=page_size,
            max_items=max_items,
        )

    async def parent(
        self,
        department_id: str,
        *,
        department_id_type: str = "open_department_id",
        page_size: int = 50,
        max_items: int | None = None,
    ) -> builtins.list[NestedDict]:
        r"""
        获取父部门信息。

        自动翻页并返回飞书原始父部门对象列表，按层级由近及远排列。如只需要 ID 链，可使用
        [feishu.contact.departments.DepartmentsNamespace.parent_ids][]。

        Args:
            department_id: 目标部门 ID。
            department_id_type: 部门 ID 类型，可选 `open_department_id`、`department_id`。默认为 `open_department_id`。
            page_size: 每页数量，最大为 50。默认为 50。
            max_items: 最多返回的父部门数量，`None` 表示不限制。默认为 `None`。

        Returns:
            飞书原始父部门对象列表。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取父部门信息](https://open.feishu.cn/document/server-docs/contact-v3/department/parent)

        Examples:
            >>> parents = await client.contact.departments.parent("od-child")  # doctest: +SKIP
            >>> parents[0]["open_department_id"]  # doctest: +SKIP
            'od-parent-1'
        """

        return await self._client.paginate_get(
            "contact/v3/departments/parent",
            params={"department_id": department_id, "department_id_type": department_id_type},
            page_size=page_size,
            max_items=max_items,
        )

    async def parent_ids(
        self, department_id: str, *, department_id_type: str = "open_department_id"
    ) -> builtins.list[str]:
        r"""
        获取指定部门的上级部门 ID 链。

        自动翻页并按从近到远的顺序返回各级上级部门的 ID，优先取 `open_department_id`，缺失时回退到
        `department_id`。

        Args:
            department_id: 目标部门 ID。
            department_id_type: 部门 ID 类型，可选 `open_department_id`、`department_id`。默认为 `open_department_id`。

        Returns:
            上级部门 ID 列表，按层级由近及远排列。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取父部门信息](https://open.feishu.cn/document/server-docs/contact-v3/department/parent)

        Examples:
            >>> ids = await client.contact.departments.parent_ids("od-child")  # doctest: +SKIP
            >>> ids  # doctest: +SKIP
            ['od-parent-1', 'od-parent-2']
        """

        items = await self.parent(department_id, department_id_type=department_id_type)
        return [d.get("open_department_id") or d.get("department_id") for d in items]

    async def update(
        self,
        department_id: str,
        department: dict[str, Any],
        *,
        department_id_type: str | None = None,
        user_id_type: str | None = None,
    ) -> NestedDict:
        r"""
        更新部门信息。

        以 PATCH 方式将 `department` 作为请求体发送至更新部门接口，仅更新传入的字段。仅在显式传入时附带
        `department_id_type` / `user_id_type` 查询参数，未设置时省略。

        Args:
            department_id: 部门 ID。
            department: 待更新的部门字段，作为请求体发送。
            department_id_type: 部门 ID 类型，可选 `open_department_id`、`department_id`；为空时省略该查询参数。
            user_id_type: 用户 ID 类型，可选 `open_id`、`union_id`、`user_id`；为空时省略该查询参数。

        Returns:
            飞书返回的 `data` 数据体，包含更新后的部门信息。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [修改部门部分信息](https://open.feishu.cn/document/server-docs/contact-v3/department/patch)

        Examples:
            >>> updated = await client.contact.departments.update("od-1", {"name": "Platform"})  # doctest: +SKIP
            >>> updated["department"]["name"]  # doctest: +SKIP
            'Platform'
        """
        params: dict[str, Any] = {}
        if department_id_type is not None:
            params["department_id_type"] = department_id_type
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        return await self._request_data(
            "PATCH", f"contact/v3/departments/{quote_segment(department_id)}", params=params or None, json=department
        )
