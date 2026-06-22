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

from typing import TYPE_CHECKING, Any

from chanfig import NestedDict

from ._envelope import _data

if TYPE_CHECKING:
    from .client import FeishuClient


class Namespace:
    r"""
    所有业务命名空间的基类。

    持有对 [feishu.client.FeishuClient][] 的引用，子类通过 `self._client` 发起请求。
    通常无需直接实例化，应通过 `client.<namespace>` 惰性访问对应的命名空间。

    Args:
        client: 绑定的飞书客户端。
    """

    def __init__(self, client: FeishuClient) -> None:
        self._client = client

    async def _request_data(self, method: str, path: str, **kwargs: Any) -> NestedDict:
        r"""发起一次请求并返回其 `data` 数据体，等价于 `_data(await self._client.request(...))`。"""
        return _data(await self._client.request(method, path, **kwargs))
