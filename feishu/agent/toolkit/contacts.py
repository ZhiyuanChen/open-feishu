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

r"""通讯录工具工厂：按关键词搜索用户（读，最小披露）。详见 [feishu.agent.toolkit][]。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..result import ToolOutcome, ToolResult
from ..tools import Tool
from ._base import needs_user_auth, resolve_client

# Zero-trust: cap how many directory entries one search can surface.
_MAX_MATCHES = 5


def find_user(
    *,
    description: str,
    name: str = "find_user",
    locale: str = "zh-CN",
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：在请求方所在组织内按关键词搜索用户，返回一个 [feishu.agent.tools.Tool][]。

    处理函数调用 `client.contact.users.search(query)`（仅支持用户态调用，无法搜索组织外或离职用户）。

    最小披露（zero-trust）：本工具是防「越狱后批量导出组织通讯录」的关键防线，因此——
    强制要求 `query` 非空（拒绝空白查询，避免无差别罗列全员）；每条匹配**只**返回
    `{name, open_id}`，**绝不**返回邮箱、手机号、部门或完整画像；结果上限收敛至
    [feishu.agent.toolkit.contacts._MAX_MATCHES][] 条。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"find_user"`。
        locale: 本地化标识。默认为 `"zh-CN"`。
        as_user: 是否以请求用户身份读取。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = find_user(description="按关键词查找同事")
        >>> tool.name, tool.requires_approval
        ('find_user', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "non-empty search keyword (name, etc.)"},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Least-privilege: reject blank queries so the model cannot dump the whole directory.
        query = str(arguments.get("query") or "").strip()
        if not query:
            return ToolResult(
                ToolOutcome.BLOCKED,
                content="a non-empty search query is required",
                is_error=True,
            )
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        users = await client.contact.users.search(query, max_items=_MAX_MATCHES)
        # Minimal disclosure: expose only name + open_id, capped; drop emails/phones/department/etc.
        matches = [
            {"name": user.get("name"), "open_id": user.get("open_id")}
            for user in (users or [])[:_MAX_MATCHES]
            if isinstance(user, dict)
        ]
        return ToolResult(ToolOutcome.COMPLETED, content=matches)

    return Tool(name=name, description=description, input_schema=input_schema, handler=handler)
