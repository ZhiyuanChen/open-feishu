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

r"""
工具库工厂的共享基件：解析（用户态）客户端、构造需授权结果。

各产品模块（[feishu.agent.toolkit.calendar][] 等）的工厂据此产出 [feishu.agent.tools.Tool][]：处理函数经
[feishu.agent.context.current_tool_context][] 拿到（用户态）飞书客户端，缺少授权时返回携带授权链接的
`NEEDS_USER_AUTH` 结果，交由模型转述。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...errors import is_permission_error, permission_subjects
from ..context import current_tool_context
from ..result import ToolOutcome, ToolResult
from ..tools import Tool


async def resolve_client(*, as_user: bool) -> Any | None:
    r"""按 `as_user` 解析当前这轮的（用户态或租户）飞书客户端；无有效用户授权时返回 `None`。"""
    context = current_tool_context()
    return (await context.as_user()) if as_user else context.client


def reauth_on_permission_error(tool: Tool, scopes: Sequence[str]) -> Tool:
    r"""把飞书缺失权限错误转换为授权交接，而不是直接暴露成不透明失败。"""
    if not scopes:
        return tool

    async def handler(**arguments: Any) -> Any:
        try:
            result = tool.handler(**arguments)
            if hasattr(result, "__await__"):
                return await result
            return result
        except Exception as exc:
            if not is_permission_error(exc):
                raise
            missing = ", ".join(permission_subjects(exc))
            content = "飞书权限不足，需要重新授权后再试。"
            if missing:
                content = f"{content}缺少权限：{missing}"
            return ToolResult(
                ToolOutcome.NEEDS_USER_AUTH,
                content=content,
                authorize_url=current_tool_context().authorize_url(scopes),
                auth_scopes=tuple(scopes),
                is_error=True,
            )

    return Tool(
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema,
        handler=handler,
        requires_approval=tool.requires_approval,
        auth_scopes=tuple(scopes),
    )


async def resolve_timezone(default: str = "Asia/Shanghai") -> str:
    r"""解析当前工具调用的时区；取不到事件时区时回退到产品默认值。"""
    return await current_tool_context().current_timezone(default) or default


def needs_user_auth(scopes: Sequence[str]) -> ToolResult:
    r"""构造一个 `NEEDS_USER_AUTH` 结果，并附上（若产品已配置）当前用户的授权链接。"""
    return ToolResult(
        ToolOutcome.NEEDS_USER_AUTH,
        content="user authorization required",
        authorize_url=current_tool_context().authorize_url(scopes),
        auth_scopes=tuple(scopes),
    )


def requesting_user() -> dict[str, Any]:
    r"""返回发起本轮请求的用户标识；用户主体类工具据此将操作限定为「请求用户本人」（最小权限）。"""
    return current_tool_context().requesting_user()


def requesting_user_id(kind: str = "open_id") -> str | None:
    r"""返回请求用户的指定标识（默认 `open_id`）；无则返回 `None`。"""
    return requesting_user().get(kind)


async def resolve_shared_file_bytes(file_id: str) -> tuple[bytes, Any] | ToolResult:
    r"""
    把一个 `file_id` 解析为 `(bytes, SharedFile)`，严格按请求用户隔离；不可用时返回一个 `BLOCKED` 结果。

    所有「消费用户分享文件」的工具都应经由此函数取字节——它是 `file_id -> bytes` 的唯一入口。请求用户取自
    [feishu.agent.context.ToolContext.requesting_user][]（绝不可由模型指定），解析委托给按轮上下文中的
    [feishu.agent.shared_files.SharedFileResolver][]。未配置句柄解析器、无法确定请求用户、或文件不可用
    （非本人 / 已过期 / 源失效 / 超限）时返回 `BLOCKED` 的 [feishu.agent.result.ToolResult][]，调用方应直接返回它；
    否则返回 `(bytes, SharedFile)`。模型只传 `file_id`，永远拿不到字节。
    """
    context = current_tool_context()
    resolver = context.shared_files
    if resolver is None:
        return ToolResult(ToolOutcome.BLOCKED, content="shared files are not configured", is_error=True)
    user = context.requesting_user()
    if not user:
        return ToolResult(ToolOutcome.BLOCKED, content="cannot resolve the requesting user's identity", is_error=True)
    resolved = await resolver.read_bytes(user, file_id)
    if resolved is None:
        return ToolResult(ToolOutcome.BLOCKED, content=f"shared file is not available: {file_id}", is_error=True)
    return resolved


async def list_recent_shared_files(limit: int = 10) -> list[Any]:
    r"""返回请求用户最近分享的文件句柄（仅元数据），按轮上下文未配置句柄解析器时返回空列表。"""
    context = current_tool_context()
    resolver = context.shared_files
    if resolver is None:
        return []
    user = context.requesting_user()
    if not user:
        return []
    return await resolver.recent(user, limit=limit)


async def list_recent_payment_accounts(*, approval_code: str | None = None, limit: int = 10) -> list[Any]:
    r"""返回请求用户本人的收款账户句柄（脱敏标签），未配置账户解析器或无法确定用户时返回空列表。"""
    context = current_tool_context()
    resolver = context.payment_accounts
    if resolver is None:
        return []
    user = context.requesting_user()
    if not user:
        return []
    return await resolver.recent(user, approval_code=approval_code, limit=limit)


async def resolve_payment_account(account_id: str) -> dict[str, Any] | ToolResult:
    r"""
    把一个收款账户句柄解析为可提交的账户控件值，严格按请求用户隔离；不可用时返回 `BLOCKED`。

    模型只传 `account_id` 句柄，永远拿不到完整卡号。完整值由当前轮上下文中的
    [feishu.agent.payment_accounts.PaymentAccountResolver][] 在服务端内存中还原。
    """
    context = current_tool_context()
    resolver = context.payment_accounts
    if resolver is None:
        return ToolResult(ToolOutcome.BLOCKED, content="payment accounts are not configured", is_error=True)
    user = context.requesting_user()
    if not user:
        return ToolResult(ToolOutcome.BLOCKED, content="cannot resolve the requesting user's identity", is_error=True)
    value = await resolver.resolve(user, account_id)
    if not value:
        return ToolResult(ToolOutcome.BLOCKED, content=f"payment account is not available: {account_id}", is_error=True)
    return value
