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

r"""文档工具工厂：搜索文档、读取文档正文、读取消息线程、读取会议记录（全部为读类）。

各工厂返回的 [feishu.agent.tools.Tool][] 只负责「取数」——抓取并回传原始正文/转录文本，由
[feishu.agent.loop.Agent][] 背后的模型在自己的回复里做总结。处理函数内绝不调用模型。
详见 [feishu.agent.toolkit][]。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from feishu.calendar import unix_seconds
from feishu.drive.references import (
    document_reference_from_mapping,
    meeting_note_reference_from_mapping,
    meeting_note_reference_from_meeting,
    resolve_document_reference,
)
from feishu.im.inbound import message_transcript

from ..result import ToolOutcome, ToolResult
from ..tools import Tool
from ._base import needs_user_auth, resolve_client, resolve_timezone


def search_documents(
    *,
    description: str,
    name: str = "search_documents",
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：搜索/列举请求用户可见的文档，返回一个 [feishu.agent.tools.Tool][]。

    传入 `query` 时走知识库全文检索（`client.wiki.search`）；否则列举云空间文件夹下的文件
    （`client.drive.files.list`）。两者均以请求用户身份调用以保持权限边界。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"search_documents"`。
        as_user: 是否以请求用户身份读取。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = search_documents(description="搜索文档")
        >>> tool.name, tool.requires_approval
        ('search_documents', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "full-text query; omit to list a folder instead"},
            "space_id": {"type": "string", "description": "restrict wiki search to one space"},
            "folder_token": {"type": "string", "description": "drive folder to list when no query is given"},
            "max_items": {"type": "integer", "description": "maximum results to return"},
        },
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        query = (arguments.get("query") or "").strip()
        max_items = arguments.get("max_items", 10)
        if query:
            items = await client.wiki.search(
                query,
                space_id=(arguments.get("space_id") or "").strip() or None,
                max_items=max_items,
            )
            return ToolResult(ToolOutcome.COMPLETED, content={"query": query, "items": items})
        items = await client.drive.files.list(
            folder_token=(arguments.get("folder_token") or "").strip() or None,
            max_items=max_items,
        )
        return ToolResult(ToolOutcome.COMPLETED, content={"items": items})

    return Tool(name=name, description=description, input_schema=input_schema, handler=handler)


def get_document_content(
    *,
    description: str,
    name: str = "get_document_content",
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
    lang: int | None = None,
) -> Tool:
    r"""
    读类工厂：抓取一篇飞书文档的纯文本正文，返回一个 [feishu.agent.tools.Tool][]。

    从参数（文档 URL/token/类型）解析出文档引用，经 `resolve_document_reference` 解析（含 wiki 反查），
    再用 `client.docx.get_raw_content` 读取 docx 纯文本并原样回传，由模型自行总结。`lang` 控制文档中
    @ 提及等内容的展示语言（`0` 默认、`1` 中文、`2` 英文）。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"get_document_content"`。
        as_user: 是否以请求用户身份读取。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。
        lang: `docx.get_raw_content` 的内容语言；为空时使用接口默认值。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = get_document_content(description="读取文档正文")
        >>> tool.name, tool.requires_approval
        ('get_document_content', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "document_url": {"type": "string", "description": "Feishu docx/docs/wiki URL"},
            "document_token": {"type": "string", "description": "bare document token"},
            "doc_type": {"type": "string", "description": "docx|wiki; inferred when omitted"},
        },
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        reference = document_reference_from_mapping(arguments)
        if reference is None:
            return ToolResult(
                ToolOutcome.BLOCKED,
                content="document reference missing: provide document_url or document_token",
                is_error=True,
            )
        resolved = await resolve_document_reference(client, reference)
        if resolved.doc_type != "docx":
            return ToolResult(
                ToolOutcome.BLOCKED,
                content=f"reading raw content is not supported for document type {resolved.doc_type!r}",
                is_error=True,
            )
        content = await client.docx.get_raw_content(resolved.token, lang=lang)
        return ToolResult(
            ToolOutcome.COMPLETED,
            content={"token": resolved.token, "doc_type": resolved.doc_type, "content": content},
        )

    return Tool(name=name, description=description, input_schema=input_schema, handler=handler)


def get_message_thread(
    *,
    description: str,
    name: str = "get_message_thread",
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：抓取一条消息所在回复链的转录文本，返回一个 [feishu.agent.tools.Tool][]。

    以 `client.im.list_reply_chain` 沿 `parent_id` 向上抓取消息，按时间正序汇总，再用
    `message_transcript` 渲染为「发送者: 文本」逐行转录并回传，由模型自行总结。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"get_message_thread"`。
        as_user: 是否以请求用户身份读取。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = get_message_thread(description="读取消息线程")
        >>> tool.name, tool.requires_approval
        ('get_message_thread', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "tail message id (starts with om_)"},
            "max_items": {"type": "integer", "description": "maximum messages to walk up the chain"},
            "max_chars": {"type": "integer", "description": "cumulative body length cap"},
        },
        "required": ["message_id"],
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)
        messages = await client.im.list_reply_chain(
            arguments["message_id"],
            max_items=arguments.get("max_items", 20),
            max_chars=arguments.get("max_chars", 20_000),
            oldest_first=True,
        )
        if not messages:
            return ToolResult(ToolOutcome.INFORMATIONAL, content="message thread is empty")
        transcript = message_transcript(messages)
        return ToolResult(ToolOutcome.COMPLETED, content={"transcript": transcript})

    return Tool(name=name, description=description, input_schema=input_schema, handler=handler)


