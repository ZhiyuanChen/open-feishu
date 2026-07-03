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

r"""画板工具工厂：列出画板内的所有节点（读）。详见 [feishu.agent.toolkit][]。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..result import ToolOutcome, ToolResult
from ..tools import Tool
from ._base import needs_user_auth, resolve_client


def list_whiteboard_nodes(
    *,
    description: str,
    name: str = "list_whiteboard_nodes",
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：一次性列出某画板内的全部节点，返回一个 [feishu.agent.tools.Tool][]。

    处理函数调用 `client.board.whiteboards.list_nodes(whiteboard_id, user_id_type="open_id")`（该接口不分页），
    节点以 `id`、`type`、`parent_id`、`children` 及对应类型的内容字段描述。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"list_whiteboard_nodes"`。
        as_user: 是否以请求用户身份读取。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = list_whiteboard_nodes(description="读取画板节点")
        >>> tool.name, tool.requires_approval
        ('list_whiteboard_nodes', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "whiteboard_id": {"type": "string", "description": "whiteboard id to list nodes for"},
        },
        "required": ["whiteboard_id"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        nodes = await client.board.whiteboards.list_nodes(arguments["whiteboard_id"], user_id_type="open_id")
        return ToolResult(ToolOutcome.COMPLETED, content=nodes)

    return Tool(name=name, description=description, input_schema=input_schema, handler=handler)
