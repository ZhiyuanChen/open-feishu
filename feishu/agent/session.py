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
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, TypeVar, runtime_checkable

from .llm import Message

T = TypeVar("T")


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

    async def clear(self, session_id: str) -> None:
        r"""清空指定会话的历史（彻底删除，而非隐藏）。"""
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

    async def clear(self, session_id: str) -> None:
        r"""清空指定会话的历史（彻底删除该会话条目）。"""
        async with self._lock:
            self._store.pop(session_id, None)


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
    # Optional integrity / idempotency / ownership metadata. All default so that
    # a minimal PendingApproval(approval_id, session_id, tool_call_id, tool_name,
    # arguments) keeps working; durable, tamper-checked stores populate the rest.
    payload_sha256: str | None = None
    idempotency_key: str | None = None
    owner_user_keys: tuple[str, ...] = ()
    tenant_key: str | None = None
    chat_id: str | None = None
    created_message_id: str | None = None
    created_event_id: str | None = None
    created_at: int | None = None
    state: str = "awaiting_confirmation"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PendingAuthorization:
    r"""
    一次挂起的用户授权，记录 OAuth callback 后恢复原工具调用所需的上下文。

    当工具返回 `NEEDS_USER_AUTH` 时，[feishu.agent.loop.Agent][] 会创建该记录、发送授权卡片并挂起本轮；
    OAuth callback 完成并保存用户 token 后，产品调用 `Agent.resume_authorization`，依据其中保存的工具调用恢复对话。
    """

    authorization_id: str
    session_id: str
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    scopes: tuple[str, ...] = ()
    owner_user_keys: tuple[str, ...] = ()
    tenant_key: str | None = None
    chat_id: str | None = None
    created_message_id: str | None = None
    created_event_id: str | None = None
    created_at: int | None = None
    state: str = "awaiting_authorization"
    extra: dict[str, Any] = field(default_factory=dict)