def get_meeting_record(
    *,
    description: str,
    name: str = "get_meeting_record",
    as_user: bool = True,
    auth_scopes: Sequence[str] = (),
    timezone: str = "Asia/Shanghai",
    lang: int | None = None,
) -> Tool:
    r"""
    读类工厂：解析一场会议并抓取其会议纪要正文，返回一个 [feishu.agent.tools.Tool][]。

    优先从参数里直接解析纪要文档引用（URL/token）；否则按 `meeting_id`（`client.vc.meetings.get`）或
    `meeting_no` + `start_time`/`end_time`（`client.vc.meetings.list_by_no`，时间经 `unix_seconds` 以
    `timezone` 归一）解析出会议，再从会议对象里取纪要引用。最终以 `client.docx.get_raw_content` 读取纪要
    docx 纯文本并回传，由模型自行总结。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"get_meeting_record"`。
        as_user: 是否以请求用户身份读取。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书权限范围。
        timezone: 解析 `start_time`/`end_time` 时使用的时区。默认为 `"Asia/Shanghai"`。
        lang: `docx.get_raw_content` 的内容语言；为空时使用接口默认值。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = get_meeting_record(description="读取会议记录")
        >>> tool.name, tool.requires_approval
        ('get_meeting_record', False)
    """
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "meeting_id": {"type": "string", "description": "internal meeting id"},
            "meeting_no": {"type": "string", "description": "9-digit meeting number"},
            "start_time": {"type": "string", "description": "ISO datetime; required with meeting_no"},
            "end_time": {"type": "string", "description": "ISO datetime; required with meeting_no"},
            "meeting_note_token": {"type": "string", "description": "meeting-notes document token"},
            "document_url": {"type": "string", "description": "meeting-notes document URL"},
            "max_items": {"type": "integer", "description": "max meetings to scan for meeting_no lookups"},
        },
        "additionalProperties": False,
    }

    async def handler(**arguments: Any) -> ToolResult:
        client = await resolve_client(as_user=as_user)
        if client is None:
            return needs_user_auth(auth_scopes)

        meeting: dict[str, Any] = {}
        note_reference = meeting_note_reference_from_mapping(arguments)
        if note_reference is None:
            meeting = await _resolve_meeting(client, arguments, timezone=await resolve_timezone(timezone))
            if not meeting:
                return ToolResult(
                    ToolOutcome.BLOCKED,
                    content="could not resolve meeting: provide meeting_id, or meeting_no with start_time/end_time",
                    is_error=True,
                )
            note_reference = meeting_note_reference_from_meeting(meeting)
        if note_reference is None:
            return ToolResult(
                ToolOutcome.BLOCKED,
                content="this meeting has no attached minutes/notes document",
                is_error=True,
            )

        resolved = await resolve_document_reference(client, note_reference)
        if resolved.doc_type != "docx":
            return ToolResult(
                ToolOutcome.BLOCKED,
                content=f"reading raw content is not supported for note type {resolved.doc_type!r}",
                is_error=True,
            )
        content = await client.docx.get_raw_content(resolved.token, lang=lang)
        return ToolResult(
            ToolOutcome.COMPLETED,
            content={"meeting": meeting or None, "note_token": resolved.token, "content": content},
        )

    return Tool(name=name, description=description, input_schema=input_schema, handler=handler)


async def _resolve_meeting(client: Any, arguments: dict[str, Any], *, timezone: str) -> dict[str, Any]:
    r"""通过 `meeting_id` 或 `meeting_no` + 时间窗口解析会议对象；无法解析时返回 `{}`。"""
    meeting_id = (arguments.get("meeting_id") or "").strip()
    if meeting_id:
        result = await client.vc.meetings.get(meeting_id, with_participants=True)
        meeting = result.get("meeting") or result
        return dict(meeting) if isinstance(meeting, dict) else {}

    meeting_no = (arguments.get("meeting_no") or "").strip()
    start_time = arguments.get("start_time")
    end_time = arguments.get("end_time")
    if not meeting_no or not start_time or not end_time:
        return {}
    meetings = await client.vc.meetings.list_by_no(
        meeting_no,
        str(unix_seconds(start_time, timezone=timezone)),
        str(unix_seconds(end_time, timezone=timezone)),
        max_items=arguments.get("max_items", 10),
    )
    if not meetings:
        return {}
    with_note = [item for item in meetings if meeting_note_reference_from_meeting(item)]
    chosen = with_note[0] if with_note else meetings[0]
    return dict(chosen) if isinstance(chosen, dict) else {}


__all__ = [
    "get_document_content",
    "get_meeting_record",
    "get_message_thread",
    "search_documents",
]
