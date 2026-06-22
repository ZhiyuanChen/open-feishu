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

from typing import Any

from chanfig import NestedDict

from .._namespace import Namespace
from .._url import quote_segment


class TablesNamespace(Namespace):
    r"""
    多维表格数据表（table）接口命名空间。

    通过 `client.bitable.tables` 访问，封装数据表相关的服务端接口，包括数据表的列举、创建与删除。
    数据表归属于某个多维表格应用，常以 `table_id` 标识。

    通常无需直接实例化，应通过 `client.bitable.tables` 访问。

    飞书文档:
        [列出数据表](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table/list)
    """

    async def create(self, app_token: str, table: dict[str, Any]) -> NestedDict:
        r"""
        新增一张数据表。

        `table` 是描述待创建数据表的请求体，原样作为 `table` 字段并入请求体，常见键包括
        `name`、`default_view_name`、`fields` 等。

        Args:
            app_token: 多维表格的唯一标识 `app_token`。
            table: 数据表定义对象，例如 `{"name": "新表", "fields": [...]}`。

        Returns:
            创建结果数据，含新建数据表的 `table_id`，以及（视请求而定）`default_view_id`、
            `field_id_list` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [新增数据表](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table/create)

        Examples:
            >>> await client.bitable.tables.create("bascnxxx", {"name": "Tasks"})  # doctest:+SKIP
            {'table_id': 'tblcnnew', 'default_view_id': 'vewxxx', ...}  # noqa: E501
        """
        return await self._request_data(
            "POST", f"bitable/v1/apps/{quote_segment(app_token)}/tables", json={"table": table}
        )

    async def delete(self, app_token: str, table_id: str) -> NestedDict:
        r"""
        删除一张数据表。

        Args:
            app_token: 多维表格的唯一标识 `app_token`。
            table_id: 待删除数据表的 `table_id`。

        Returns:
            接口返回的数据（通常为空）。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [删除数据表](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table/delete)

        Examples:
            >>> await client.bitable.tables.delete("bascnxxx", "tblcn1")  # doctest:+SKIP
            {}
        """
        return await self._request_data(
            "DELETE", f"bitable/v1/apps/{quote_segment(app_token)}/tables/{quote_segment(table_id)}"
        )

    async def list(self, app_token: str, *, page_size: int = 50, max_items: int | None = None) -> list[NestedDict]:
        r"""
        列出多维表格下的数据表。

        自动翻页并将各页结果拼接为单个列表返回。`page_size` 会被限制在
        [feishu.consts.MAX_PAGE_SIZE][] 以内。

        Args:
            app_token: 多维表格的唯一标识 `app_token`。
            page_size: 每页条数，默认为 50，超过上限时按上限截断。
            max_items: 最多返回的条数；为空表示返回全部。

        Returns:
            数据表数据列表，每项包含 `table_id`、`name`、`revision` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [列出数据表](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table/list)

        Examples:
            >>> await client.bitable.tables.list("bascnxxx")  # doctest:+SKIP
            [{'table_id': 'tblcn1', 'name': 'Sheet1', ...}, {'table_id': 'tblcn2', ...}]  # noqa: E501
        """
        return await self._client.paginate_get(
            f"bitable/v1/apps/{quote_segment(app_token)}/tables",
            page_size=page_size,
            max_items=max_items,
        )
