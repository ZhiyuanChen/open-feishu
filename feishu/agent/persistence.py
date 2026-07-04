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
基于 SQLite / JSONL 的持久化默认实现：会话历史、挂起审批、幂等执行缓存与审计日志均可跨进程重启存活。

这些类分别实现 [feishu.agent.session.SessionStore][]、[feishu.agent.session.PendingApprovalStore][]、
[feishu.agent.approval.ExecutionResultStore][] 与 [feishu.agent.approval.AuditLog][] 协议，是内置 `InMemory*`
实现的持久化对应物：把它们传给 [feishu.agent.loop.AgentEngine][] 即可让会话与「人在环」审批在重启后继续。

并发与异步：结构化存储以 SQLite（WAL 模式）落盘，各自持有独立连接；异步存储用 `asyncio.Lock` 串行化连接
访问，同步存储用 `threading.Lock`。SQLite 本地操作通常为亚毫秒级，对机器人场景可接受；超大规模部署可按相同
协议替换为真正的异步数据库后端。审批认领以「读取—校验—`UPDATE ... WHERE state='awaiting_confirmation'`
（依据 `rowcount`）」实现 compare-and-swap，从而在并发确认下保证至多一次执行。
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

from .integrity import payload_summary
from .llm import Message, TextPart, ToolResultPart, ToolUsePart
from .session import ClaimResult, PendingApproval, PendingAuthorization

T = TypeVar("T")

# How long an awaiting confirmation stays valid, and how long a frozen
# (execution_unknown) record is retained, by default.
_DEFAULT_TTL_SECONDS = 15 * 60
_DEFAULT_AUTHORIZATION_TTL_SECONDS = 60 * 60
_DEFAULT_EXECUTION_UNKNOWN_TTL_SECONDS = 7 * 24 * 3600


def _now() -> int:
    return int(time.time())


def _connect(db_path: str | Path) -> sqlite3.Connection:
    # Shared hardened connector: WAL + 0o700 data dir + 0o600 files (protects token-at-rest sidecars).
    from .._sqlite import connect

    return connect(db_path)


# --------------------------------------------------------------------------- #
# Serialization: provider-neutral Message / PendingApproval <-> JSON-able dict
# --------------------------------------------------------------------------- #


def _part_to_dict(part: Any) -> dict[str, Any]:
    if isinstance(part, TextPart):
        return {"k": "text", "text": part.text}
    if isinstance(part, ToolUsePart):
        return {"k": "tool_use", "id": part.id, "name": part.name, "arguments": part.arguments}
    if isinstance(part, ToolResultPart):
        return {
            "k": "tool_result",
            "tool_call_id": part.tool_call_id,
            "content": part.content,
            "is_error": part.is_error,
        }
    raise TypeError(f"cannot serialize content part of type {type(part).__name__}")


def _part_from_dict(data: dict[str, Any]) -> Any:
    kind = data.get("k")
    if kind == "text":
        return TextPart(text=data.get("text", ""))
    if kind == "tool_use":
        return ToolUsePart(id=data["id"], name=data["name"], arguments=data.get("arguments") or {})
    if kind == "tool_result":
        return ToolResultPart(
            tool_call_id=data["tool_call_id"], content=data.get("content", ""), is_error=bool(data.get("is_error"))
        )
    raise ValueError(f"unknown content part kind: {kind!r}")


def message_to_dict(message: Message) -> dict[str, Any]:
    r"""将 [feishu.agent.llm.Message][] 序列化为可 JSON 化的字典。"""
    return {"role": message.role, "content": [_part_to_dict(part) for part in message.content]}


def message_from_dict(data: dict[str, Any]) -> Message:
    r"""从 [feishu.agent.persistence.message_to_dict][] 的产物还原 [feishu.agent.llm.Message][]。"""
    return Message(role=data["role"], content=[_part_from_dict(part) for part in data.get("content", [])])


