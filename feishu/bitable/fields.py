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

from chanfig import NestedDict

from .._namespace import Namespace
from .._url import quote_segment


class FieldsNamespace(Namespace):
    r"""
    多维表格字段（field）接口命名空间。

    通过 `client.bitable.fields` 访问，封装字段相关的服务端接口，目前提供数据表字段的列举。
    字段定义数据表的结构，归属于某张数据表，常以 `field_id` 标识。

    通常无需直接实例化，应通过 `client.bitable.fields` 访问。

    飞书文档:
        [列出字段](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-field/list)
    """

    async def list(
        self, app_token: str, table_id: str, *, page_size: int = 50, max_items: int | None = None
    ) -> list[NestedDict]:
        r"""
        列出数据表的字段。

        自动翻页并将各页结果拼接为单个列表返回。`page_size` 会被限制在
        [feishu.consts.MAX_PAGE_SIZE][] 以内。

        Args:
            app_token: 多维表格的唯一标识 `app_token`。
            table_id: 数据表的 `table_id`。
            page_size: 每页条数，默认为 50，超过上限时按上限截断。
            max_items: 最多返回的条数；为空表示返回全部。

        Returns:
            字段数据列表，每项包含 `field_id`、`field_name`、`type`、`property` 等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [列出字段](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-field/list)

        Examples:
            >>> await client.bitable.fields.list("bascnxxx", "tblcn1")  # doctest:+SKIP
            [{'field_id': 'fldcn1', 'field_name': 'Title', 'type': 1, ...}, ...]  # noqa: E501
        """
        return await self._client.paginate_get(
            f"bitable/v1/apps/{quote_segment(app_token)}/tables/{quote_segment(table_id)}/fields",
            page_size=page_size,
            max_items=max_items,
        )
