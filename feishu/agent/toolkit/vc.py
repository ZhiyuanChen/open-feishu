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

r"""视频会议工具工厂：预约、更新、取消会议（均需审批）。详见 [feishu.agent.toolkit][]。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from feishu.calendar import unix_seconds

from ..result import ToolOutcome, ToolResult
from ..tools import Tool
from ._base import needs_user_auth, requesting_user_id, resolve_client


def reserve_meeting(
    *,
    description: str,
    name: str = "reserve_meeting",
    locale: str = "zh-CN",
    timezone: str = "Asia/Shanghai",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：预约一场视频会议，返回一个需审批的 [feishu.agent.tools.Tool][]。

    处理函数把会议主题写入 `meeting_settings`，将 ISO 到期时间经 [feishu.calendar.unix_seconds][] 转为接口所需的
    秒级时间戳字符串，再调用 `client.vc.reserves.apply(meeting_settings, end_time=..., owner_id=..., user_id_type="open_id")`。
    预约成功后返回会议号与入会链接。`requires_approval=True` 时由 [feishu.agent.loop.Agent][] 先发审批卡片，用户批准后
    处理函数才执行预约。

    最小权限（zero-trust）：预约人始终是发起请求的用户本人，`owner_id` 取自
    [feishu.agent.context.ToolContext.requesting_user][]（`open_id`），模型无法指向他人。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"reserve_meeting"`。
        locale: 本地化标识。默认为 `"zh-CN"`。
        timezone: ISO 时间换算所用时区。默认为 `"Asia/Shanghai"`。
        requires_approval: 是否需用户审批后执行。默认为 `True`。
        as_user: 是否以请求用户身份预约。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Examples:
        >>> tool = reserve_meeting(description="预约视频会议")
        >>> tool.name, tool.requires_approval
        ('reserve_meeting', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "meeting topic / title"},
            "end_time": {"type": "string", "description": "ISO 8601 reservation expiry time"},
        },
        "required": ["topic", "end_time"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        # Least-privilege: the reservation owner is always the requesting user, never an arbitrary id.
        owner = requesting_user_id("open_id")
        if not owner:
            return ToolResult(
                ToolOutcome.BLOCKED, content="cannot resolve the requesting user's identity", is_error=True
            )
        result = await client.vc.reserves.apply(
            {"topic": arguments["topic"]},
            end_time=str(unix_seconds(arguments["end_time"], timezone=timezone)),
            owner_id=owner,
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


def update_reservation(
    *,
    description: str,
    name: str = "update_reservation",
    locale: str = "zh-CN",
    timezone: str = "Asia/Shanghai",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：更新一场已预约的视频会议，返回一个需审批的 [feishu.agent.tools.Tool][]。

    处理函数仅把显式传入的字段写入请求：给出 `end_time` 时经 [feishu.calendar.unix_seconds][] 转为接口所需的秒级
    时间戳字符串；给出 `topic` 时写入 `meeting_settings`；并始终以 `user_id_type="open_id"` 调用
    `client.vc.reserves.update(reserve_id, **kwargs)`。`requires_approval=True` 时由 [feishu.agent.loop.Agent][]
    先发审批卡片，用户批准后处理函数才执行更新。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"update_reservation"`。
        locale: 本地化标识。默认为 `"zh-CN"`。
        timezone: ISO 时间换算所用时区。默认为 `"Asia/Shanghai"`。
        requires_approval: 是否需用户审批后执行。默认为 `True`。
        as_user: 是否以请求用户身份更新。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Examples:
        >>> tool = update_reservation(description="更新视频会议预约")
        >>> tool.name, tool.requires_approval
        ('update_reservation', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "reserve_id": {"type": "string", "description": "reservation id to update"},
            "end_time": {"type": "string", "description": "new ISO 8601 reservation expiry time"},
            "topic": {"type": "string", "description": "new meeting topic / title"},
        },
        "required": ["reserve_id"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        kwargs: dict[str, Any] = {"user_id_type": "open_id"}
        if "end_time" in arguments:
            kwargs["end_time"] = str(unix_seconds(arguments["end_time"], timezone=timezone))
        if "topic" in arguments:
            kwargs["meeting_settings"] = {"topic": arguments["topic"]}
        result = await client.vc.reserves.update(arguments["reserve_id"], **kwargs)
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )


def cancel_reservation(
    *,
    description: str,
    name: str = "cancel_reservation",
    locale: str = "zh-CN",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：取消一场已预约的视频会议，返回一个需审批的 [feishu.agent.tools.Tool][]。

    处理函数直接调用 `client.vc.reserves.delete(reserve_id)` 删除预约。`requires_approval=True` 时由
    [feishu.agent.loop.Agent][] 先发审批卡片，用户批准后处理函数才执行取消。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"cancel_reservation"`。
        locale: 本地化标识。默认为 `"zh-CN"`。
        requires_approval: 是否需用户审批后执行。默认为 `True`。
        as_user: 是否以请求用户身份取消。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Examples:
        >>> tool = cancel_reservation(description="取消视频会议预约")
        >>> tool.name, tool.requires_approval
        ('cancel_reservation', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "reserve_id": {"type": "string", "description": "reservation id to cancel"},
        },
        "required": ["reserve_id"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        result = await client.vc.reserves.delete(arguments["reserve_id"])
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
    )