def approval_to_dict(approval: PendingApproval) -> dict[str, Any]:
    r"""将 [feishu.agent.session.PendingApproval][] 序列化为可 JSON 化的字典。"""
    return {
        "approval_id": approval.approval_id,
        "session_id": approval.session_id,
        "tool_call_id": approval.tool_call_id,
        "tool_name": approval.tool_name,
        "arguments": approval.arguments,
        "payload_sha256": approval.payload_sha256,
        "idempotency_key": approval.idempotency_key,
        "owner_user_keys": list(approval.owner_user_keys),
        "tenant_key": approval.tenant_key,
        "chat_id": approval.chat_id,
        "created_message_id": approval.created_message_id,
        "created_event_id": approval.created_event_id,
        "created_at": approval.created_at,
        "state": approval.state,
        "extra": approval.extra,
    }


def approval_from_dict(data: dict[str, Any]) -> PendingApproval:
    r"""从 [feishu.agent.persistence.approval_to_dict][] 的产物还原 [feishu.agent.session.PendingApproval][]。"""
    return PendingApproval(
        approval_id=data["approval_id"],
        session_id=data["session_id"],
        tool_call_id=data["tool_call_id"],
        tool_name=data["tool_name"],
        arguments=data.get("arguments") or {},
        payload_sha256=data.get("payload_sha256"),
        idempotency_key=data.get("idempotency_key"),
        owner_user_keys=tuple(data.get("owner_user_keys") or ()),
        tenant_key=data.get("tenant_key"),
        chat_id=data.get("chat_id"),
        created_message_id=data.get("created_message_id"),
        created_event_id=data.get("created_event_id"),
        created_at=data.get("created_at"),
        state=data.get("state", "awaiting_confirmation"),
        extra=data.get("extra") or {},
    )


def authorization_to_dict(authorization: PendingAuthorization) -> dict[str, Any]:
    r"""将 [feishu.agent.session.PendingAuthorization][] 序列化为可 JSON 化的字典。"""
    return {
        "authorization_id": authorization.authorization_id,
        "session_id": authorization.session_id,
        "tool_call_id": authorization.tool_call_id,
        "tool_name": authorization.tool_name,
        "arguments": authorization.arguments,
        "scopes": list(authorization.scopes),
        "owner_user_keys": list(authorization.owner_user_keys),
        "tenant_key": authorization.tenant_key,
        "chat_id": authorization.chat_id,
        "created_message_id": authorization.created_message_id,
        "created_event_id": authorization.created_event_id,
        "created_at": authorization.created_at,
        "state": authorization.state,
        "extra": authorization.extra,
    }


def authorization_from_dict(data: dict[str, Any]) -> PendingAuthorization:
    r"""从 [feishu.agent.persistence.authorization_to_dict][] 的产物还原挂起授权。"""
    return PendingAuthorization(
        authorization_id=data["authorization_id"],
        session_id=data["session_id"],
        tool_call_id=data["tool_call_id"],
        tool_name=data["tool_name"],
        arguments=data.get("arguments") or {},
        scopes=tuple(data.get("scopes") or ()),
        owner_user_keys=tuple(data.get("owner_user_keys") or ()),
        tenant_key=data.get("tenant_key"),
        chat_id=data.get("chat_id"),
        created_message_id=data.get("created_message_id"),
        created_event_id=data.get("created_event_id"),
        created_at=data.get("created_at"),
        state=data.get("state", "awaiting_authorization"),
        extra=data.get("extra") or {},
    )


# --------------------------------------------------------------------------- #
# Durable stores
# --------------------------------------------------------------------------- #


