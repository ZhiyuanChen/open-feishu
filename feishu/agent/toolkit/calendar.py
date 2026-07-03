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

r"""日历工具工厂：列出日程、创建日程（需审批）、查询忙闲。详见 [feishu.agent.toolkit][]。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from feishu.calendar import calendar_event, calendar_time, freebusy_body, unix_seconds

from ..result import ToolOutcome, ToolResult
from ..tools import Tool
from ._base import needs_user_auth, requesting_user_id, resolve_client, resolve_timezone


async def _resolve_calendar_id(client: Any, calendar_id: str | None) -> str:
    r"""返回给定 `calendar_id`，或在缺省 / `"primary"` 时解析当前用户的主日历 id。"""
    if calendar_id and calendar_id != "primary":
        return calendar_id
    primary = await client.calendar.calendars.primary()
    for item in primary.get("calendars") or []:
        if not isinstance(item, dict):
            continue
        calendar = item.get("calendar") or item
        if isinstance(calendar, dict) and calendar.get("calendar_id"):
            return str(calendar["calendar_id"])
    raise ValueError("no primary calendar found")


def list_calendar_events(
    *,
    description: str,
    name: str = "list_calendar_events",
    timezone: str = "Asia/Shanghai",
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：列出用户日程，返回一个 [feishu.agent.tools.Tool][]。

    处理函数解析日历（缺省取主日历），把 ISO 时间经 [feishu.calendar.unix_seconds][] 转为接口所需的秒级
    时间戳，再调用 `client.calendar.events.list(calendar_id, start_time=..., end_time=...)`。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"list_calendar_events"`。
        timezone: ISO 时间换算所用时区。默认为 `"Asia/Shanghai"`。
        as_user: 是否以请求用户身份读取。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Examples:
        >>> tool = list_calendar_events(description="查看我的日程")
        >>> tool.name, tool.requires_approval
        ('list_calendar_events', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "time_min": {"type": "string", "description": "ISO 8601 start of the range"},
            "time_max": {"type": "string", "description": "ISO 8601 end of the range"},
            "calendar_id": {"type": "string", "description": "calendar id; defaults to the user's primary calendar"},
            "max_items": {"type": "integer", "description": "max events to return"},
        },
        "required": ["time_min", "time_max"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        tz = await resolve_timezone(timezone)
        calendar_id = await _resolve_calendar_id(client, arguments.get("calendar_id"))
        events = await client.calendar.events.list(
            calendar_id,
            start_time=str(unix_seconds(arguments["time_min"], timezone=tz)),
            end_time=str(unix_seconds(arguments["time_max"], timezone=tz)),
            max_items=int(arguments.get("max_items") or 20),
        )
        return ToolResult(ToolOutcome.COMPLETED, content=events)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        auth_scopes=tuple(auth_scopes),
    )


def create_calendar_event(
    *,
    description: str,
    name: str = "create_calendar_event",
    timezone: str = "Asia/Shanghai",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：创建日程，返回一个需审批的 [feishu.agent.tools.Tool][]。

    处理函数解析日历（缺省取主日历），用 [feishu.calendar.calendar_event][] 构造事件体，再调用
    `client.calendar.events.create(calendar_id, event)`。`requires_approval=True` 时由
    [feishu.agent.loop.Agent][] 先发审批卡片，用户批准后才执行。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"create_calendar_event"`。
        timezone: ISO 时间换算所用时区。默认为 `"Asia/Shanghai"`。
        requires_approval: 是否需用户审批后执行。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的需审批 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = create_calendar_event(description="创建日程")
        >>> tool.name, tool.requires_approval
        ('create_calendar_event', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "start": {"type": "string", "description": "ISO 8601 start"},
            "end": {"type": "string", "description": "ISO 8601 end"},
            "description": {"type": "string"},
            "location": {"type": "string"},
            "calendar_id": {"type": "string", "description": "calendar id; defaults to the user's primary calendar"},
        },
        "required": ["summary", "start", "end"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        tz = await resolve_timezone(timezone)
        calendar_id = await _resolve_calendar_id(client, arguments.get("calendar_id"))
        event = calendar_event(
            summary=arguments["summary"],
            start_time=arguments["start"],
            end_time=arguments["end"],
            timezone=tz,
            description=arguments.get("description"),
            location=arguments.get("location"),
        )
        result = await client.calendar.events.create(calendar_id, event)
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
        auth_scopes=tuple(auth_scopes),
    )


def query_calendar_freebusy(
    *,
    description: str,
    name: str = "query_calendar_freebusy",
    timezone: str = "Asia/Shanghai",
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：查询「请求用户本人」的忙闲信息，返回一个 [feishu.agent.tools.Tool][]。

    最小权限（zero-trust）：本工具只查询发起请求的用户本人，主体身份取自
    [feishu.agent.context.ToolContext.requesting_user][]，模型无法指向他人。查询会议室忙闲请改用
    [feishu.agent.toolkit.rooms.query_meeting_room_freebusy][]。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"query_calendar_freebusy"`。
        timezone: 时间转换所用时区。默认为 `"Asia/Shanghai"`。
        as_user: 是否以请求用户身份读取。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Examples:
        >>> tool = query_calendar_freebusy(description="查询忙闲")
        >>> tool.name, tool.requires_approval
        ('query_calendar_freebusy', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "time_min": {"type": "string", "description": "ISO 8601 start of the range"},
            "time_max": {"type": "string", "description": "ISO 8601 end of the range"},
        },
        "required": ["time_min", "time_max"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        # Least-privilege: only ever query the requesting user's own free/busy, never an arbitrary user.
        subject = requesting_user_id("open_id")
        if not subject:
            return ToolResult(
                ToolOutcome.BLOCKED, content="cannot resolve the requesting user's identity", is_error=True
            )
        tz = await resolve_timezone(timezone)
        body = freebusy_body(
            time_min=arguments["time_min"],
            time_max=arguments["time_max"],
            user_id=subject,
            timezone=tz,
        )
        freebusy = await client.calendar.freebusy.query(body)
        return ToolResult(ToolOutcome.COMPLETED, content=freebusy)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        auth_scopes=tuple(auth_scopes),
    )


def update_calendar_event(
    *,
    description: str,
    name: str = "update_calendar_event",
    timezone: str = "Asia/Shanghai",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：改期 / 编辑已有日程，返回一个需审批的 [feishu.agent.tools.Tool][]。

    处理函数解析日历（缺省取主日历），仅依据模型显式传入的字段构造**增量更新体**——未传入的字段保持
    不变——再调用 `client.calendar.events.update(calendar_id, event_id, event)`。时间字段经
    [feishu.calendar.calendar_time][] 归一化（与 [feishu.calendar.calendar_event][] 同形），`location`
    以 `{"name": ...}` 写入。`requires_approval=True` 时由 [feishu.agent.loop.Agent][] 先发审批卡片，
    用户批准后处理函数才执行写入。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"update_calendar_event"`。
        timezone: ISO 时间换算所用时区。默认为 `"Asia/Shanghai"`。
        requires_approval: 是否需用户审批后执行。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Examples:
        >>> tool = update_calendar_event(description="改期日程")
        >>> tool.name, tool.requires_approval
        ('update_calendar_event', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "event id to update"},
            "summary": {"type": "string"},
            "start": {"type": "string", "description": "ISO 8601 start"},
            "end": {"type": "string", "description": "ISO 8601 end"},
            "description": {"type": "string"},
            "location": {"type": "string"},
            "calendar_id": {"type": "string", "description": "calendar id; defaults to the user's primary calendar"},
        },
        "required": ["event_id"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        tz = await resolve_timezone(timezone)
        calendar_id = await _resolve_calendar_id(client, arguments.get("calendar_id"))
        # Incremental patch: include only fields the model explicitly supplied; omit the rest.
        event: dict[str, Any] = {}
        if arguments.get("summary") is not None:
            event["summary"] = arguments["summary"]
        if arguments.get("start") is not None:
            event["start_time"] = calendar_time(arguments["start"], timezone=tz)
        if arguments.get("end") is not None:
            event["end_time"] = calendar_time(arguments["end"], timezone=tz)
        if arguments.get("description") is not None:
            event["description"] = arguments["description"]
        if arguments.get("location") is not None:
            event["location"] = {"name": arguments["location"]}
        result = await client.calendar.events.update(calendar_id, arguments["event_id"], event)
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
        auth_scopes=tuple(auth_scopes),
    )


def cancel_calendar_event(
    *,
    description: str,
    name: str = "cancel_calendar_event",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：取消（删除）已有日程，返回一个需审批的 [feishu.agent.tools.Tool][]。

    处理函数解析日历（缺省取主日历），再调用 `client.calendar.events.delete(calendar_id, event_id)`。
    `requires_approval=True` 时由 [feishu.agent.loop.Agent][] 先发审批卡片，用户批准后处理函数才执行删除。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"cancel_calendar_event"`。
        requires_approval: 是否需用户审批后执行。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Examples:
        >>> tool = cancel_calendar_event(description="取消日程")
        >>> tool.name, tool.requires_approval
        ('cancel_calendar_event', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "event id to delete"},
            "calendar_id": {"type": "string", "description": "calendar id; defaults to the user's primary calendar"},
        },
        "required": ["event_id"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        calendar_id = await _resolve_calendar_id(client, arguments.get("calendar_id"))
        result = await client.calendar.events.delete(calendar_id, arguments["event_id"])
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
        auth_scopes=tuple(auth_scopes),
    )


def respond_to_invite(
    *,
    description: str,
    name: str = "respond_to_invite",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：回复（RSVP）一个日程邀请——接受 / 待定 / 拒绝，返回一个需审批的 [feishu.agent.tools.Tool][]。

    处理函数解析日历（缺省取主日历），调用 `client.calendar.events.reply(calendar_id, event_id, rsvp_status=...)`。
    `requires_approval=True` 时由 [feishu.agent.loop.Agent][] 先发审批卡片，用户批准后才执行；回复的始终是请求
    用户本人的日历邀请（按用户身份与主日历解析）。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"respond_to_invite"`。
        requires_approval: 是否需用户审批后执行。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的需审批 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = respond_to_invite(description="回复日程邀请")
        >>> tool.name, tool.requires_approval
        ('respond_to_invite', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "event id to respond to"},
            "rsvp_status": {
                "type": "string",
                "enum": ["accept", "tentative", "decline"],
                "description": "RSVP response",
            },
            "calendar_id": {"type": "string", "description": "calendar id; defaults to the user's primary calendar"},
        },
        "required": ["event_id", "rsvp_status"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        calendar_id = await _resolve_calendar_id(client, arguments.get("calendar_id"))
        result = await client.calendar.events.reply(
            calendar_id, arguments["event_id"], rsvp_status=arguments["rsvp_status"]
        )
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        requires_approval=requires_approval,
        auth_scopes=tuple(auth_scopes),
    )
