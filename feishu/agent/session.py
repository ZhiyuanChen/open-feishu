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

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .llm import Message


@runtime_checkable
class SessionStore(Protocol):
    r"""
    会话历史存储协议，是自定义持久化后端的扩展契约。

    [feishu.agent.loop.Agent][] 通过该协议读写各会话的对话历史；内置实现为
    [feishu.agent.session.InMemorySessionStore][]，可自行实现该协议接入数据库等持久化后端。该协议标注了
    `runtime_checkable`，可用 `isinstance` 校验实现是否符合契约。

    Examples:
        >>> isinstance(InMemorySessionStore(), SessionStore)
        True
    """

    async def get(self, session_id: str) -> list[Message]:
        r"""读取指定会话的全部历史消息。"""
        ...

    async def append(self, session_id: str, *messages: Message) -> None:
        r"""向指定会话追加一条或多条消息。"""
        ...

    async def set(self, session_id: str, messages: list[Message]) -> None:
        r"""以给定的消息列表整体替换指定会话的历史。"""
        ...


class InMemorySessionStore:
    r"""
    基于内存的 [feishu.agent.session.SessionStore][] 实现。

    将各会话历史保存在进程内字典中，写操作以锁保护因而并发安全。仅适用于单进程、可接受重启即丢失历史的
    场景；生产环境请自行实现 [feishu.agent.session.SessionStore][] 接入持久化后端。

    Examples:
        >>> import asyncio
        >>> from feishu.agent.llm import Message, TextPart
        >>> store = InMemorySessionStore()
        >>> async def demo():
        ...     await store.append("oc_1", Message(role="user", content=[TextPart(text="你好")]))
        ...     history = await store.get("oc_1")
        ...     return history[0].content[0].text
        >>> asyncio.run(demo())
        '你好'
    """

    def __init__(self) -> None:
        self._store: dict[str, list[Message]] = {}
        self._lock = asyncio.Lock()

    async def get(self, session_id: str) -> list[Message]:
        r"""
        读取指定会话的全部历史消息。

        Args:
            session_id: 会话标识。

        Returns:
            历史消息的副本；会话不存在时返回空列表。对返回列表的修改不会影响内部状态。
        """
        return list(self._store.get(session_id, []))

    async def append(self, session_id: str, *messages: Message) -> None:
        r"""
        向指定会话追加一条或多条消息。

        Args:
            session_id: 会话标识。
            *messages: 待追加的消息。
        """
        async with self._lock:
            bucket = self._store.get(session_id)
            if bucket is None:  # double-checked read after acquiring the lock
                bucket = self._store.setdefault(session_id, [])
            bucket.extend(messages)

    async def set(self, session_id: str, messages: list[Message]) -> None:
        r"""
        以给定的消息列表整体替换指定会话的历史。

        Args:
            session_id: 会话标识。
            messages: 用于替换的消息列表，将被拷贝保存。
        """
        async with self._lock:
            self._store[session_id] = list(messages)


@dataclass
class PendingApproval:
    r"""
    一次挂起的工具审批，记录恢复对话所需的全部上下文。

    当工具的 `requires_approval` 为 `True` 时，[feishu.agent.loop.Agent][] 会创建该记录并发送审批卡片；
    用户在卡片上批准或拒绝后，依据其中保存的会话与工具调用信息恢复本轮对话。

    Examples:
        >>> approval = PendingApproval(
        ...     approval_id="ap_1",
        ...     session_id="oc_1",
        ...     tool_call_id="c1",
        ...     tool_name="deploy",
        ...     arguments={"env": "prod"},
        ... )
        >>> approval.tool_name
        'deploy'
    """

    approval_id: str
    session_id: str
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]


@runtime_checkable
class PendingApprovalStore(Protocol):
    r"""
    挂起审批存储协议，是自定义审批持久化后端的扩展契约。

    [feishu.agent.loop.Agent][] 通过该协议保存与取回挂起的 [feishu.agent.session.PendingApproval][]；
    内置实现为 [feishu.agent.session.InMemoryPendingApprovalStore][]。该协议标注了 `runtime_checkable`，
    可用 `isinstance` 校验实现是否符合契约。

    Examples:
        >>> isinstance(InMemoryPendingApprovalStore(), PendingApprovalStore)
        True
    """

    async def put(self, approval: PendingApproval) -> None:
        r"""保存一次挂起的审批。"""
        ...

    async def pop(self, approval_id: str) -> PendingApproval | None:
        r"""按 `approval_id` 取出并移除一次挂起的审批，不存在时返回 `None`。"""
        ...


class InMemoryPendingApprovalStore:
    r"""
    基于内存的 [feishu.agent.session.PendingApprovalStore][] 实现。

    将挂起审批保存在进程内字典中，写操作以锁保护因而并发安全。每个审批仅可被取出一次，取出即移除，可天然
    防止重复执行。仅适用于单进程场景；生产环境请自行实现 [feishu.agent.session.PendingApprovalStore][]。

    Examples:
        >>> import asyncio
        >>> store = InMemoryPendingApprovalStore()
        >>> approval = PendingApproval(
        ...     approval_id="ap_1",
        ...     session_id="oc_1",
        ...     tool_call_id="c1",
        ...     tool_name="deploy",
        ...     arguments={"env": "prod"},
        ... )
        >>> async def demo():
        ...     await store.put(approval)
        ...     first = await store.pop("ap_1")
        ...     second = await store.pop("ap_1")
        ...     return first.tool_name, second
        >>> asyncio.run(demo())
        ('deploy', None)
    """

    def __init__(self) -> None:
        self._store: dict[str, PendingApproval] = {}
        self._lock = asyncio.Lock()

    async def put(self, approval: PendingApproval) -> None:
        r"""
        保存一次挂起的审批。

        Args:
            approval: 待保存的 [feishu.agent.session.PendingApproval][]。
        """
        async with self._lock:
            self._store[approval.approval_id] = approval

    async def pop(self, approval_id: str) -> PendingApproval | None:
        r"""
        按 `approval_id` 取出并移除一次挂起的审批。

        Args:
            approval_id: 审批标识。

        Returns:
            对应的 [feishu.agent.session.PendingApproval][]；不存在或已被取出时返回 `None`。
        """
        async with self._lock:
            return self._store.pop(approval_id, None)
