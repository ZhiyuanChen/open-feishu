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

from typing import TYPE_CHECKING

from .._namespace import Namespace

if TYPE_CHECKING:
    from .departments import DepartmentsNamespace
    from .users import UsersNamespace


class ContactNamespace(Namespace):
    r"""
    通讯录接口命名空间。

    通过 `client.contact` 访问，作为用户与部门两个子命名空间的入口：
    [`ContactNamespace.users`][feishu.contact.contact.ContactNamespace.users] 暴露用户相关能力，
    [`ContactNamespace.departments`][feishu.contact.contact.ContactNamespace.departments] 暴露部门相关能力。
    两个子命名空间均在首次访问时惰性创建。

    通常无需直接实例化，应通过 `client.contact` 访问。

    飞书文档:
        [服务端 API / 通讯录](https://open.feishu.cn/document/server-docs/contact-v3/resources)
    """

    _departments: DepartmentsNamespace | None = None
    _users: UsersNamespace | None = None

    @property
    def departments(self) -> DepartmentsNamespace:
        r"""
        部门接口命名空间。

        惰性创建并返回 [feishu.contact.departments.DepartmentsNamespace][]，用于查询部门、遍历子部门、
        展开上级部门链，以及创建、更新与删除部门。

        Returns:
            部门接口命名空间实例。

        飞书文档:
            [通讯录 / 部门](https://open.feishu.cn/document/server-docs/contact-v3/department/field-overview)

        Examples:
            >>> client.contact.departments  # doctest:+SKIP
            <feishu.contact.departments.DepartmentsNamespace object at ...>
        """
        if self._departments is None:
            from .departments import DepartmentsNamespace

            self._departments = DepartmentsNamespace(self._client)
        return self._departments

    @property
    def users(self) -> UsersNamespace:
        r"""
        用户接口命名空间。

        惰性创建并返回 [feishu.contact.users.UsersNamespace][]，用于查询、批量查询、创建、更新与删除用户。

        Returns:
            用户接口命名空间实例。

        飞书文档:
            [通讯录 / 用户](https://open.feishu.cn/document/server-docs/contact-v3/user/field-overview)

        Examples:
            >>> client.contact.users  # doctest:+SKIP
            <feishu.contact.users.UsersNamespace object at ...>
        """
        if self._users is None:
            from .users import UsersNamespace

            self._users = UsersNamespace(self._client)
        return self._users
