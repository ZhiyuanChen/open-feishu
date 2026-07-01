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

from typing import Any, TypedDict


class NormalizedUser(TypedDict):
    r"""
    归一化后的飞书用户信息。

    字段:
        user_id: 飞书用户 ID。
        open_id: 飞书 Open ID。
        union_id: 飞书 Union ID。
        name: 用户展示名。
        email: 用户邮箱；接口未返回时为 `None`。
        department_ids: 用户所属部门 ID 列表。
        status: 飞书返回的用户状态对象。
        active: 用户是否处于启用状态。
        raw: 原始用户对象，便于调用方读取 SDK 未归一化的字段。
    """

    user_id: str
    open_id: str
    union_id: str
    name: str
    email: str | None
    department_ids: list[str]
    status: dict[str, Any]
    active: bool
    raw: Any


class NormalizedDepartment(TypedDict):
    r"""
    归一化后的飞书部门信息。

    字段:
        department_id: 部门 ID。
        open_department_id: 部门 Open ID。
        parent_department_id: 父部门 ID。
        name: 部门名称。
        member_count: 部门成员数量。
        raw: 原始部门对象，便于调用方读取 SDK 未归一化的字段。
    """

    department_id: str
    open_department_id: str
    parent_department_id: str
    name: str
    member_count: int
    raw: Any
