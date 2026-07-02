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

r"""任务工具工厂：创建任务（需审批）。详见 [feishu.agent.toolkit][]。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from feishu.task import task_payload

from ..result import ToolOutcome, ToolResult
from ..tools import Tool
from ._base import needs_user_auth, resolve_client


def create_task(
    *,
    description: str,
    name: str = "create_task",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：创建任务，返回一个需审批的 [feishu.agent.tools.Tool][]。

    `requires_approval=True` 时，[feishu.agent.loop.Agent][] 先挂起并发审批卡片；用户批准后处理函数才执行。
    处理函数以请求用户身份（`open_id`）调用飞书任务 v2 创建接口。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"create_task"`。
        requires_approval: 是否在执行前要求审批。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = create_task(description="创建任务")
        >>> tool.name, tool.requires_approval
        ('create_task', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Task title"},
            "description": {"type": "string", "description": "Optional task description"},
            "due": {
                "type": "object",
                "description": "Optional Feishu due object, e.g. {'timestamp': '...', 'is_all_day': false}",
            },
            "members": {
                "type": "array",
                "description": "Optional Feishu member objects, each with id/role/type",
                "items": {"type": "object"},
            },
        },
        "required": ["summary"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        task = task_payload(
            arguments["summary"],
            description=arguments.get("description"),
            due=arguments.get("due"),
            members=arguments.get("members"),
        )
        result = await client.task.tasks.create(task, user_id_type="open_id")
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def comment_on_task(
    *,
    description: str,
    name: str = "comment_on_task",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：在任务上发表评论，返回一个需审批的 [feishu.agent.tools.Tool][]。

    `requires_approval=True` 时，[feishu.agent.loop.Agent][] 先挂起并发审批卡片；用户批准后处理函数才执行。
    处理函数以请求用户身份（`open_id`）调用飞书任务 v2 评论创建接口
    `client.task.comments.create(task_guid, content, user_id_type="open_id")`，其中 `task_guid` 即任务的
    `guid`（作为评论资源的 `resource_id`，`resource_type` 取默认值 `task`）。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"comment_on_task"`。
        requires_approval: 是否在执行前要求审批。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = comment_on_task(description="给任务留言")
        >>> tool.name, tool.requires_approval
        ('comment_on_task', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_guid": {"type": "string", "description": "Task guid to comment on"},
            "content": {"type": "string", "description": "Comment text"},
        },
        "required": ["task_guid", "content"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        result = await client.task.comments.create(
            arguments["task_guid"],
            arguments["content"],
            user_id_type="open_id",
        )
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def update_task(
    *,
    description: str,
    name: str = "update_task",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：增量编辑已有任务，返回一个需审批的 [feishu.agent.tools.Tool][]。

    处理函数仅依据模型显式传入的字段构造**增量更新体**——未传入的字段保持不变——把新值收进 `task`、
    把待更新字段名收进 `update_fields`（与 [feishu.agent.toolkit.calendar.update_calendar_event][] 同形），
    再以请求用户身份（`open_id`）调用 `client.task.tasks.update(task_guid, task, update_fields, user_id_type="open_id")`。
    `requires_approval=True` 时由 [feishu.agent.loop.Agent][] 先发审批卡片，用户批准后处理函数才执行写入。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"update_task"`。
        requires_approval: 是否在执行前要求审批。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = update_task(description="编辑任务")
        >>> tool.name, tool.requires_approval
        ('update_task', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_guid": {"type": "string", "description": "Task guid to update"},
            "summary": {"type": "string", "description": "Optional new task title"},
            "description": {"type": "string", "description": "Optional new task description"},
        },
        "required": ["task_guid"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        # Incremental patch: include only fields the model explicitly supplied; omit the rest.
        task: dict[str, Any] = {}
        update_fields: list[str] = []
        if arguments.get("summary") is not None:
            task["summary"] = arguments["summary"]
            update_fields.append("summary")
        if arguments.get("description") is not None:
            task["description"] = arguments["description"]
            update_fields.append("description")
        result = await client.task.tasks.update(arguments["task_guid"], task, update_fields, user_id_type="open_id")
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def delete_task(
    *,
    description: str,
    name: str = "delete_task",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：删除已有任务，返回一个需审批的 [feishu.agent.tools.Tool][]。

    处理函数以请求用户身份调用 `client.task.tasks.delete(task_guid)`。`requires_approval=True` 时由
    [feishu.agent.loop.Agent][] 先发审批卡片，用户批准后处理函数才执行删除。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"delete_task"`。
        requires_approval: 是否在执行前要求审批。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = delete_task(description="删除任务")
        >>> tool.name, tool.requires_approval
        ('delete_task', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_guid": {"type": "string", "description": "Task guid to delete"},
        },
        "required": ["task_guid"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        result = await client.task.tasks.delete(arguments["task_guid"])
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def update_task_comment(
    *,
    description: str,
    name: str = "update_task_comment",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：编辑任务评论的内容，返回一个需审批的 [feishu.agent.tools.Tool][]。

    处理函数以请求用户身份调用 `client.task.comments.update(comment_id, content)`，用新内容整体替换评论文本。
    `requires_approval=True` 时由 [feishu.agent.loop.Agent][] 先发审批卡片，用户批准后处理函数才执行写入。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"update_task_comment"`。
        requires_approval: 是否在执行前要求审批。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = update_task_comment(description="编辑任务评论")
        >>> tool.name, tool.requires_approval
        ('update_task_comment', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "comment_id": {"type": "string", "description": "Comment id to update"},
            "content": {"type": "string", "description": "New comment text"},
        },
        "required": ["comment_id", "content"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        result = await client.task.comments.update(arguments["comment_id"], arguments["content"])
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def delete_task_comment(
    *,
    description: str,
    name: str = "delete_task_comment",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：删除任务评论，返回一个需审批的 [feishu.agent.tools.Tool][]。

    处理函数以请求用户身份调用 `client.task.comments.delete(comment_id)`。`requires_approval=True` 时由
    [feishu.agent.loop.Agent][] 先发审批卡片，用户批准后处理函数才执行删除。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"delete_task_comment"`。
        requires_approval: 是否在执行前要求审批。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = delete_task_comment(description="删除任务评论")
        >>> tool.name, tool.requires_approval
        ('delete_task_comment', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "comment_id": {"type": "string", "description": "Comment id to delete"},
        },
        "required": ["comment_id"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        result = await client.task.comments.delete(arguments["comment_id"])
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def list_my_tasks(
    *,
    description: str,
    name: str = "list_my_tasks",
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：列出「请求用户本人」负责的任务，返回一个 [feishu.agent.tools.Tool][]。

    最小权限（zero-trust）：本工具不暴露任何用户 id 入参——`client.task.tasks.list` 仅支持
    `user_access_token` 调用，天然只返回发起请求的用户本人负责的任务，模型无法指向他人。处理函数以请求用户
    身份（`open_id`）调用 `client.task.tasks.list(completed=..., user_id_type="open_id")`，`completed` 缺省时
    返回全部任务。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"list_my_tasks"`。
        as_user: 是否以请求用户身份读取。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = list_my_tasks(description="查看我的任务")
        >>> tool.name, tool.requires_approval
        ('list_my_tasks', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "completed": {
                "type": "boolean",
                "description": "Only completed (true) / pending (false) tasks; omit for all",
            },
        },
        "required": [],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        result = await client.task.tasks.list(completed=arguments.get("completed"), user_id_type="open_id")
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(name=name, description=description, input_schema=input_schema, handler=handler)


def list_task_comments(
    *,
    description: str,
    name: str = "list_task_comments",
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：列举某个任务下的评论，返回一个 [feishu.agent.tools.Tool][]。

    处理函数以请求用户身份调用 `client.task.comments.list(task_guid)`，其中 `task_guid` 即任务的 `guid`
    （作为评论资源的 `resource_id`，`resource_type` 取默认值 `task`）。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"list_task_comments"`。
        as_user: 是否以请求用户身份读取。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = list_task_comments(description="查看任务评论")
        >>> tool.name, tool.requires_approval
        ('list_task_comments', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_guid": {"type": "string", "description": "Task guid whose comments to list"},
        },
        "required": ["task_guid"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        result = await client.task.comments.list(arguments["task_guid"])
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(name=name, description=description, input_schema=input_schema, handler=handler)


__all__ = [
    "comment_on_task",
    "create_task",
    "delete_task",
    "delete_task_comment",
    "list_my_tasks",
    "list_task_comments",
    "update_task",
    "update_task_comment",
]
