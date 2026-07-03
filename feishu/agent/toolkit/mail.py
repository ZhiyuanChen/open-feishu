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

r"""邮箱工具工厂：列邮件、读邮件、总结邮件、发邮件。详见 [feishu.agent.toolkit][]。"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Callable, Sequence
from typing import Any

from ..result import ToolOutcome, ToolResult
from ..summarization import TextSummaryRequest
from ..tools import Tool, ToolValidationError
from ._base import needs_user_auth, resolve_client

_MAIL_ADDRESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mail_address": {"type": "string", "description": "邮箱地址，例如 alice@example.com。"},
        "name": {"type": "string", "description": "可选的收件人显示名。"},
    },
    "required": ["mail_address"],
    "additionalProperties": False,
}

_MAIL_ATTACHMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "附件对象，按飞书 mail send 接口传入；常见字段包括 file_name、content_type、content。",
}
_CURRENT_USER_MAILBOX_ID = "me"


async def _user_mail_client(scopes: Sequence[str]) -> Any | ToolResult:
    client = await resolve_client(as_user=True)
    if client is not None:
        return client.mail
    result = needs_user_auth(scopes)
    result.content = "飞书邮箱需要以你的身份授权后才能访问。"
    result.is_error = True
    return result


def list_mail_messages(
    *,
    description: str,
    name: str = "list_mail_messages",
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：列出当前用户邮箱中的邮件 ID，返回一个 [feishu.agent.tools.Tool][]。

    工具始终以请求用户身份访问固定邮箱 `"me"`，不会让模型指定任意 `user_mailbox_id`。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"list_mail_messages"`。
        auth_scopes: 缺少授权时申请的飞书邮箱权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = list_mail_messages(description="列出邮件")
        >>> tool.name, tool.requires_approval
        ('list_mail_messages', False)
    """

    async def handler(**arguments: Any) -> ToolResult:
        mail = await _user_mail_client(auth_scopes)
        if isinstance(mail, ToolResult):
            return mail
        messages = await mail.messages.list(
            _CURRENT_USER_MAILBOX_ID,
            page_size=arguments.get("page_size", 20),
            max_items=arguments.get("max_items"),
            folder_id=arguments.get("folder_id"),
            only_unread=arguments.get("only_unread"),
            label_id=arguments.get("label_id"),
        )
        return ToolResult(ToolOutcome.COMPLETED, content={"message_ids": messages})

    return Tool(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "description": "每页数量，飞书邮箱接口上限为 20。"},
                "max_items": {"type": "integer", "description": "最多返回多少封邮件；为空表示按接口翻完。"},
                "folder_id": {"type": "string", "description": "文件夹 ID，例如 INBOX。"},
                "only_unread": {"type": "boolean", "description": "是否只列未读邮件。"},
                "label_id": {"type": "string", "description": "标签 ID，例如 FLAGGED。"},
            },
            "additionalProperties": False,
        },
        handler=handler,
        auth_scopes=tuple(auth_scopes),
    )


