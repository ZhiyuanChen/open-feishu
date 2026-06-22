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


class AppsNamespace(Namespace):
    r"""
    多维表格应用（app）接口命名空间。

    通过 `client.bitable.apps` 访问，封装多维表格应用相关的服务端接口，目前提供应用元数据的查询。
    多维表格以应用为容器，应用内含若干数据表，常以 `app_token` 标识。

    通常无需直接实例化，应通过 `client.bitable.apps` 访问。

    飞书文档:
        [多维表格概述](https://open.feishu.cn/document/server-docs/docs/bitable-v1/bitable-overview)
    """

    async def get(self, app_token: str) -> NestedDict:
        r"""
        获取多维表格元数据。

        Args:
            app_token: 多维表格的唯一标识 `app_token`。

        Returns:
            包含 `app` 字段的数据，`app` 内含 `app_token`、`name`、`revision`、`is_advanced`
            等字段。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取多维表格元数据](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app/get)

        Examples:
            >>> await client.bitable.apps.get("bascnxxx")  # doctest:+SKIP
            {'app': {'app_token': 'bascnxxx', 'name': 'My Base', 'revision': 12, ...}}  # noqa: E501
        """
        return await self._request_data("GET", f"bitable/v1/apps/{quote_segment(app_token)}")
