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
from typing import Any

from chanfig import NestedDict

from .._namespace import Namespace

_MAX_EMPLOYEE_IDS = 100


class EmployeesNamespace(Namespace):
    r"""
    飞书人事 Directory 员工接口。

    通过 `client.directory.employees` 访问。与通讯录 `client.contact.users` 不同，本命名空间
    调用 `directory/v1` 员工实体，可按需拉取自定义字段；SDK 不解释字段含义，也不按邮箱过滤。

    飞书文档:
        [批量获取员工信息](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/directory-v1/employee/mget)
    """

    async def mget(
        self,
        employee_ids: Iterable[str],
        *,
        required_fields: Iterable[str],
        employee_id_type: str | None = None,
        department_id_type: str | None = None,
    ) -> NestedDict:
        r"""
        按员工 ID 批量获取员工详情。

        `employee_ids` 的解释方式由 `employee_id_type` 决定。飞书限制单次最多 100 个 ID；
        超过时直接抛出 [ValueError][]。`required_fields` 原样发给飞书，例如
        `base_info.employee_id`、`base_info.custom_field_values`。

        Args:
            employee_ids: 员工 ID 列表，单次最多 100 个。
            required_fields: 需要返回的字段路径列表。
            employee_id_type: 员工 ID 类型，如 `open_id`、`user_id`、`employee_id`；为空时省略。
            department_id_type: 部门 ID 类型；为空时省略。

        Returns:
            飞书返回的 `data` 数据体，通常含 `employees` 与 `abnormals`。

        Raises:
            ValueError: 当传入的员工 ID 超过 100 个时抛出。
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [批量获取员工信息](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/directory-v1/employee/mget)
        """
        ids = list(employee_ids)
        if len(ids) > _MAX_EMPLOYEE_IDS:
            raise ValueError(f"mget accepts at most {_MAX_EMPLOYEE_IDS} employee_ids per call, got {len(ids)}")
        params: dict[str, Any] = {}
        if employee_id_type is not None:
            params["employee_id_type"] = employee_id_type
        if department_id_type is not None:
            params["department_id_type"] = department_id_type
        body = {
            "employee_ids": ids,
            "required_fields": list(required_fields),
        }
        return await self._request_data(
            "POST",
            "directory/v1/employees/mget",
            params=params or None,
            json=body,
        )


__all__ = ["EmployeesNamespace"]
