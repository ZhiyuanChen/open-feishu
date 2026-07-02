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
工具的按轮上下文：让工具处理函数在不改变其参数签名的前提下访问飞书客户端与用户态客户端。

[feishu.agent.loop.Agent][] 在处理每条消息 / 卡片回调前，把一个 [feishu.agent.context.ToolContext][] 设入一个
`contextvars.ContextVar`；工具处理函数（如 [feishu.agent.toolkit][] 中的工厂所产出者）经
[feishu.agent.context.current_tool_context][] 读取它。`contextvars` 的值会随 `await` 在同一任务内保持，并被
`asyncio.to_thread`（同步处理函数的执行方式）复制，因而读 / 写工具都能拿到正确的按轮上下文，而
[feishu.agent.tools.ToolRegistry.dispatch][] 的签名保持不变。
"""

from __future__ import annotations

import contextvars
import inspect
import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from ._callbacks import accepts_positional_arguments as _accepts_positional_arguments

logger = logging.getLogger("feishu")


@dataclass
class ToolContext:
    r"""
    一次工具调用的按轮上下文：租户客户端、触发事件、用户态 token 提供方与已解析的用户标识。

    `as_user` 借助 `user_tokens`（[feishu.auth][] 的用户态 token 提供方）解析「当前请求用户」的用户态飞书
    客户端，用于需以用户身份执行的读写；缺少提供方或有效授权时返回 `None`，由工具自行决定降级或要求授权。

    Examples:
        >>> ctx = ToolContext(client="<tenant-client>")
        >>> ctx.client
        '<tenant-client>'
    """

    client: Any | None = None
    event: Any | None = None
    user_tokens: Any | None = None
    user: dict[str, Any] = field(default_factory=dict)
    authorize_url_builder: Any | None = None
    shared_files: Any | None = None  # a SharedFileResolver: the only path from a file_id to bytes
    payment_accounts: Any | None = None  # a PaymentAccountResolver: account_id handle -> account value
    timezone: str | Callable[..., Any] | None = None

    async def as_user(self) -> Any | None:
        r"""解析当前请求用户的用户态飞书客户端；无提供方 / 无授权时返回 `None`。"""
        if self.user_tokens is None:
            return None
        user = self.user or _user_from_event(self.event)
        if not user:
            return None
        return await self.user_tokens.as_user(user)

    async def has_user_auth(self, scopes: Sequence[str]) -> bool:
        r"""判断当前请求用户是否已有有效授权；提供方支持 scope 检查时要求覆盖 `scopes`。"""
        if not scopes:
            return True
        if self.user_tokens is None:
            return False
        user = self.user or _user_from_event(self.event)
        if not user:
            return False
        checker = getattr(self.user_tokens, "has_scopes", None)
        if checker is not None:
            result = checker(user, tuple(scopes))
            if inspect.isawaitable(result):
                result = await result
            return bool(result)
        return await self.as_user() is not None

    def requesting_user(self) -> dict[str, Any]:
        r"""
        返回发起本轮请求的用户标识（open_id / union_id / user_id 的子集）。

        优先使用显式传入的 `user`，否则从触发事件解析。用户主体类工具应据此把操作限定在「请求用户本人」，
        防止被越权指向他人（zero-trust / least-privilege）。
        """
        return self.user or _user_from_event(self.event)

    def authorize_url(self, scopes: Sequence[str] = ()) -> str | None:
        r"""
        为当前用户构造授权跳转 URL，供工具在缺少用户授权时回传给模型转述。

        URL 的构造（重定向地址、`state` 签名等）由产品侧注入的 `authorize_url_builder` 负责，SDK 不内置任何
        产品配置；未注入或无法解析用户时返回 `None`。

        Args:
            scopes: 本次操作所需的飞书权限范围。

        Returns:
            授权 URL，或在未配置 / 无用户身份时返回 `None`。
        """
        if self.authorize_url_builder is None:
            return None
        user = self.user or _user_from_event(self.event)
        if not user:
            return None
        return self.authorize_url_builder(user, tuple(scopes))

    async def current_timezone(self, default: str | None = None) -> str | None:
        r"""返回当前轮次的时区，优先使用飞书事件上下文，其次使用产品默认值。"""
        event_timezone = _timezone_from_event(self.event)
        if event_timezone:
            return event_timezone
        timezone = self.timezone
        if callable(timezone):
            try:
                timezone = timezone(self.event) if _accepts_positional_arguments(timezone, 1) else timezone()
                if inspect.isawaitable(timezone):
                    timezone = await timezone
            except Exception:
                logger.debug("tool context timezone resolver failed", exc_info=True)
                return default
        if timezone:
            return str(timezone)
        return default


_CURRENT: contextvars.ContextVar[ToolContext | None] = contextvars.ContextVar("feishu_tool_context", default=None)


def current_tool_context() -> ToolContext:
    r"""
    读取当前生效的 [feishu.agent.context.ToolContext][]；未设置时返回一个空上下文。

    Returns:
        当前任务内由 [feishu.agent.context.use_tool_context][] 设入的上下文；未设置时返回 `ToolContext()`
        （`client` 为 `None`），由工具自行处理缺省。

    Examples:
        >>> current_tool_context().client is None
        True
    """
    return _CURRENT.get() or ToolContext()


@contextmanager
def use_tool_context(context: ToolContext) -> Iterator[None]:
    r"""
    在 `with` 作用域内将 `context` 设为当前的 [feishu.agent.context.ToolContext][]，退出时恢复。

    Args:
        context: 本轮生效的工具上下文。

    Examples:
        >>> ctx = ToolContext(client="c1")
        >>> with use_tool_context(ctx):
        ...     current_tool_context().client
        'c1'
        >>> current_tool_context().client is None
        True
    """
    token = _CURRENT.set(context)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def _user_from_event(event: Any) -> dict[str, Any]:
    body = getattr(event, "body", None) or {}
    sender = (body.get("sender") or {}).get("sender_id") or {}
    if sender:
        return {key: sender[key] for key in ("open_id", "union_id", "user_id") if sender.get(key)}
    operator = body.get("operator") or {}
    return {key: operator[key] for key in ("open_id", "union_id", "user_id") if operator.get(key)}


def _timezone_from_event(event: Any) -> str | None:
    body = getattr(event, "body", None) or {}
    nodes = [
        body,
        body.get("context") or {},
        body.get("action") or {},
        (body.get("action") or {}).get("option") or {},
    ]
    for node in nodes:
        if not isinstance(node, dict):
            continue
        for key in ("timezone", "time_zone", "timeZone"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


__all__ = [
    "ToolContext",
    "current_tool_context",
    "use_tool_context",
]
