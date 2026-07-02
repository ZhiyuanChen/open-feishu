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

from collections.abc import Iterable

from chanfig import NestedDict

from .._namespace import Namespace


class UsersNamespace(Namespace):
    r"""
    邮箱地址状态查询命名空间。

    通过 `client.mail.users` 访问，封装 `mail/v1/users/query`，用于查询邮箱地址对应的类型与状态。

    飞书文档:
        [查询邮箱地址状态](https://open.feishu.cn/document/server-docs/mail-v1/user/query)
    """

    async def query(self, email_list: Iterable[str]) -> NestedDict:
        r"""
        查询邮箱地址状态。

        Args:
            email_list: 待查询的邮箱地址列表。

        Returns:
            飞书返回的 `data` 数据体，其中 `user_list` 给出邮箱地址、状态与类型。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [查询邮箱地址状态](https://open.feishu.cn/document/server-docs/mail-v1/user/query)

        Examples:
            >>> await client.mail.users.query(["ops@example.com"])  # doctest:+SKIP
            {'user_list': [{'email': 'ops@example.com', ...}]}
        """
        return await self._request_data("POST", "mail/v1/users/query", json={"email_list": list(email_list)})