def search_mail_messages(
    *,
    description: str,
    name: str = "search_mail_messages",
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：搜索当前用户邮箱中的邮件，返回一个 [feishu.agent.tools.Tool][]。

    搜索范围固定为请求用户邮箱 `"me"`；过滤条件直接透传给飞书邮箱 search 接口。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"search_mail_messages"`。
        auth_scopes: 缺少授权时申请的飞书邮箱权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = search_mail_messages(description="搜索邮件")
        >>> tool.name
        'search_mail_messages'
    """

    async def handler(**arguments: Any) -> ToolResult:
        mail = await _user_mail_client(auth_scopes)
        if isinstance(mail, ToolResult):
            return mail
        messages = await mail.messages.search(
            _CURRENT_USER_MAILBOX_ID,
            query=arguments.get("query"),
            filter=arguments.get("filter"),
            page_size=arguments.get("page_size", 15),
            max_items=arguments.get("max_items"),
        )
        return ToolResult(ToolOutcome.COMPLETED, content={"messages": messages})

    return Tool(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词，例如主题、正文或联系人关键字。"},
                "filter": {
                    "type": "object",
                    "description": "飞书邮箱搜索过滤条件，例如 from、to、folder、is_unread、create_time。",
                },
                "page_size": {"type": "integer", "description": "每页数量，飞书邮箱 search 接口上限为 15。"},
                "max_items": {"type": "integer", "description": "最多返回多少条搜索结果；为空表示按接口翻完。"},
            },
            "additionalProperties": False,
        },
        handler=handler,
        auth_scopes=tuple(auth_scopes),
    )


def get_mail_message(
    *,
    description: str,
    name: str = "get_mail_message",
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：读取当前用户邮箱中的一封邮件详情，返回一个 [feishu.agent.tools.Tool][]。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"get_mail_message"`。
        auth_scopes: 缺少授权时申请的飞书邮箱权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = get_mail_message(description="读取邮件")
        >>> tool.name
        'get_mail_message'
    """

    async def handler(**arguments: Any) -> ToolResult:
        mail = await _user_mail_client(auth_scopes)
        if isinstance(mail, ToolResult):
            return mail
        message = await mail.messages.get(
            _CURRENT_USER_MAILBOX_ID,
            arguments["message_id"],
            format=arguments.get("format"),
        )
        return ToolResult(ToolOutcome.COMPLETED, content=message)

    return Tool(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "邮件 ID。"},
                "format": {
                    "type": "string",
                    "enum": ["full", "plain_text_full", "metadata"],
                    "description": "返回内容格式；不确定时可省略。",
                },
            },
            "required": ["message_id"],
            "additionalProperties": False,
        },
        handler=handler,
        auth_scopes=tuple(auth_scopes),
    )


def summarize_mail_message(
    *,
    description: str,
    text_summarizer: Callable[..., Any] | None,
    name: str = "summarize_mail_message",
    auth_scopes: Sequence[str] = (),
    max_body_chars: int = 4000,
    max_summary_chars: int = 2000,
) -> Tool:
    r"""
    读类工厂：读取并总结当前用户邮箱中的一封邮件，返回一个 [feishu.agent.tools.Tool][]。

    邮件全文只交给注入的快速文本摘要器，主模型只收到摘要和元数据。

    Args:
        description: 工具描述（产品本地化文案）。
        text_summarizer: 纯文本摘要器，通常由 fast/flash 模型提供。
        name: 工具名。默认为 `"summarize_mail_message"`。
        auth_scopes: 缺少授权时申请的飞书邮箱权限范围。
        max_body_chars: 传给摘要器的单封邮件正文最大字符数。
        max_summary_chars: 摘要最大字符数。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = summarize_mail_message(description="总结邮件", text_summarizer=None)
        >>> tool.name
        'summarize_mail_message'
    """

    async def handler(**arguments: Any) -> ToolResult:
        if text_summarizer is None:
            return _mail_summarizer_missing_result()
        message_id = arguments["message_id"]
        mail = await _user_mail_client(auth_scopes)
        if isinstance(mail, ToolResult):
            return mail
        messages, errors = await _read_mail_summary_items(
            mail,
            _CURRENT_USER_MAILBOX_ID,
            [message_id],
            max_body_chars=max_body_chars,
        )
        if not messages:
            return ToolResult(
                ToolOutcome.FAILED,
                content={
                    "summary": "邮件读取失败，暂时无法总结。",
                    "message": {"message_id": message_id},
                    "errors": errors,
                },
                is_error=True,
            )
        request = TextSummaryRequest(
            kind="mail_message",
            instruction="总结这封邮件。提炼主题、关键事实、待办、紧急程度和建议回复。不要输出原始全文。",
            text=_mail_digest_text(messages),
            max_chars=max_summary_chars,
        )
        summary = await _call_text_summarizer(text_summarizer, request)
        if not summary:
            return ToolResult(
                ToolOutcome.FAILED,
                content={
                    "summary": "邮件已读取，但 fast 文本摘要器暂时不可用。",
                    "message": _mail_public_item(messages[0]),
                    "errors": errors,
                },
                is_error=True,
            )
        return ToolResult(
            ToolOutcome.COMPLETED,
            content={"summary": summary, "message": _mail_public_item(messages[0]), "errors": errors},
        )

    return Tool(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "邮件 ID。"},
            },
            "required": ["message_id"],
            "additionalProperties": False,
        },
        handler=handler,
        auth_scopes=tuple(auth_scopes),
    )


def summarize_mail_messages(
    *,
    description: str,
    text_summarizer: Callable[..., Any] | None,
    name: str = "summarize_mail_messages",
    auth_scopes: Sequence[str] = (),
    default_max_items: int = 10,
    max_items_limit: int = 20,
    max_body_chars: int = 4000,
    max_summary_chars: int = 2000,
) -> Tool:
    r"""
    读类工厂：读取并总结当前用户邮箱中的多封邮件，返回一个 [feishu.agent.tools.Tool][]。

    未显式给出 `message_ids` 时，工具会按筛选条件读取最近邮件再汇总。

    Args:
        description: 工具描述（产品本地化文案）。
        text_summarizer: 纯文本摘要器，通常由 fast/flash 模型提供。
        name: 工具名。默认为 `"summarize_mail_messages"`。
        auth_scopes: 缺少授权时申请的飞书邮箱权限范围。
        default_max_items: 未指定数量时默认总结的邮件数。
        max_items_limit: 单次可总结邮件数上限。
        max_body_chars: 传给摘要器的单封邮件正文最大字符数。
        max_summary_chars: 摘要最大字符数。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = summarize_mail_messages(description="总结最近邮件", text_summarizer=None)
        >>> tool.name
        'summarize_mail_messages'
    """

    async def handler(**arguments: Any) -> ToolResult:
        if text_summarizer is None:
            return _mail_summarizer_missing_result()
        limit = _bounded_count(arguments.get("max_items"), default=default_max_items, upper=max_items_limit)
        mail = await _user_mail_client(auth_scopes)
        if isinstance(mail, ToolResult):
            return mail
        raw_ids: Sequence[Any]
        message_ids = arguments.get("message_ids")
        if message_ids:
            raw_ids = message_ids
        else:
            raw_ids = await mail.messages.list(
                _CURRENT_USER_MAILBOX_ID,
                page_size=min(limit, 20),
                max_items=limit,
                folder_id=arguments.get("folder_id"),
                only_unread=arguments.get("only_unread"),
            )
        ids = []
        for item in raw_ids:
            message_id = _mail_message_id(item)
            if message_id:
                ids.append(message_id)
        ids = ids[:limit]
        if not ids:
            return ToolResult(
                ToolOutcome.COMPLETED,
                content={"summary": "没有找到符合条件的邮件。", "message_count": 0, "messages": []},
            )
        messages, errors = await _read_mail_summary_items(
            mail,
            _CURRENT_USER_MAILBOX_ID,
            ids,
            max_body_chars=max_body_chars,
        )
        if not messages:
            return ToolResult(
                ToolOutcome.FAILED,
                content={
                    "summary": "邮件读取失败，暂时无法总结。",
                    "message_count": 0,
                    "messages": [],
                    "errors": errors,
                },
                is_error=True,
            )
        request = TextSummaryRequest(
            kind="mail_digest",
            instruction=(
                "总结这些邮件。按重要性提炼：总体概览、每封邮件的一句话摘要、需要用户处理的待办、"
                "紧急程度和建议回复。不要输出原始全文。"
            ),
            text=_mail_digest_text(messages),
            max_chars=max_summary_chars,
        )
        summary = await _call_text_summarizer(text_summarizer, request)
        if not summary:
            return ToolResult(
                ToolOutcome.FAILED,
                content={
                    "summary": "邮件已读取，但 fast 文本摘要器暂时不可用。",
                    "message_count": len(messages),
                    "messages": [_mail_public_item(item) for item in messages],
                    "errors": errors,
                },
                is_error=True,
            )
        return ToolResult(
            ToolOutcome.COMPLETED,
            content={
                "summary": summary,
                "message_count": len(messages),
                "messages": [_mail_public_item(item) for item in messages],
                "errors": errors,
            },
        )

    return Tool(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": {
                "message_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要总结的邮件 ID 列表；为空时读取最近邮件。",
                },
                "max_items": {"type": "integer", "description": "要总结的邮件数量，默认 10，上限 20。"},
                "folder_id": {"type": "string", "description": "文件夹 ID，例如 INBOX。"},
                "only_unread": {"type": "boolean", "description": "是否只总结未读邮件。"},
            },
            "additionalProperties": False,
        },
        handler=handler,
        auth_scopes=tuple(auth_scopes),
    )


def _mail_summarizer_missing_result() -> ToolResult:
    return ToolResult(
        ToolOutcome.FAILED,
        content="邮件摘要器未配置；请在构建工作区工具包时注入 text_summarizer。",
        is_error=True,
    )


async def _read_mail_summary_items(
    mail: Any,
    user_mailbox_id: str,
    message_ids: Sequence[str],
    *,
    max_body_chars: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    details = await asyncio.gather(
        *(mail.messages.get(user_mailbox_id, message_id, format="plain_text_full") for message_id in message_ids),
        return_exceptions=True,
    )
    messages: list[dict[str, Any]] = []
    errors: list[str] = []
    for message_id, detail in zip(message_ids, details, strict=False):
        if isinstance(detail, Exception):
            errors.append(message_id)
            continue
        messages.append(_mail_summary_item(message_id, detail, max_body_chars=max_body_chars))
    return messages, errors


def _bounded_count(value: int | None, *, default: int, upper: int) -> int:
    if value is None:
        value = default
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = default
    return max(1, min(count, upper))


def _mail_message_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("message_id", "id"):
            message_id = value.get(key)
            if message_id:
                return str(message_id)
    return None


async def _call_text_summarizer(text_summarizer: Callable[..., Any], request: TextSummaryRequest) -> str | None:
    result = text_summarizer(request)
    if inspect.isawaitable(result):
        result = await result
    return result if isinstance(result, str) and result.strip() else None


def _mail_summary_item(message_id: str, raw: Any, *, max_body_chars: int) -> dict[str, Any]:
    message = _mail_payload(raw)
    body = _mail_body_text(message)
    if len(body) > max_body_chars:
        body = body[: max(0, max_body_chars - 1)].rstrip() + "…"
    return {
        "message_id": str(_mail_field(message, "message_id", "id") or message_id),
        "subject": _as_text(_mail_field(message, "subject", "title")),
        "from": _address_text(_mail_field(message, "from", "sender", "head_from")),
        "to": _address_text(_mail_field(message, "to", "recipients")),
        "cc": _address_text(_mail_field(message, "cc")),
        "send_time": _as_text(_mail_field(message, "send_time", "sent_time", "date", "created_time")),
        "body": body,
    }


def _mail_public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "body" and value not in (None, "", [])}


def _mail_digest_text(messages: list[dict[str, Any]]) -> str:
    blocks = []
    for index, message in enumerate(messages, start=1):
        blocks.append(
            "\n".join(
                (
                    f"[{index}]",
                    f"Message ID: {message.get('message_id') or ''}",
                    f"Subject: {message.get('subject') or '(no subject)'}",
                    f"From: {message.get('from') or ''}",
                    f"To: {message.get('to') or ''}",
                    f"CC: {message.get('cc') or ''}",
                    f"Time: {message.get('send_time') or ''}",
                    f"Body:\n{message.get('body') or ''}",
                )
            )
        )
    return "\n\n".join(blocks)


def _mail_payload(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    for key in ("message", "data"):
        value = raw.get(key)
        if isinstance(value, dict):
            if isinstance(value.get("message"), dict):
                return value["message"]
            return value
    return raw


def _mail_field(message: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in message and message[key] not in (None, ""):
            return message[key]
    return None


def _mail_body_text(message: dict[str, Any]) -> str:
    for key in ("body_plain_text", "plain_text", "body_text", "text", "content"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    html = message.get("body_html") or message.get("html")
    if isinstance(html, str) and html.strip():
        return _strip_html(html)
    body = message.get("body")
    if isinstance(body, dict):
        return _mail_body_text(body)
    return _as_text(body)


def _strip_html(value: str) -> str:
    text = re.sub(r"<(br|p|div|li|tr|h[1-6])\b[^>]*>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _address_text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(filter(None, (_address_text(item) for item in value)))
    if isinstance(value, dict):
        name = _as_text(value.get("name") or value.get("display_name"))
        address = _as_text(value.get("mail_address") or value.get("email") or value.get("address"))
        if name and address:
            return f"{name} <{address}>"
        return name or address
    return _as_text(value)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, int | float | bool):
        return str(value)
    return json.dumps(value, ensure_ascii=False, default=str)


def list_mail_folders(
    *,
    description: str,
    name: str = "list_mail_folders",
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    读类工厂：列出当前用户邮箱的文件夹，返回一个 [feishu.agent.tools.Tool][]。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"list_mail_folders"`。
        auth_scopes: 缺少授权时申请的飞书邮箱权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = list_mail_folders(description="列出邮箱文件夹")
        >>> tool.name
        'list_mail_folders'
    """

    async def handler(**arguments: Any) -> ToolResult:
        mail = await _user_mail_client(auth_scopes)
        if isinstance(mail, ToolResult):
            return mail
        folders = await mail.folders.list(_CURRENT_USER_MAILBOX_ID, folder_type=arguments.get("folder_type"))
        return ToolResult(ToolOutcome.COMPLETED, content={"folders": folders})

    return Tool(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": {
                "folder_type": {"type": "integer", "description": "文件夹类型；为空时不筛选。"},
            },
            "additionalProperties": False,
        },
        handler=handler,
        auth_scopes=tuple(auth_scopes),
    )


