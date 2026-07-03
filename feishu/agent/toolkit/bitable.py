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

r"""多维表格工具工厂：新增 / 更新 / 删除记录（均需审批）、列出记录（只读）。详见 [feishu.agent.toolkit][]。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..result import ToolOutcome, ToolResult
from ..tools import Tool
from ._base import needs_user_auth, resolve_client


def create_bitable_record(
    *,
    description: str,
    name: str = "create_bitable_record",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：在指定数据表中新增一条记录，返回一个需审批的 [feishu.agent.tools.Tool][]。

    `requires_approval=True` 时，[feishu.agent.loop.Agent][] 先挂起并发审批卡片；用户批准后处理函数才执行，
    直接调用 `client.bitable.records.create(app_token, table_id, fields)` 完成写入。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"create_bitable_record"`。
        requires_approval: 是否需要审批后执行。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = create_bitable_record(description="新增多维表格记录")
        >>> tool.name, tool.requires_approval
        ('create_bitable_record', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "app_token": {"type": "string", "description": "Bitable app_token"},
            "table_id": {"type": "string", "description": "Target table_id"},
            "fields": {
                "type": "object",
                "description": "Record field values keyed by Bitable field name",
            },
        },
        "required": ["app_token", "table_id", "fields"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        result = await client.bitable.records.create(
            arguments["app_token"],
            arguments["table_id"],
            arguments["fields"],
        )
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def update_bitable_record(
    *,
    description: str,
    name: str = "update_bitable_record",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：更新指定数据表中的一条记录，返回一个需审批的 [feishu.agent.tools.Tool][]。

    `requires_approval=True` 时，[feishu.agent.loop.Agent][] 先挂起并发审批卡片；用户批准后处理函数才执行，
    直接调用 `client.bitable.records.update(app_token, table_id, record_id, fields)` 完成写入。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"update_bitable_record"`。
        requires_approval: 是否需要审批后执行。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = update_bitable_record(description="更新多维表格记录")
        >>> tool.name, tool.requires_approval
        ('update_bitable_record', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "app_token": {"type": "string", "description": "Bitable app_token"},
            "table_id": {"type": "string", "description": "Target table_id"},
            "record_id": {"type": "string", "description": "Record id to update"},
            "fields": {
                "type": "object",
                "description": "Record field values keyed by Bitable field name",
            },
        },
        "required": ["app_token", "table_id", "record_id", "fields"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        result = await client.bitable.records.update(
            arguments["app_token"],
            arguments["table_id"],
            arguments["record_id"],
            arguments["fields"],
        )
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def delete_bitable_record(
    *,
    description: str,
    name: str = "delete_bitable_record",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：删除指定数据表中的一条记录，返回一个需审批的 [feishu.agent.tools.Tool][]。

    `requires_approval=True` 时，[feishu.agent.loop.Agent][] 先挂起并发审批卡片；用户批准后处理函数才执行，
    直接调用 `client.bitable.records.delete(app_token, table_id, record_id)` 完成删除。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"delete_bitable_record"`。
        requires_approval: 是否需要审批后执行。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = delete_bitable_record(description="删除多维表格记录")
        >>> tool.name, tool.requires_approval
        ('delete_bitable_record', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "app_token": {"type": "string", "description": "Bitable app_token"},
            "table_id": {"type": "string", "description": "Target table_id"},
            "record_id": {"type": "string", "description": "Record id to delete"},
        },
        "required": ["app_token", "table_id", "record_id"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        result = await client.bitable.records.delete(
            arguments["app_token"],
            arguments["table_id"],
            arguments["record_id"],
        )
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def list_bitable_records(
    *,
    description: str,
    name: str = "list_bitable_records",
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：列出指定数据表中的记录，返回一个 [feishu.agent.tools.Tool][]。

    处理函数调用 `client.bitable.records.list(app_token, table_id, view_id=..., filter=..., max_items=100)`。
    最小权限（zero-trust）：`max_items` 由本工厂硬编码为 `100`，模型无法放大返回规模。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"list_bitable_records"`。
        as_user: 是否以请求用户身份读取。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = list_bitable_records(description="列出多维表格记录")
        >>> tool.name, tool.requires_approval
        ('list_bitable_records', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "app_token": {"type": "string", "description": "Bitable app_token"},
            "table_id": {"type": "string", "description": "Target table_id"},
            "view_id": {"type": "string", "description": "Optional view_id to scope the listing"},
            "filter": {"type": "string", "description": "Optional filter expression"},
        },
        "required": ["app_token", "table_id"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        # Least-privilege: cap the number of records the model can ever pull back.
        result = await client.bitable.records.list(
            arguments["app_token"],
            arguments["table_id"],
            view_id=arguments.get("view_id"),
            filter=arguments.get("filter"),
            max_items=100,
        )
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(name=name, description=description, input_schema=input_schema, handler=handler)