class SqliteSessionStore:
    r"""
    基于 SQLite 的 [feishu.agent.session.SessionStore][] 实现，会话历史跨重启存活。

    每个会话以一行 JSON 存储其消息列表；`append` 为「读取—追加—写回」事务，并按 `max_messages` 截断最旧消息。

    Args:
        db_path: SQLite 数据库文件路径，自动创建父目录并以 0o600 收紧权限。
        max_messages: 每个会话保留的最大消息数，`0` 表示不截断。默认为 `400`。

    Examples:
        >>> store = SqliteSessionStore(":memory:")  # doctest:+SKIP
    """

    def __init__(self, db_path: str | Path, *, max_messages: int = 400) -> None:
        self._db = _connect(db_path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS sessions ("
            "session_id TEXT PRIMARY KEY, messages TEXT NOT NULL, updated_at REAL)"
        )
        self._db.commit()
        self._max_messages = max_messages
        self._lock = asyncio.Lock()

    async def get(self, session_id: str) -> list[Message]:
        r"""读取指定会话的全部历史消息；会话不存在时返回空列表。"""
        async with self._lock:
            row = self._db.execute("SELECT messages FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if not row:
            return []
        return [message_from_dict(item) for item in json.loads(row[0])]

    async def append(self, session_id: str, *messages: Message) -> None:
        r"""向指定会话追加消息，并按 `max_messages` 截断最旧消息。"""
        if not messages:
            return
        async with self._lock:
            row = self._db.execute("SELECT messages FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            data = json.loads(row[0]) if row else []
            data.extend(message_to_dict(message) for message in messages)
            self._write(session_id, data)

    async def set(self, session_id: str, messages: list[Message]) -> None:
        r"""以给定的消息列表整体替换指定会话的历史。"""
        data = [message_to_dict(message) for message in messages]
        async with self._lock:
            self._write(session_id, data)

    async def clear(self, session_id: str) -> None:
        r"""清空指定会话的历史（删除该行，彻底丢弃，而非隐藏）。"""
        async with self._lock:
            self._db.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            self._db.commit()

    async def updated_at(self, session_id: str) -> float | None:
        r"""返回指定会话最近写入时间戳；未知会话返回 `None`。"""
        async with self._lock:
            row = self._db.execute("SELECT updated_at FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if not row or row[0] is None:
            return None
        return float(row[0])

    def _write(self, session_id: str, data: list[dict[str, Any]]) -> None:
        if self._max_messages and len(data) > self._max_messages:
            data = data[-self._max_messages :]
        self._db.execute(
            "INSERT INTO sessions (session_id, messages, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET messages = excluded.messages, updated_at = excluded.updated_at",
            (session_id, json.dumps(data, ensure_ascii=False), time.time()),
        )
        self._db.commit()


class SqlitePendingApprovalStore:
    r"""
    基于 SQLite 的 [feishu.agent.session.PendingApprovalStore][] 实现，挂起审批跨重启存活。

    实现完整的 CAS 生命周期（`get`/`claim`/`complete`/`update`，并保留 `put`/`pop`）：`claim` 在事务内完成
    存在性、TTL、防篡改与状态校验，并以 `UPDATE ... WHERE state='awaiting_confirmation'` 翻转状态，依据
    `rowcount` 判定是否抢占成功，从而保证并发确认下至多一次执行。过期记录在访问时惰性清理。

    Args:
        db_path: SQLite 数据库文件路径。
        ttl_seconds: 等待确认的存活时长。默认为 `900`（15 分钟）。
        execution_unknown_ttl_seconds: 冻结记录（`execution_unknown`）的保留时长。默认为 `604800`（7 天）。

    Examples:
        >>> store = SqlitePendingApprovalStore(":memory:")  # doctest:+SKIP
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        execution_unknown_ttl_seconds: int = _DEFAULT_EXECUTION_UNKNOWN_TTL_SECONDS,
    ) -> None:
        self._db = _connect(db_path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS approvals ("
            "approval_id TEXT PRIMARY KEY, state TEXT NOT NULL, payload_sha256 TEXT, "
            "created_at INTEGER, data TEXT NOT NULL)"
        )
        self._db.commit()
        self._ttl = ttl_seconds
        self._frozen_ttl = execution_unknown_ttl_seconds
        self._lock = asyncio.Lock()

    async def put(self, approval: PendingApproval) -> None:
        r"""保存一次挂起的审批；未设置 `created_at` 时以当前时间戳记。"""
        created_at = approval.created_at if approval.created_at is not None else _now()
        async with self._lock:
            self._db.execute(
                "INSERT INTO approvals (approval_id, state, payload_sha256, created_at, data) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(approval_id) DO UPDATE SET state=excluded.state, payload_sha256=excluded.payload_sha256, "
                "created_at=excluded.created_at, data=excluded.data",
                (
                    approval.approval_id,
                    approval.state,
                    approval.payload_sha256,
                    created_at,
                    json.dumps(approval_to_dict(approval), ensure_ascii=False),
                ),
            )
            self._db.commit()

    async def get(self, approval_id: str) -> PendingApproval | None:
        r"""读取挂起审批而不移除；过期则惰性删除并返回 `None`。"""
        async with self._lock:
            return self._load_locked(approval_id)

    async def pop(self, approval_id: str) -> PendingApproval | None:
        r"""取出并移除一次挂起审批，不存在时返回 `None`。"""
        async with self._lock:
            approval = self._load_locked(approval_id)
            if approval is not None:
                self._db.execute("DELETE FROM approvals WHERE approval_id = ?", (approval_id,))
                self._db.commit()
            return approval

    async def claim(self, approval_id: str, *, expected_payload_sha256: str | None = None) -> ClaimResult:
        r"""原子认领一次审批，返回 [feishu.agent.session.ClaimResult][]；仅 `CLAIMED` 可继续执行。"""
        async with self._lock:
            row = self._db.execute(
                "SELECT state, payload_sha256, created_at FROM approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                return ClaimResult.MISSING
            state, stored_sha, created_at = row
            if self._is_expired(state, created_at):
                self._db.execute("DELETE FROM approvals WHERE approval_id = ?", (approval_id,))
                self._db.commit()
                return ClaimResult.EXPIRED
            # Fail closed on tampering: when EITHER side has a payload hash, both must match. A stored hash with a
            # missing/None callback hash is a mismatch (so a callback that omits payload_sha256 can't skip the
            # check); a callback hash with no stored hash is also a mismatch. Only when neither exists is there
            # nothing to verify.
            stored_sha = stored_sha or ""
            if (stored_sha or expected_payload_sha256 is not None) and expected_payload_sha256 != stored_sha:
                return ClaimResult.TAMPERED
            if state != "awaiting_confirmation":
                return ClaimResult.ALREADY_CLAIMED
            cursor = self._db.execute(
                "UPDATE approvals SET state = 'executing' WHERE approval_id = ? AND state = 'awaiting_confirmation'",
                (approval_id,),
            )
            self._db.commit()
            return ClaimResult.CLAIMED if cursor.rowcount == 1 else ClaimResult.ALREADY_CLAIMED

    async def complete(self, approval_id: str, *, outcome: str) -> None:
        r"""标记最终处置：成功/拒绝/取消即移除，结果未知则冻结为 `execution_unknown`。"""
        async with self._lock:
            if outcome == "retry":
                self._db.execute(
                    "UPDATE approvals SET state = 'awaiting_confirmation' WHERE approval_id = ?", (approval_id,)
                )
            elif outcome in ("unknown", "frozen"):
                self._db.execute(
                    "UPDATE approvals SET state = 'execution_unknown' WHERE approval_id = ?", (approval_id,)
                )
            else:
                self._db.execute("DELETE FROM approvals WHERE approval_id = ?", (approval_id,))
            self._db.commit()

    async def update(self, approval_id: str, mutator: Callable[[PendingApproval], tuple[T, PendingApproval]]) -> T:
        r"""以 compare-and-swap 方式原子更新一次审批：`mutator(旧值)` 返回 `(返回值, 新值)`。"""
        async with self._lock:
            approval = self._load_locked(approval_id)
            if approval is None:
                raise KeyError(approval_id)
            value, updated = mutator(approval)
            created_at = updated.created_at if updated.created_at is not None else _now()
            self._db.execute(
                "UPDATE approvals SET state = ?, payload_sha256 = ?, created_at = ?, data = ? WHERE approval_id = ?",
                (
                    updated.state,
                    updated.payload_sha256,
                    created_at,
                    json.dumps(approval_to_dict(updated), ensure_ascii=False),
                    approval_id,
                ),
            )
            self._db.commit()
            return value

    async def purge_expired(self) -> int:
        r"""删除所有已过期的审批记录，返回删除数量。"""
        now = _now()
        async with self._lock:
            # `executing` is retained like `execution_unknown` (long TTL): a stale executing means a process died
            # mid-side-effect, and its unknown outcome must not be purged at the short pending TTL.
            cursor = self._db.execute(
                "DELETE FROM approvals WHERE (state IN ('execution_unknown', 'executing') AND created_at < ?) "
                "OR (state NOT IN ('execution_unknown', 'executing') AND created_at < ?)",
                (now - self._frozen_ttl, now - self._ttl),
            )
            self._db.commit()
            return cursor.rowcount

    def _load_locked(self, approval_id: str) -> PendingApproval | None:
        row = self._db.execute(
            "SELECT state, created_at, data FROM approvals WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        if row is None:
            return None
        state, created_at, data = row
        if self._is_expired(state, created_at):
            self._db.execute("DELETE FROM approvals WHERE approval_id = ?", (approval_id,))
            self._db.commit()
            return None
        approval = approval_from_dict(json.loads(data))
        approval.state = state
        approval.created_at = created_at
        return approval

    def _is_expired(self, state: str, created_at: int | None) -> bool:
        if created_at is None:
            return False
        # `executing` (a process that died mid-side-effect) is retained for the long frozen TTL like
        # `execution_unknown`, so its unknown outcome is not silently dropped at the short pending TTL.
        ttl = self._frozen_ttl if state in ("execution_unknown", "executing") else self._ttl
        return _now() - created_at > ttl


class SqlitePendingAuthorizationStore:
    r"""
    基于 SQLite 的 [feishu.agent.session.PendingAuthorizationStore][] 实现，挂起授权跨重启存活。

    OAuth callback 可能晚于原消息数分钟到达，甚至跨进程重启；该 store 以 `authorization_id` 保存恢复所需的
    tool call 上下文，并用 `claim` 保证重复 callback 至多恢复一次。

    Args:
        db_path: SQLite 数据库文件路径。
        ttl_seconds: 等待授权的存活时长。默认为 `3600`（1 小时）。
        execution_unknown_ttl_seconds: 冻结记录（`execution_unknown`）的保留时长。默认为 `604800`（7 天）。
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        ttl_seconds: int = _DEFAULT_AUTHORIZATION_TTL_SECONDS,
        execution_unknown_ttl_seconds: int = _DEFAULT_EXECUTION_UNKNOWN_TTL_SECONDS,
    ) -> None:
        self._db = _connect(db_path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS authorizations ("
            "authorization_id TEXT PRIMARY KEY, state TEXT NOT NULL, created_at INTEGER, data TEXT NOT NULL)"
        )
        self._db.commit()
        self._ttl = ttl_seconds
        self._frozen_ttl = execution_unknown_ttl_seconds
        self._lock = asyncio.Lock()

    async def put(self, authorization: PendingAuthorization) -> None:
        r"""保存一次挂起授权；未设置 `created_at` 时以当前时间戳记。"""
        created_at = authorization.created_at if authorization.created_at is not None else _now()
        async with self._lock:
            self._db.execute(
                "INSERT INTO authorizations (authorization_id, state, created_at, data) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(authorization_id) DO UPDATE SET state=excluded.state, "
                "created_at=excluded.created_at, data=excluded.data",
                (
                    authorization.authorization_id,
                    authorization.state,
                    created_at,
                    json.dumps(authorization_to_dict(authorization), ensure_ascii=False),
                ),
            )
            self._db.commit()

    async def get(self, authorization_id: str) -> PendingAuthorization | None:
        r"""读取挂起授权而不移除；过期状态由 `claim` 判定，以便 callback 仍能回原会话提示用户。"""
        async with self._lock:
            return self._load_locked(authorization_id)

    async def pop(self, authorization_id: str) -> PendingAuthorization | None:
        r"""取出并移除一次挂起授权，不存在时返回 `None`。"""
        async with self._lock:
            authorization = self._load_locked(authorization_id)
            if authorization is not None:
                self._db.execute("DELETE FROM authorizations WHERE authorization_id = ?", (authorization_id,))
                self._db.commit()
            return authorization

    async def claim(self, authorization_id: str) -> ClaimResult:
        r"""原子认领一次授权，返回 [feishu.agent.session.ClaimResult][]；仅 `CLAIMED` 可恢复工具。"""
        async with self._lock:
            row = self._db.execute(
                "SELECT state, created_at FROM authorizations WHERE authorization_id = ?", (authorization_id,)
            ).fetchone()
            if row is None:
                return ClaimResult.MISSING
            state, created_at = row
            if self._is_expired(state, created_at):
                self._db.execute("DELETE FROM authorizations WHERE authorization_id = ?", (authorization_id,))
                self._db.commit()
                return ClaimResult.EXPIRED
            if state != "awaiting_authorization":
                return ClaimResult.ALREADY_CLAIMED
            cursor = self._db.execute(
                "UPDATE authorizations SET state = 'executing' "
                "WHERE authorization_id = ? AND state = 'awaiting_authorization'",
                (authorization_id,),
            )
            self._db.commit()
            return ClaimResult.CLAIMED if cursor.rowcount == 1 else ClaimResult.ALREADY_CLAIMED

    async def complete(self, authorization_id: str, *, outcome: str) -> None:
        r"""标记最终处置：`retry` 重开，`unknown`/`frozen` 冻结，其余终态移除。"""
        async with self._lock:
            if outcome == "retry":
                self._db.execute(
                    "UPDATE authorizations SET state = 'awaiting_authorization' WHERE authorization_id = ?",
                    (authorization_id,),
                )
            elif outcome in ("unknown", "frozen"):
                self._db.execute(
                    "UPDATE authorizations SET state = 'execution_unknown' WHERE authorization_id = ?",
                    (authorization_id,),
                )
            else:
                self._db.execute("DELETE FROM authorizations WHERE authorization_id = ?", (authorization_id,))
            self._db.commit()

    async def update(
        self,
        authorization_id: str,
        mutator: Callable[[PendingAuthorization], tuple[T, PendingAuthorization]],
    ) -> T:
        r"""以 compare-and-swap 方式原子更新一次授权：`mutator(旧值)` 返回 `(返回值, 新值)`。"""
        async with self._lock:
            authorization = self._load_locked(authorization_id)
            if authorization is None:
                raise KeyError(authorization_id)
            value, updated = mutator(authorization)
            created_at = updated.created_at if updated.created_at is not None else _now()
            self._db.execute(
                "UPDATE authorizations SET state = ?, created_at = ?, data = ? WHERE authorization_id = ?",
                (
                    updated.state,
                    created_at,
                    json.dumps(authorization_to_dict(updated), ensure_ascii=False),
                    authorization_id,
                ),
            )
            self._db.commit()
            return value

    async def purge_expired(self) -> int:
        r"""删除所有已过期的授权记录，返回删除数量。"""
        now = _now()
        async with self._lock:
            cursor = self._db.execute(
                "DELETE FROM authorizations WHERE (state IN ('execution_unknown', 'executing') AND created_at < ?) "
                "OR (state NOT IN ('execution_unknown', 'executing') AND created_at < ?)",
                (now - self._frozen_ttl, now - self._ttl),
            )
            self._db.commit()
            return cursor.rowcount

    def _load_locked(self, authorization_id: str) -> PendingAuthorization | None:
        row = self._db.execute(
            "SELECT state, created_at, data FROM authorizations WHERE authorization_id = ?", (authorization_id,)
        ).fetchone()
        if row is None:
            return None
        state, created_at, data = row
        authorization = authorization_from_dict(json.loads(data))
        authorization.state = state
        authorization.created_at = created_at
        return authorization

    def _is_expired(self, state: str, created_at: int | None) -> bool:
        if created_at is None:
            return False
        ttl = self._frozen_ttl if state in ("execution_unknown", "executing") else self._ttl
        return _now() - created_at > ttl


class SqliteExecutionResultStore:
    r"""
    基于 SQLite 的 [feishu.agent.approval.ExecutionResultStore][] 实现：按幂等键缓存执行结果以支持重放。

    `put` 同时为幂等键与各别名键写入指向同一结果的行；`get` 命中任一键即返回，从而让「已执行的写操作被再次
    确认」时返回先前结果而非二次提交。方法为同步，以 `threading.Lock` 串行化独立连接。

    Examples:
        >>> store = SqliteExecutionResultStore(":memory:")  # doctest:+SKIP
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db = _connect(db_path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS executions ("
            "lookup_key TEXT PRIMARY KEY, payload_sha256 TEXT, execution_status TEXT, result TEXT, created_at INTEGER)"
        )
        self._db.commit()
        self._lock = threading.Lock()

    def get(self, lookup_key: str) -> dict[str, Any] | None:
        r"""按幂等键 / 别名键读取已缓存的执行结果记录，未命中返回 `None`。"""
        with self._lock:
            row = self._db.execute(
                "SELECT payload_sha256, execution_status, result FROM executions WHERE lookup_key = ?", (lookup_key,)
            ).fetchone()
        if row is None:
            return None
        payload_sha, status, result = row
        return {
            "payload_sha256": payload_sha,
            "execution_status": status,
            "result": json.loads(result) if result is not None else None,
        }

    def put(
        self,
        idempotency_key: str,
        *,
        execution_status: str,
        result: Any,
        alias_lookup_keys: tuple[str, ...] = (),
        payload_sha256: str | None = None,
    ) -> None:
        r"""写入一次执行结果，并为各别名键写入指向同一结果的行。"""
        encoded = json.dumps(result, ensure_ascii=False, default=str)
        created_at = _now()
        rows = [
            (key, payload_sha256, execution_status, encoded, created_at)
            for key in (idempotency_key, *alias_lookup_keys)
        ]
        with self._lock:
            self._db.executemany(
                "INSERT INTO executions (lookup_key, payload_sha256, execution_status, result, created_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(lookup_key) DO UPDATE SET "
                "payload_sha256=excluded.payload_sha256, execution_status=excluded.execution_status, "
                "result=excluded.result, created_at=excluded.created_at",
                rows,
            )
            self._db.commit()


class JsonlAuditLog:
    r"""
    基于 JSONL 的 [feishu.agent.approval.AuditLog][] 实现：仅追加地记录审批生命周期事件。

    每行一条事件，仅记录负载的结构化摘要（[feishu.agent.integrity.payload_summary][]）而非原始内容，避免敏感
    数据落盘。文件以 0o600 创建，写入以 `threading.Lock` 串行化。

    Args:
        path: 审计日志文件路径（JSONL）。

    Examples:
        >>> log = JsonlAuditLog("/tmp/audit.jsonl")  # doctest:+SKIP
    """

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        parent = os.path.dirname(os.path.abspath(self._path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()

    def append(
        self,
        event_type: str,
        *,
        key: str,
        approval: PendingApproval | None = None,
        event_id: str | None = None,
        message_id: str | None = None,
        outcome: str = "ok",
        error: str | None = None,
    ) -> None:
        r"""追加一条审计事件。"""
        record: dict[str, Any] = {"ts": _now(), "event": event_type, "key": key, "outcome": outcome}
        if approval is not None:
            record["tool_name"] = approval.tool_name
            record["state"] = approval.state
            record["payload"] = payload_summary(approval.arguments, include_hash=True)
        if event_id:
            record["event_id"] = event_id
        if message_id:
            record["message_id"] = message_id
        if error:
            record["error"] = error
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            # Create with 0o600 ATOMICALLY (no world-readable window between create and chmod): the audit log
            # holds tool names, states and payload summaries. 0o600 has no group/other bits, so umask can't widen
            # it; an existing file keeps its perms (as before). O_APPEND keeps writes append-only.
            fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
