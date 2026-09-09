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
    from .employees import EmployeesNamespace


class DirectoryNamespace(Namespace):
    r"""
    飞书人事 Directory 接口入口。

    通过 `client.directory` 访问。员工查询见
    [`DirectoryNamespace.employees`][feishu.directory.directory.DirectoryNamespace.employees]。
    这与通讯录 `client.contact` 是不同的产品面：Directory 提供人事自定义字段，通讯录提供
    组织架构用户对象。

    飞书文档:
        [Directory 员工概述](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/directory-v1/employee/overview)
    """

    _employees: EmployeesNamespace | None = None

    @property
    def employees(self) -> EmployeesNamespace:
        r"""
        员工接口命名空间。

        Returns:
            员工接口命名空间实例。
        """
        if self._employees is None:
            from .employees import EmployeesNamespace

            self._employees = EmployeesNamespace(self._client)
        return self._employees