class ClaimResult(str, Enum):
    r"""
    一次审批认领（claim）的结果，是审批执行前的并发安全闸门。

    [feishu.agent.approval.ApprovalEngine][] 在执行工具前先 `claim` 对应审批，依据返回值决定放行或拒绝：
    仅 `CLAIMED` 允许继续执行，其余值各自对应一种不可执行的情形。由于继承自 `str`，枚举成员可直接与字符串
    字面量比较。

    Examples:
        >>> ClaimResult.CLAIMED == "claimed"
        True
        >>> ClaimResult("tampered") is ClaimResult.TAMPERED
        True
    """

    CLAIMED = "claimed"  # state flipped awaiting_confirmation -> executing; proceed
    ALREADY_CLAIMED = "already_claimed"  # another confirm already claimed/executed it
    SUPERSEDED = "superseded"  # a newer proposal for the same operation replaced it
    TAMPERED = "tampered"  # expected_payload_sha256 != stored payload_sha256
    EXPIRED = "expired"  # TTL elapsed before confirmation
    MISSING = "missing"  # no such approval (unknown id or already resolved)


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

    async def get(self, approval_id: str) -> PendingApproval | None:
        r"""按 `approval_id` 读取挂起的审批而不移除，不存在时返回 `None`。"""
        ...

    async def claim(self, approval_id: str, *, expected_payload_sha256: str | None = None) -> ClaimResult:
        r"""
        原子地认领一次审批（`awaiting_confirmation` -> `executing`），返回 [feishu.agent.session.ClaimResult][]。

        这是防重复执行与防篡改的并发闸门：提供 `expected_payload_sha256` 时须与存储的负载摘要一致，否则返回
        `TAMPERED`；已被认领/执行返回 `ALREADY_CLAIMED`；不存在返回 `MISSING`。仅 `CLAIMED` 允许继续执行。
        """
        ...

    async def complete(self, approval_id: str, *, outcome: str) -> None:
        r"""标记一次审批的最终处置：成功/拒绝/取消即移除，结果未知则冻结以防重复执行。"""
        ...

    async def update(self, approval_id: str, mutator: Callable[[PendingApproval], tuple[T, PendingApproval]]) -> T:
        r"""以 compare-and-swap 方式原子更新一次审批：`mutator(旧值)` 返回 `(返回值, 新值)`。"""
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

    async def get(self, approval_id: str) -> PendingApproval | None:
        r"""
        按 `approval_id` 读取挂起的审批而不移除。

        Args:
            approval_id: 审批标识。

        Returns:
            对应的 [feishu.agent.session.PendingApproval][]；不存在时返回 `None`。
        """
        return self._store.get(approval_id)

    async def claim(self, approval_id: str, *, expected_payload_sha256: str | None = None) -> ClaimResult:
        r"""
        原子地认领一次审批，返回 [feishu.agent.session.ClaimResult][]。

        在锁内完成「存在性 + 篡改 + 状态」三项校验，并在通过时将状态翻转为 `executing`，从而保证同一审批不会
        被并发确认重复执行。

        Args:
            approval_id: 审批标识。
            expected_payload_sha256: 卡片回传携带的负载摘要；提供时须与存储值一致，否则返回 `TAMPERED`。

        Returns:
            认领结果；仅 `CLAIMED` 表示可继续执行。

        Examples:
            >>> import asyncio
            >>> store = InMemoryPendingApprovalStore()
            >>> approval = PendingApproval(
            ...     approval_id="ap_1", session_id="oc_1", tool_call_id="c1",
            ...     tool_name="deploy", arguments={"env": "prod"}, payload_sha256="abc",
            ... )
            >>> async def demo():
            ...     await store.put(approval)
            ...     bad = await store.claim("ap_1", expected_payload_sha256="zzz")
            ...     ok = await store.claim("ap_1", expected_payload_sha256="abc")
            ...     again = await store.claim("ap_1", expected_payload_sha256="abc")
            ...     return bad.value, ok.value, again.value
            >>> asyncio.run(demo())
            ('tampered', 'claimed', 'already_claimed')
        """
        async with self._lock:
            approval = self._store.get(approval_id)
            if approval is None:
                return ClaimResult.MISSING
            # Fail closed on tampering: when EITHER side has a payload hash, both must match. A stored hash with a
            # missing/None callback hash is a mismatch (so a callback that omits payload_sha256 can't skip the
            # check); a callback hash with no stored hash is also a mismatch. Only when neither exists is there
            # nothing to verify.
            stored_sha = approval.payload_sha256 or ""
            if (stored_sha or expected_payload_sha256 is not None) and expected_payload_sha256 != stored_sha:
                return ClaimResult.TAMPERED
            if approval.state != "awaiting_confirmation":
                return ClaimResult.ALREADY_CLAIMED
            approval.state = "executing"
            return ClaimResult.CLAIMED

    async def complete(self, approval_id: str, *, outcome: str) -> None:
        r"""
        标记一次审批的最终处置。

        成功、拒绝或取消（`executed`/`replayed`/`rejected`/`cancelled`）即移除记录；`retry` 还原为
        `awaiting_confirmation` 以便重试；执行结果未知（`unknown`/`frozen`）则冻结为 `execution_unknown`。

        Args:
            approval_id: 审批标识。
            outcome: 最终处置标签。
        """
        async with self._lock:
            if outcome == "retry":
                approval = self._store.get(approval_id)
                if approval is not None:
                    approval.state = "awaiting_confirmation"
                return
            if outcome in ("unknown", "frozen"):
                approval = self._store.get(approval_id)
                if approval is not None:
                    approval.state = "execution_unknown"
                return
            self._store.pop(approval_id, None)

    async def update(self, approval_id: str, mutator: Callable[[PendingApproval], tuple[T, PendingApproval]]) -> T:
        r"""
        以 compare-and-swap 方式原子更新一次审批。

        在锁内调用 `mutator(旧值)`，其须返回 `(返回值, 新值)`；新值写回存储，返回值回传调用方。

        Args:
            approval_id: 审批标识。
            mutator: 接受旧审批、返回 `(返回值, 新审批)` 的纯函数。

        Returns:
            `mutator` 的返回值。

        Raises:
            KeyError: 审批不存在时抛出。
        """
        async with self._lock:
            approval = self._store.get(approval_id)
            if approval is None:
                raise KeyError(approval_id)
            value, updated = mutator(approval)
            self._store[approval_id] = updated
            return value


