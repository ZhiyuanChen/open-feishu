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
from dataclasses import dataclass
from typing import Any

from feishu.agent.context import current_tool_context
from feishu.agent.result import ToolOutcome, ToolResult

_ACCESS_DENIAL_REASONS = {
    "requester identity is unavailable": "无法获取请求者身份",
    "requester user_id is unavailable": "无法从请求者身份解析服务用户名",
}


@dataclass(frozen=True)
class OperationalAccess:
    r"""运营数据工具解析出的可信请求者身份。"""

    username: str
    owner_aliases: frozenset[str]
    identity_aliases: frozenset[str]


async def resolve_operational_access(
    *,
    service: str,
) -> OperationalAccess | ToolResult:
    r"""
    解析当前请求者的服务侧身份。

    运营类工具不接受模型声称的角色、邮箱或用户名；服务侧用户名固定取已验证的 Feishu `user_id`。
    SDK 不维护各上游服务的二次授权名单，真实准入由上游服务或其前置网关决定；SDK 只在本地需要
    owner 过滤时使用这个已验证的 `user_id`。
    """

    tool_context = current_tool_context()
    user = tool_context.requesting_user()
    if not user:
        return access_blocked(service, "requester identity is unavailable")
    username = service_username(user)
    if not username:
        return access_blocked(service, "requester user_id is unavailable")
    principal_identities = verified_principal_identities(user)
    owner = owner_aliases(username)
    return OperationalAccess(username=username, owner_aliases=owner, identity_aliases=principal_identities)


def access_blocked(service: str, reason: str) -> ToolResult:
    r"""构造统一的敏感运营数据拒绝结果。"""

    message = _ACCESS_DENIAL_REASONS.get(reason, reason)
    return ToolResult(
        ToolOutcome.BLOCKED,
        content=f"{service} 访问被拒绝：{message}",
        is_error=True,
    )


def verified_principal_identities(
    user: Mapping[str, Any],
) -> frozenset[str]:
    r"""返回只能由运行时身份解析得到的 principal 标识；不包含模型可声称的身份。"""

    identities = [
        text(user.get(key)) for key in ("open_id", "union_id", "user_id", "internal_id", "ad_id", "employee_id")
    ]
    return frozenset(item for value in identities if (item := normalize_identity(value)))


def service_username(user: Mapping[str, Any]) -> str:
    r"""按组织规则解析服务账号用户名：只使用 Feishu `user_id`。"""

    return text(user.get("user_id"))


def owner_aliases(username: str) -> frozenset[str]:
    r"""返回服务用户名的可匹配 owner 标识；只做精确归一化，不从邮箱推导 local-part。"""

    normalized = normalize_identity(username)
    return frozenset({normalized}) if normalized else frozenset()


def normalize_identity(value: str) -> str:
    r"""归一化身份字符串用于匹配。"""

    return value.strip().lower()


def bool_config(value: Any, *, default: bool = False) -> bool:
    r"""解析配置中的布尔值。"""

    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def text(value: Any) -> str:
    r"""把任意值转成去空白字符串，空值返回空字符串。"""

    return str(value).strip() if value not in (None, "") else ""


__all__ = [
    "OperationalAccess",
    "access_blocked",
    "bool_config",
    "normalize_identity",
    "owner_aliases",
    "resolve_operational_access",
    "text",
]
