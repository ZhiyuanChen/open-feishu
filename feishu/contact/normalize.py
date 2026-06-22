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

from collections.abc import Mapping
from typing import Any

from chanfig import NestedDict


def get_user_email(user: Mapping[str, Any], *, prefer_enterprise: bool = True) -> str | None:
    r"""
    从用户数据中提取邮箱地址。

    用户可能同时拥有企业邮箱（`enterprise_email`）与个人邮箱（`email`）。当 `prefer_enterprise`
    为 `True`（默认）时优先返回企业邮箱，否则优先返回个人邮箱；任一字段缺失或为空时回退到另一字段，
    两者皆无则返回 `None`。

    Args:
        user: 用户数据，通常为飞书返回的原始用户对象。
        prefer_enterprise: 是否优先返回企业邮箱。默认为 `True`。

    Returns:
        邮箱地址，若均不存在则为 `None`。

    Examples:
        >>> user = {"email": "alice@corp.com", "enterprise_email": "alice@ent.com"}
        >>> get_user_email(user)
        'alice@ent.com'
        >>> get_user_email(user, prefer_enterprise=False)
        'alice@corp.com'
        >>> get_user_email({}) is None
        True
    """
    enterprise = user.get("enterprise_email") or None
    personal = user.get("email") or None
    if prefer_enterprise:
        return enterprise or personal
    return personal or enterprise


def get_user_status(user: Mapping[str, Any]) -> NestedDict:
    r"""
    提取用户状态对象。

    返回用户的 `status` 字段并统一封装为 [`NestedDict`][chanfig.NestedDict]，便于以属性或缺省索引方式
    读取 `is_activated`、`is_frozen`、`is_resigned` 等子字段。当字段缺失时返回空的 `NestedDict`。

    Args:
        user: 用户数据，通常为飞书返回的原始用户对象。

    Returns:
        用户状态对象。

    Examples:
        >>> get_user_status({"status": {"is_activated": True}})["is_activated"]
        True
        >>> get_user_status({}).dict()
        {}
    """
    status = user.get("status") or {}
    return status if isinstance(status, NestedDict) else NestedDict(status)


def is_active_user(user: Mapping[str, Any]) -> bool:
    r"""
    判断用户是否为在职可用状态。

    仅当用户已激活（`is_activated`）且未被冻结（`is_frozen`）、未离职（`is_resigned`）时返回 `True`。

    Args:
        user: 用户数据，通常为飞书返回的原始用户对象。

    Returns:
        用户是否在职可用。

    Examples:
        >>> is_active_user({"status": {"is_activated": True, "is_frozen": False, "is_resigned": False}})
        True
        >>> is_active_user({"status": {"is_activated": True, "is_frozen": True}})
        False
        >>> is_active_user({})
        False
    """
    status = get_user_status(user)
    return bool(status.get("is_activated")) and not status.get("is_frozen") and not status.get("is_resigned")


def get_user_department_ids(user: Mapping[str, Any]) -> list[str]:
    r"""
    提取用户所属部门的 ID 列表。

    返回用户 `department_ids` 字段的副本；字段缺失或为空时返回空列表。

    Args:
        user: 用户数据，通常为飞书返回的原始用户对象。

    Returns:
        部门 ID 列表。

    Examples:
        >>> get_user_department_ids({"department_ids": ["od-1", "od-2"]})
        ['od-1', 'od-2']
        >>> get_user_department_ids({})
        []
    """
    return list(user.get("department_ids") or [])


def get_user_identity(user: Mapping[str, Any]) -> NestedDict:
    r"""
    提取用户的三种身份标识。

    返回仅包含 `user_id`、`open_id`、`union_id` 的 [`NestedDict`][chanfig.NestedDict]，缺失的字段以
    `None` 占位，使调用方无需担心 `KeyError` 即可索引任一标识。

    Args:
        user: 用户数据，通常为飞书返回的原始用户对象。

    Returns:
        包含 `user_id`、`open_id`、`union_id` 的身份标识对象。

    Examples:
        >>> identity = get_user_identity({"user_id": "u1", "open_id": "ou_1", "union_id": "on_1"})
        >>> identity["user_id"], identity["open_id"], identity["union_id"]
        ('u1', 'ou_1', 'on_1')
        >>> get_user_identity({"user_id": "u1"})["open_id"] is None
        True
    """
    return NestedDict({k: user.get(k) for k in ("user_id", "open_id", "union_id")})


def normalize_user(user: Mapping[str, Any]) -> NestedDict:
    r"""
    将飞书原始用户数据规整为统一结构。

    提取常用字段（身份标识、姓名、邮箱、部门、状态）并派生 `active` 在职标志，同时在 `raw` 字段下
    保留完整的原始数据，便于调用方读取未规整的字段。邮箱通过 [feishu.contact.normalize.get_user_email][]
    解析，在职标志通过 [feishu.contact.normalize.is_active_user][] 派生。

    部分字段是否被填充取决于应用所申请的数据权限（邮箱、手机号、部门、员工信息等字段级权限）。

    Args:
        user: 飞书返回的原始用户对象。

    Returns:
        规整后的用户对象，包含 `user_id`、`open_id`、`union_id`、`name`、`email`、`department_ids`、
        `status`、`active` 及原始数据 `raw`。

    飞书文档:
        [获取单个用户信息](https://open.feishu.cn/document/server-docs/contact-v3/user/get)

    Examples:
        >>> user = normalize_user(
        ...     {
        ...         "user_id": "u1",
        ...         "open_id": "ou_1",
        ...         "name": "Alice",
        ...         "enterprise_email": "alice@ent.com",
        ...         "department_ids": ["od-1"],
        ...         "status": {"is_activated": True, "is_frozen": False, "is_resigned": False},
        ...     }
        ... )
        >>> user["name"], user["email"], user["active"]
        ('Alice', 'alice@ent.com', True)
        >>> user["raw"]["enterprise_email"]
        'alice@ent.com'
    """
    return NestedDict(
        {
            "user_id": user.get("user_id"),
            "open_id": user.get("open_id"),
            "union_id": user.get("union_id"),
            "name": user.get("name"),
            "email": get_user_email(user),
            "department_ids": get_user_department_ids(user),
            "status": get_user_status(user),
            "active": is_active_user(user),
            "raw": user,
        }
    )


def normalize_department(dept: Mapping[str, Any]) -> NestedDict:
    r"""
    将飞书原始部门数据规整为统一结构。

    提取部门 ID、上级部门 ID、名称与成员数等常用字段，缺失的字段以 `None` 占位，并在 `raw` 字段下
    保留完整的原始数据。

    Args:
        dept: 飞书返回的原始部门对象。

    Returns:
        规整后的部门对象，包含 `department_id`、`open_department_id`、`parent_department_id`、`name`、
        `member_count` 及原始数据 `raw`。

    飞书文档:
        [获取单个部门信息](https://open.feishu.cn/document/server-docs/contact-v3/department/get)

    Examples:
        >>> dept = normalize_department(
        ...     {
        ...         "department_id": "d1",
        ...         "open_department_id": "od-1",
        ...         "name": "Engineering",
        ...         "member_count": 42,
        ...     }
        ... )
        >>> dept["name"], dept["member_count"], dept["parent_department_id"]
        ('Engineering', 42, None)
        >>> dept["raw"]["department_id"]
        'd1'
    """
    return NestedDict(
        {
            "department_id": dept.get("department_id"),
            "open_department_id": dept.get("open_department_id"),
            "parent_department_id": dept.get("parent_department_id"),
            "name": dept.get("name"),
            "member_count": dept.get("member_count"),
            "raw": dept,
        }
    )