@runtime_checkable
class PendingAuthorizationStore(Protocol):
    r"""
    挂起授权存储协议，是 OAuth 授权后自动恢复工具调用的扩展契约。

    [feishu.agent.loop.Agent][] 通过该协议保存与取回挂起的 [feishu.agent.session.PendingAuthorization][]；
    内置实现为 [feishu.agent.session.InMemoryPendingAuthorizationStore][]。
    """

    async def put(self, authorization: PendingAuthorization) -> None:
        r"""保存一次挂起授权。"""
        ...

    async def get(self, authorization_id: str) -> PendingAuthorization | None:
        r"""按 `authorization_id` 读取挂起授权而不移除，不存在时返回 `None`。"""
        ...

    async def pop(self, authorization_id: str) -> PendingAuthorization | None:
        r"""按 `authorization_id` 取出并移除一次挂起授权，不存在时返回 `None`。"""
        ...

    async def claim(self, authorization_id: str) -> ClaimResult:
        r"""原子地认领一次授权（`awaiting_authorization` -> `executing`），仅 `CLAIMED` 允许恢复工具。"""
        ...

    async def complete(self, authorization_id: str, *, outcome: str) -> None:
        r"""
        标记一次授权恢复的最终处置。

        `retry` 还原为 `awaiting_authorization`；`unknown`/`frozen` 冻结为 `execution_unknown`；其余终态
        （如 `executed`/`failed`/`cancelled`/`expired`）移除记录。
        """
        ...


class InMemoryPendingAuthorizationStore:
    r"""基于内存的 [feishu.agent.session.PendingAuthorizationStore][] 实现。"""

    def __init__(self) -> None:
        self._store: dict[str, PendingAuthorization] = {}
        self._lock = asyncio.Lock()

    async def put(self, authorization: PendingAuthorization) -> None:
        r"""保存一次挂起授权。"""
        async with self._lock:
            self._store[authorization.authorization_id] = authorization

    async def get(self, authorization_id: str) -> PendingAuthorization | None:
        r"""按 `authorization_id` 读取挂起授权而不移除。"""
        async with self._lock:
            return self._store.get(authorization_id)

    async def pop(self, authorization_id: str) -> PendingAuthorization | None:
        r"""按 `authorization_id` 取出并移除一次挂起授权。"""
        async with self._lock:
            return self._store.pop(authorization_id, None)

    async def claim(self, authorization_id: str) -> ClaimResult:
        r"""原子地认领一次授权，返回 [feishu.agent.session.ClaimResult][]。"""
        async with self._lock:
            authorization = self._store.get(authorization_id)
            if authorization is None:
                return ClaimResult.MISSING
            if authorization.state != "awaiting_authorization":
                return ClaimResult.ALREADY_CLAIMED
            authorization.state = "executing"
            return ClaimResult.CLAIMED

    async def complete(self, authorization_id: str, *, outcome: str) -> None:
        r"""标记授权恢复的最终处置；`retry` 重开，`unknown`/`frozen` 冻结，其余终态移除。"""
        async with self._lock:
            if outcome in ("unknown", "frozen"):
                authorization = self._store.get(authorization_id)
                if authorization is not None:
                    authorization.state = "execution_unknown"
                return
            if outcome == "retry":
                authorization = self._store.get(authorization_id)
                if authorization is not None:
                    authorization.state = "awaiting_authorization"
                return
            self._store.pop(authorization_id, None)


__all__ = [
    "ClaimResult",
    "InMemoryPendingApprovalStore",
    "InMemoryPendingAuthorizationStore",
    "InMemorySessionStore",
    "PendingApproval",
    "PendingApprovalStore",
    "PendingAuthorization",
    "PendingAuthorizationStore",
    "SessionStore",
]
