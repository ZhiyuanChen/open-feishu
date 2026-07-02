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

r"""会议室工具工厂：列出会议室、查询会议室忙闲、预订会议室（需审批）。详见 [feishu.agent.toolkit][]。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...calendar import calendar_event
from ..result import ToolOutcome, ToolResult
from ..tools import Tool
from ._base import needs_user_auth, resolve_client, resolve_timezone


def list_meeting_room_buildings(
    *,
    description: str,
    name: str = "list_meeting_room_buildings",
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：列出会议室所在的建筑 / 楼宇，返回一个 [feishu.agent.tools.Tool][]。

    用于先发现 `building_id`，再用 [feishu.agent.toolkit.rooms.list_meeting_rooms][] 在该建筑内列会议室。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"list_meeting_room_buildings"`。
        as_user: 是否以请求用户身份读取。默认为 `True`（用户态读取，权限受该用户自身约束）。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = list_meeting_room_buildings(description="列出建筑")
        >>> tool.name, tool.requires_approval
        ('list_meeting_room_buildings', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"max_items": {"type": "integer", "description": "Maximum number of buildings to return"}},
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        buildings = await client.meeting_room.list_buildings(max_items=arguments.get("max_items"))
        return ToolResult(ToolOutcome.COMPLETED, content=buildings)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        auth_scopes=tuple(auth_scopes),
    )


def list_meeting_rooms(
    *,
    description: str,
    name: str = "list_meeting_rooms",
    # User scope (zero-trust): the meeting_room API accepts user_access_token (calendar:room:readonly,
    # user-granted), so read as the requesting user — bounded by their own permissions — not the tenant.
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：列出某建筑内的会议室，返回一个 [feishu.agent.tools.Tool][]。

    读取指定建筑下的会议室目录。未提供 `building_id` 时，模型应先通过
    [feishu.agent.toolkit.rooms.list_meeting_room_buildings][] 发现建筑；遍历所有建筑并合并结果属于产品层策略，
    本工厂不内置。按会议室能力筛选需要更新的 `vc/v1/rooms` 接口，当前旧版会议室列表接口不提供该能力。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"list_meeting_rooms"`。
        as_user: 是否以请求用户身份读取。默认为 `True`（用户态读取，权限受该用户自身约束）。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = list_meeting_rooms(description="x")
        >>> tool.name, tool.requires_approval
        ('list_meeting_rooms', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "building_id": {"type": "string", "description": "Building ID to list rooms in"},
            "order_by": {"type": "string", "description": "e.g. name-asc, name-desc, floor_name-asc"},
            "max_items": {"type": "integer", "description": "Maximum number of rooms to return"},
        },
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        rooms = await client.meeting_room.list(
            building_id=arguments.get("building_id"),
            order_by=arguments.get("order_by"),
            max_items=arguments.get("max_items"),
        )
        return ToolResult(ToolOutcome.COMPLETED, content=rooms)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        auth_scopes=tuple(auth_scopes),
    )


def query_meeting_room_freebusy(
    *,
    description: str,
    name: str = "query_meeting_room_freebusy",
    # User scope (zero-trust): meeting_room API accepts user_access_token (calendar:room:readonly).
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：查询会议室在某时间段内的忙闲，返回一个 [feishu.agent.tools.Tool][]。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"query_meeting_room_freebusy"`。
        as_user: 是否以请求用户身份读取。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = query_meeting_room_freebusy(description="x")
        >>> tool.name, tool.requires_approval
        ('query_meeting_room_freebusy', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "room_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Meeting room IDs to check",
            },
            "time_min": {"type": "string", "description": "ISO 8601 start of the range"},
            "time_max": {"type": "string", "description": "ISO 8601 end of the range"},
        },
        "required": ["room_ids", "time_min", "time_max"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        freebusy = await client.meeting_room.freebusy(
            arguments["room_ids"],
            time_min=arguments["time_min"],
            time_max=arguments["time_max"],
        )
        return ToolResult(ToolOutcome.COMPLETED, content=freebusy)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
        auth_scopes=tuple(auth_scopes),
    )


def book_meeting_room(
    *,
    description: str,
    name: str = "book_meeting_room",
    timezone: str = "Asia/Shanghai",
    requires_approval: bool = True,
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：预订会议室，返回一个需审批的 [feishu.agent.tools.Tool][]。

    飞书没有独立的“订会议室”接口：预订会议室通过创建一条日程、再把会议室作为
    `type=resource` 的日程参与人加入完成。`requires_approval=True`
    时，[feishu.agent.loop.Agent][] 先挂起并发审批卡片；用户批准后处理函数才执行写入。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"book_meeting_room"`。
        timezone: ISO 时间换算所用时区。默认为 `"Asia/Shanghai"`。
        requires_approval: 执行前是否要求用户审批。默认为 `True`。
        as_user: 是否以请求用户身份写入。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = book_meeting_room(description="x")
        >>> tool.name, tool.requires_approval
        ('book_meeting_room', True)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Meeting title"},
            "start_time": {"type": "string", "description": "ISO 8601 start"},
            "end_time": {"type": "string", "description": "ISO 8601 end"},
            "room_id": {"type": "string", "description": "Meeting room ID to reserve"},
            "description": {"type": "string", "description": "Meeting description"},
            "location": {"type": "string", "description": "Meeting location name"},
        },
        "required": ["summary", "start_time", "end_time", "room_id"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        # Runs only AFTER the user approves the confirmation card; perform the write directly.
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        tz = await resolve_timezone(timezone)
        calendar_id = await _primary_calendar_id(client)
        event_payload = calendar_event(
            summary=arguments["summary"],
            start_time=arguments["start_time"],
            end_time=arguments["end_time"],
            timezone=tz,
            description=arguments.get("description"),
            location=arguments.get("location"),
        )
        result = await client.calendar.events.create(calendar_id, event_payload)
        event = result.get("event") or {}
        event_id = event.get("event_id")
        if event_id:
            result.attendees_response = await client.calendar.attendees.add(
                calendar_id,
                str(event_id),
                [{"type": "resource", "room_id": arguments["room_id"]}],
                need_notification=True,
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


async def _primary_calendar_id(client: Any) -> str:
    r"""解析当前身份的主日历 `calendar_id`。"""
    primary = await client.calendar.calendars.primary()
    for item in primary.get("calendars") or []:
        if not isinstance(item, dict):
            continue
        calendar = item.get("calendar") or item
        if isinstance(calendar, dict) and calendar.get("calendar_id"):
            return str(calendar["calendar_id"])
    raise ValueError("没有找到主日历")


__all__ = [
    "book_meeting_room",
    "list_meeting_room_buildings",
    "list_meeting_rooms",
    "query_meeting_room_freebusy",
]
