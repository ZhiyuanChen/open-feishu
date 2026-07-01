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

import builtins  # 'list' is a method here; bare list[...] in earlier annotations resolves to it, so use builtins.list
from typing import Any

from chanfig import NestedDict

from .._namespace import Namespace
from .._url import quote_segment
from ..consts import MAX_PAGE_SIZE
from ..pagination import paginate


class RecordsNamespace(Namespace):
    r"""
    多维表格记录（record）接口命名空间。

    通过 `client.bitable.records` 访问，封装记录相关的服务端接口，包括记录的查询、检索、创建、更新与
    删除，以及对应的批量操作。记录承载数据表中的数据，归属于某张数据表，常以 `record_id` 标识。

    通常无需直接实例化，应通过 `client.bitable.records` 访问。

    飞书文档:
        [记录数据结构](https://open.feishu.cn/document/docs/bitable-v1/app-table-record/bitable-record-data-structure-overview)
    """

    async def batch_create(
        self,
        app_token: str,
        table_id: str,
        records: builtins.list[dict[str, Any]],
        *,
        user_id_type: str | None = None,
    ) -> NestedDict:
        r"""
        批量新增记录。

        `records` 为待新增记录的列表，原样作为 `records` 字段并入请求体，每个元素形如
        `{"fields": {...}}`。

        Args:
            app_token: 多维表格的唯一标识 `app_token`。
            table_id: 数据表的 `table_id`。
            records: 待新增记录列表，每个元素形如 `{"fields": {"Title": "hi"}}`。
            user_id_type: 用户字段的 ID 类型；为空时使用飞书接口默认值。

        Returns:
            批量新增结果数据，含新建的 `records` 列表（每项含 `record_id`、`fields`）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [新增多条记录](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/batch_create)

        Examples:
            >>> recs = [{"fields": {"Title": "a"}}, {"fields": {"Title": "b"}}]
            >>> await client.bitable.records.batch_create("bascnxxx", "tblcn1", recs)  # doctest:+SKIP
            {'records': [{'record_id': 'rec1', ...}, {'record_id': 'rec2', ...}]}  # noqa: E501
        """
        return await self._request_data(
            "POST",
            f"bitable/v1/apps/{quote_segment(app_token)}/tables/{quote_segment(table_id)}/records/batch_create",
            params=_user_id_type_params(user_id_type),
            json={"records": records},
        )

    async def batch_delete(self, app_token: str, table_id: str, record_ids: builtins.list[str]) -> NestedDict:
        r"""
        批量删除记录。

        `record_ids` 为待删除记录的 ID 列表，原样作为 `records` 字段并入请求体。

        Args:
            app_token: 多维表格的唯一标识 `app_token`。
            table_id: 数据表的 `table_id`。
            record_ids: 待删除记录的 `record_id` 列表。

        Returns:
            批量删除结果数据，含 `records` 列表（每项含 `deleted`、`record_id`）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [删除多条记录](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/batch_delete)

        Examples:
            >>> await client.bitable.records.batch_delete("bascnxxx", "tblcn1", ["rec1", "rec2"])  # doctest:+SKIP
            {'records': [{'deleted': True, 'record_id': 'rec1'}, ...]}  # noqa: E501
        """
        return await self._request_data(
            "POST",
            f"bitable/v1/apps/{quote_segment(app_token)}/tables/{quote_segment(table_id)}/records/batch_delete",
            json={"records": record_ids},
        )

    async def batch_update(
        self,
        app_token: str,
        table_id: str,
        records: builtins.list[dict[str, Any]],
        *,
        user_id_type: str | None = None,
    ) -> NestedDict:
        r"""
        批量更新记录。

        `records` 为待更新记录的列表，原样作为 `records` 字段并入请求体，每个元素需携带
        其 `record_id` 与待更新的 `fields`，形如 `{"record_id": "recxxx", "fields": {...}}`。

        Args:
            app_token: 多维表格的唯一标识 `app_token`。
            table_id: 数据表的 `table_id`。
            records: 待更新记录列表，每个元素形如
                `{"record_id": "recxxx", "fields": {"Title": "new"}}`。
            user_id_type: 用户字段的 ID 类型；为空时使用飞书接口默认值。

        Returns:
            批量更新结果数据，含更新后的 `records` 列表（每项含 `record_id`、`fields`）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [更新多条记录](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/batch_update)

        Examples:
            >>> recs = [{"record_id": "rec1", "fields": {"Title": "a"}}]
            >>> await client.bitable.records.batch_update("bascnxxx", "tblcn1", recs)  # doctest:+SKIP
            {'records': [{'record_id': 'rec1', 'fields': {'Title': 'a'}}]}  # noqa: E501
        """
        return await self._request_data(
            "POST",
            f"bitable/v1/apps/{quote_segment(app_token)}/tables/{quote_segment(table_id)}/records/batch_update",
            params=_user_id_type_params(user_id_type),
            json={"records": records},
        )

    async def create(
        self, app_token: str, table_id: str, fields: dict[str, Any], *, user_id_type: str | None = None
    ) -> NestedDict:
        r"""
        新增一条记录。

        `fields` 是记录的字段值映射，原样作为 `fields` 字段并入请求体，键为字段名、值为
        对应类型的字段值。

        Args:
            app_token: 多维表格的唯一标识 `app_token`。
            table_id: 数据表的 `table_id`。
            fields: 记录字段值映射，例如 `{"Title": "hi", "Done": True}`。
            user_id_type: 用户字段的 ID 类型；为空时使用飞书接口默认值。

        Returns:
            包含 `record` 字段的数据，`record` 内含新建记录的 `record_id`、`fields` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [新增记录](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/create)

        Examples:
            >>> await client.bitable.records.create("bascnxxx", "tblcn1", {"Title": "hi"})  # doctest:+SKIP
            {'record': {'record_id': 'recnew', 'fields': {'Title': 'hi'}}}  # noqa: E501
        """
        return await self._request_data(
            "POST",
            f"bitable/v1/apps/{quote_segment(app_token)}/tables/{quote_segment(table_id)}/records",
            params=_user_id_type_params(user_id_type),
            json={"fields": fields},
        )

    async def delete(self, app_token: str, table_id: str, record_id: str) -> NestedDict:
        r"""
        删除一条记录。

        Args:
            app_token: 多维表格的唯一标识 `app_token`。
            table_id: 数据表的 `table_id`。
            record_id: 待删除记录的 `record_id`。

        Returns:
            删除结果数据，含 `deleted`、`record_id` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [删除记录](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/delete)

        Examples:
            >>> await client.bitable.records.delete("bascnxxx", "tblcn1", "recxxx")  # doctest:+SKIP
            {'deleted': True, 'record_id': 'recxxx'}  # noqa: E501
        """
        return await self._request_data(
            "DELETE",
            f"bitable/v1/apps/{quote_segment(app_token)}/tables/{quote_segment(table_id)}"
            f"/records/{quote_segment(record_id)}",
        )

    async def get(
        self, app_token: str, table_id: str, record_id: str, *, user_id_type: str | None = None
    ) -> NestedDict:
        r"""
        获取单条记录。

        Args:
            app_token: 多维表格的唯一标识 `app_token`。
            table_id: 数据表的 `table_id`。
            record_id: 记录的 `record_id`。
            user_id_type: 用户字段的 ID 类型；为空时使用飞书接口默认值。

        Returns:
            包含 `record` 字段的数据，`record` 内含 `record_id`、`fields` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [检索记录](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/get)

        Examples:
            >>> await client.bitable.records.get("bascnxxx", "tblcn1", "recxxx")  # doctest:+SKIP
            {'record': {'record_id': 'recxxx', 'fields': {'Title': 'hi'}}}  # noqa: E501
        """
        return await self._request_data(
            "GET",
            f"bitable/v1/apps/{quote_segment(app_token)}/tables/{quote_segment(table_id)}"
            f"/records/{quote_segment(record_id)}",
            params=_user_id_type_params(user_id_type),
        )

    async def list(
        self,
        app_token: str,
        table_id: str,
        *,
        page_size: int = 50,
        max_items: int | None = None,
        view_id: str | None = None,
        filter: str | None = None,
        sort: str | None = None,
        field_names: str | None = None,
        user_id_type: str | None = None,
    ) -> builtins.list[NestedDict]:
        r"""
        列出数据表的记录。

        自动翻页并将各页结果拼接为单个列表返回。`page_size` 会被限制在
        [feishu.consts.MAX_PAGE_SIZE][] 以内。仅将显式传入的可选查询参数并入请求，
        未设置的项会被省略。

        通过简单的列出接口返回记录，仅支持以查询参数传递 `view_id`、`filter`、`sort`、
        `field_names` 等基础筛选项；若需以结构化的筛选/排序请求体进行更丰富的复杂查询，
        请改用 [feishu.bitable.records.RecordsNamespace.search][]。

        Args:
            app_token: 多维表格的唯一标识 `app_token`。
            table_id: 数据表的 `table_id`。
            page_size: 每页条数，默认为 50，超过上限时按上限截断。
            max_items: 最多返回的条数；为空表示返回全部。
            view_id: 视图 ID，按指定视图的筛选与排序返回记录；为空时省略该参数。
            filter: 筛选条件表达式；为空时省略该参数。
            sort: 排序条件；为空时省略该参数。
            field_names: 指定返回的字段集合；为空时省略该参数。
            user_id_type: 用户字段的 ID 类型；为空时使用飞书接口默认值。

        Returns:
            记录数据列表，每项包含 `record_id`、`fields` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [列出记录](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/list)

        Examples:
            >>> await client.bitable.records.list("bascnxxx", "tblcn1")  # doctest:+SKIP
            [{'record_id': 'recxxx', 'fields': {'Title': 'hi'}}, ...]  # noqa: E501
        """
        params: dict[str, Any] = {}
        if view_id is not None:
            params["view_id"] = view_id
        if filter is not None:
            params["filter"] = filter
        if sort is not None:
            params["sort"] = sort
        if field_names is not None:
            params["field_names"] = field_names
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        return await self._client.paginate_get(
            f"bitable/v1/apps/{quote_segment(app_token)}/tables/{quote_segment(table_id)}/records",
            params=params,
            page_size=page_size,
            max_items=max_items,
        )

    async def search(
        self,
        app_token: str,
        table_id: str,
        body: dict[str, Any],
        *,
        page_size: int = 50,
        max_items: int | None = None,
        user_id_type: str | None = None,
    ) -> builtins.list[NestedDict]:
        r"""
        检索数据表的记录。

        自动翻页并将各页结果拼接为单个列表返回。`page_size` 会被限制在
        [feishu.consts.MAX_PAGE_SIZE][] 以内。`page_token` 与 `page_size` 通过查询参数
        传递，而 `filter`、`sort`、`field_names`、`view_id`、`automatic_fields` 等检索
        条件经 `body` 以 JSON 请求体发送。

        通过检索接口以结构化的筛选/排序请求体进行更丰富的复杂查询；若仅需以查询参数传递
        `view_id`、`filter`、`sort`、`field_names` 等基础筛选项的简单列出，
        请改用 [feishu.bitable.records.RecordsNamespace.list][]。

        Args:
            app_token: 多维表格的唯一标识 `app_token`。
            table_id: 数据表的 `table_id`。
            body: 检索条件请求体，原样作为 JSON 发送，例如
                `{"filter": {...}, "sort": [...], "field_names": [...]}`。
            page_size: 每页条数，默认为 50，超过上限时按上限截断。
            max_items: 最多返回的条数；为空表示返回全部。
            user_id_type: 用户字段的 ID 类型；为空时使用飞书接口默认值。

        Returns:
            记录数据列表，每项包含 `record_id`、`fields` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [检索记录](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/bitable-v1/app-table-record/search)

        Examples:
            >>> await client.bitable.records.search("bascnxxx", "tblcn1", {"field_names": ["Title"]})  # doctest:+SKIP
            [{'record_id': 'recxxx', 'fields': {'Title': 'hi'}}, ...]  # noqa: E501
        """

        async def fetch(page_token: str | None) -> NestedDict:
            params = {
                "page_size": min(page_size, MAX_PAGE_SIZE),
                "page_token": page_token,
            }
            if user_id_type is not None:
                params["user_id_type"] = user_id_type
            return await self._client.request(
                "POST",
                f"bitable/v1/apps/{quote_segment(app_token)}/tables/{quote_segment(table_id)}/records/search",
                params=params,
                json=body,
            )

        return await paginate(fetch, max_items=max_items)

    async def update(
        self,
        app_token: str,
        table_id: str,
        record_id: str,
        fields: dict[str, Any],
        *,
        user_id_type: str | None = None,
    ) -> NestedDict:
        r"""
        更新一条记录。

        `fields` 是记录的字段值映射，原样作为 `fields` 字段并入请求体；仅传入的字段会被
        更新，未传入的字段保持不变。

        Args:
            app_token: 多维表格的唯一标识 `app_token`。
            table_id: 数据表的 `table_id`。
            record_id: 待更新记录的 `record_id`。
            fields: 待更新的字段值映射，例如 `{"Title": "new"}`。
            user_id_type: 用户字段的 ID 类型；为空时使用飞书接口默认值。

        Returns:
            包含 `record` 字段的数据，`record` 内含更新后记录的 `record_id`、`fields` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [更新记录](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/update)

        Examples:
            >>> await client.bitable.records.update("bascnxxx", "tblcn1", "recxxx", {"Title": "new"})  # doctest:+SKIP
            {'record': {'record_id': 'recxxx', 'fields': {'Title': 'new'}}}  # noqa: E501
        """
        return await self._request_data(
            "PUT",
            f"bitable/v1/apps/{quote_segment(app_token)}/tables/{quote_segment(table_id)}"
            f"/records/{quote_segment(record_id)}",
            params=_user_id_type_params(user_id_type),
            json={"fields": fields},
        )


def _user_id_type_params(user_id_type: str | None) -> dict[str, str] | None:
    return {"user_id_type": user_id_type} if user_id_type is not None else None