def send_mail_message(
    *,
    description: str,
    name: str = "send_mail_message",
    requires_approval: bool = True,
    auth_scopes: Sequence[str] = (),
) -> Tool:
    r"""
    写类工厂：以当前用户身份发送一封邮件，返回一个需审批的 [feishu.agent.tools.Tool][]。

    `requires_approval=True` 时，[feishu.agent.loop.AgentEngine][] 会先发确认卡；用户批准后才执行发送。

    Args:
        description: 工具描述（产品本地化文案）。
        name: 工具名。默认为 `"send_mail_message"`。
        requires_approval: 是否需用户审批后执行。默认为 `True`。
        auth_scopes: 缺少授权时申请的飞书邮箱发送权限范围。

    Returns:
        可注册到 [feishu.agent.tools.ToolRegistry][] 的需审批 [feishu.agent.tools.Tool][]。

    Examples:
        >>> tool = send_mail_message(description="发送邮件")
        >>> tool.name, tool.requires_approval
        ('send_mail_message', True)
    """

    async def handler(**arguments: Any) -> ToolResult:
        to = arguments.get("to")
        subject = arguments.get("subject")
        raw = arguments.get("raw")
        if raw is None and (not to or subject is None):
            raise ToolValidationError("tool 'send_mail_message' requires 'raw' or both 'to' and 'subject'")
        mail = await _user_mail_client(auth_scopes)
        if isinstance(mail, ToolResult):
            return mail
        result = await mail.messages.send(
            _CURRENT_USER_MAILBOX_ID,
            subject=subject,
            to=to,
            raw=raw,
            cc=arguments.get("cc"),
            bcc=arguments.get("bcc"),
            body_plain_text=arguments.get("body_plain_text"),
            body_html=arguments.get("body_html"),
            attachments=arguments.get("attachments"),
            dedupe_key=arguments.get("dedupe_key"),
            head_from=arguments.get("head_from"),
        )
        return ToolResult(ToolOutcome.COMPLETED, content=result)

    return Tool(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": {
                "to": {"type": "array", "items": _MAIL_ADDRESS_SCHEMA, "description": "收件人列表。"},
                "cc": {"type": "array", "items": _MAIL_ADDRESS_SCHEMA, "description": "抄送人列表。"},
                "bcc": {"type": "array", "items": _MAIL_ADDRESS_SCHEMA, "description": "密送人列表。"},
                "subject": {"type": "string", "description": "邮件主题。"},
                "raw": {"type": "string", "description": "base64url 编码后的 MIME/EML 原文。"},
                "body_plain_text": {"type": "string", "description": "纯文本正文。"},
                "body_html": {"type": "string", "description": "HTML 正文。"},
                "attachments": {"type": "array", "items": _MAIL_ATTACHMENT_SCHEMA, "description": "附件列表。"},
                "dedupe_key": {"type": "string", "description": "可选去重键。"},
                "head_from": {"type": "object", "description": "可选发件人显示信息。"},
            },
            "additionalProperties": False,
        },
        handler=handler,
        requires_approval=requires_approval,
        auth_scopes=tuple(auth_scopes),
    )
