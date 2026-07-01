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
用户分享文件的「句柄登记」：把用户发来的文件接入 Agent 的工具世界，且字节绝不进模型上下文。

用户在私聊里发来图片 / 文件后，本模块只登记一个**不透明、按用户隔离、带 TTL 的句柄**（重取配方：
`message_id` + `file_key` + 中性元数据），**不下载字节**。模型只会看到 `file_id` 及 `{name, media_type, size,
kind}` 等中性元数据；真正的字节由（后续阶段的）`SharedFileResolver` 在某个消费工具运行时，经
[feishu.im.messages.IMNamespace.get_resource][] 按需重取——零字节落盘（除审批绑定时的临时 pin 缓存外）。

设计对标 [feishu.auth.user_tokens][]：[feishu.agent.shared_files.SharedFileStore][] 抽象「按用户多别名
（open_id/union_id/user_id）存取句柄」，内置 [feishu.agent.shared_files.InMemorySharedFileStore][] 与
[feishu.agent.shared_files.SqliteSharedFileStore][]（后者经 `feishu._sqlite.connect` 加固：WAL + 0o700 目录
+ 0o600 文件）。`file_id` 为随机不可枚举令牌（非内容寻址、非由 `file_key` 派生），跨用户访问在解析期按请求
用户别名集合拦截。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable
from uuid import uuid4

from ..auth import user_identity_keys


def _now() -> int:
    return int(time.time())


def shared_file_keys(user: Mapping[str, Any]) -> tuple[str, ...]:
    r"""用户的稳定多别名键；[feishu.auth.user_tokens.user_identity_keys][] 的别名（全库统一表示）。"""
    return user_identity_keys(user)


def _new_file_id() -> str:
    # Random, unguessable, NOT derived from file_key and NOT content-addressed: a jailbroken agent can
    # neither forge a key nor probe whether another user uploaded identical bytes.
    return "sf_" + uuid4().hex


def _str_or_none(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return None


@dataclass
class SharedFile:
    r"""
    一个用户分享文件的句柄：足以（按请求用户）重取字节并向模型描述，但**不含任何字节**。

    `resource_type` 供 [feishu.im.messages.IMNamespace.get_resource][] 重取（`image` / `file`），`kind`
    为面向模型的类别。[feishu.agent.shared_files.SharedFile.summary][] 是唯一应进入模型上下文的视图——
    只含中性元数据，绝不含 `file_key` / `message_id` / 字节。

    Examples:
        >>> sf = SharedFile(file_id="sf_x", user_keys=("ou_1",), message_id="om_1", file_key="file_1",
        ...                 resource_type="file", kind="file", name="a.pdf", created_at=1, expires_at=0)
        >>> sf.summary()["name"], "file_key" in sf.summary()
        ('a.pdf', False)
    """

    file_id: str
    user_keys: tuple[str, ...]
    message_id: str
    file_key: str
    resource_type: str  # 'image' | 'file' — re-fetch type for im.get_resource
    kind: str  # model-facing category ('image' | 'file')
    name: str | None = None
    media_type: str | None = None
    size: int | None = None
    source_chat_id: str | None = None
    created_at: int = 0
    expires_at: int = 0  # 0 == never expires
    pinned: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, now: int) -> bool:
        r"""句柄是否已过期（`expires_at == 0` 视为永不过期）。"""
        return self.expires_at != 0 and self.expires_at <= now

    def summary(self) -> dict[str, Any]:
        r"""面向模型的中性元数据视图：**绝不**包含 `file_key` / `message_id` / 字节。"""
        return {
            "file_id": self.file_id,
            "name": self.name,
            "media_type": self.media_type,
            "kind": self.kind,
            "size": self.size,
            "received_at": self.created_at,
            "expires_at": self.expires_at,
        }

    def to_dict(self) -> dict[str, Any]:
        r"""完整（含 `file_key` / `message_id`）序列化为可 JSON 化字典，用于持久化——非模型可见视图，勿与 `summary()` 混淆。"""
        return {
            "file_id": self.file_id,
            "user_keys": list(self.user_keys),
            "message_id": self.message_id,
            "file_key": self.file_key,
            "resource_type": self.resource_type,
            "kind": self.kind,
            "name": self.name,
            "media_type": self.media_type,
            "size": self.size,
            "source_chat_id": self.source_chat_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "pinned": self.pinned,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SharedFile:
        r"""从 [feishu.agent.shared_files.SharedFile.to_dict][] 的产物还原 [feishu.agent.shared_files.SharedFile][]。"""
        return cls(
            file_id=str(data.get("file_id") or ""),
            user_keys=tuple(data.get("user_keys") or ()),
            message_id=str(data.get("message_id") or ""),
            file_key=str(data.get("file_key") or ""),
            resource_type=str(data.get("resource_type") or "file"),
            kind=str(data.get("kind") or "file"),
            name=data.get("name"),
            media_type=data.get("media_type"),
            size=data.get("size"),
            source_chat_id=data.get("source_chat_id"),
            created_at=int(data.get("created_at") or 0),
            expires_at=int(data.get("expires_at") or 0),
            pinned=bool(data.get("pinned")),
            meta=dict(data.get("meta") or {}),
        )

    @classmethod
    def from_resource(
        cls,
        resource: Mapping[str, Any],
        *,
        user_keys: tuple[str, ...],
        message: Mapping[str, Any],
        ttl_seconds: int,
        now: int,
        file_id: str | None = None,
    ) -> SharedFile:
        r"""由 [feishu.im.inbound.message_resource][] 的资源字典 + 入站消息构造句柄（**不取字节**）。"""
        return cls(
            file_id=file_id or _new_file_id(),
            user_keys=tuple(user_keys),
            message_id=str(message.get("message_id") or ""),
            file_key=str(resource.get("key") or ""),
            resource_type=str(resource.get("resource_type") or "file"),
            kind=str(resource.get("kind") or resource.get("resource_type") or "file"),
            name=_str_or_none(resource.get("name")),
            media_type=_str_or_none(resource.get("mime_type")),
            size=_int_or_none(resource.get("size")),
            source_chat_id=_str_or_none(message.get("chat_id")),
            created_at=now,
            expires_at=(now + ttl_seconds) if ttl_seconds else 0,
        )


@runtime_checkable
class SharedFileStore(Protocol):
    r"""
    用户分享文件句柄的存储协议：按用户多别名登记 / 读取，并支持审批绑定时的临时字节 pin 缓存。

    内置实现为 [feishu.agent.shared_files.InMemorySharedFileStore][] 与
    [feishu.agent.shared_files.SqliteSharedFileStore][]。本协议标注了 `runtime_checkable`。
    所有读写均按请求用户的别名集合做隔离——传入他人或臆造的 `file_id` 一律解析失败。
    """

    async def register(
        self, user: Mapping[str, Any], resource: Mapping[str, Any], *, message: Mapping[str, Any], ttl_seconds: int
    ) -> SharedFile:
        r"""登记一条入站资源句柄（不取字节）；同一用户对同一 `(message_id, file_key)` 幂等，返回既有句柄。"""
        ...

    async def get(self, user: Mapping[str, Any], file_id: str) -> SharedFile | None:
        r"""按请求用户读取句柄；非本人所有、已过期或不存在时返回 `None`。"""
        ...

    async def recent(self, user: Mapping[str, Any], *, limit: int = 10) -> list[SharedFile]:
        r"""按请求用户返回最近未过期的句柄（按登记时间倒序，至多 `limit` 条）。"""
        ...

    async def pin(self, user: Mapping[str, Any], file_id: str, *, cache_bytes: bytes | None = None) -> bool:
        r"""标记句柄为 pinned，并可附带要缓存的字节（审批绑定期防止源资源过期）；非本人所有返回 `False`。"""
        ...

    async def read_cached(self, user: Mapping[str, Any], file_id: str) -> bytes | None:
        r"""读取此前 pin 缓存的字节；无缓存或非本人所有时返回 `None`。"""
        ...

    async def purge_expired(self) -> int:
        r"""清理所有已过期句柄，返回清理条数。"""
        ...


class InMemorySharedFileStore:
    r"""
    基于内存的 [feishu.agent.shared_files.SharedFileStore][] 实现，仅适用于单进程、可接受重启即丢失的场景。

    Examples:
        >>> isinstance(InMemorySharedFileStore(), SharedFileStore)
        True
    """

    def __init__(self) -> None:
        self._files: dict[str, SharedFile] = {}
        self._cache: dict[str, bytes] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _owned(sf: SharedFile, aliases: set[str]) -> bool:
        return bool(set(sf.user_keys) & aliases)

    async def register(
        self, user: Mapping[str, Any], resource: Mapping[str, Any], *, message: Mapping[str, Any], ttl_seconds: int
    ) -> SharedFile:
        keys = shared_file_keys(user)
        if not keys:
            raise ValueError("cannot register a shared file without a user identity (open_id/union_id/user_id)")
        now = _now()
        aliases = set(keys)
        file_key = str(resource.get("key") or "")
        message_id = str(message.get("message_id") or "")
        async with self._lock:
            self._purge(now)
            for sf in self._files.values():
                if (
                    self._owned(sf, aliases)
                    and sf.message_id == message_id
                    and sf.file_key == file_key
                    and not sf.is_expired(now)
                ):
                    return sf  # idempotent: same file re-sent
            sf = SharedFile.from_resource(resource, user_keys=keys, message=message, ttl_seconds=ttl_seconds, now=now)
            self._files[sf.file_id] = sf
            return sf

    async def get(self, user: Mapping[str, Any], file_id: str) -> SharedFile | None:
        aliases = set(shared_file_keys(user))
        now = _now()
        async with self._lock:
            self._purge(now)
            sf = self._files.get(file_id)
            if sf is not None and self._owned(sf, aliases) and not sf.is_expired(now):
                return sf
        return None

    async def recent(self, user: Mapping[str, Any], *, limit: int = 10) -> list[SharedFile]:
        aliases = set(shared_file_keys(user))
        now = _now()
        async with self._lock:
            self._purge(now)
            files = [sf for sf in self._files.values() if self._owned(sf, aliases) and not sf.is_expired(now)]
        files.sort(key=lambda s: s.created_at, reverse=True)
        return files[:limit]

    async def pin(self, user: Mapping[str, Any], file_id: str, *, cache_bytes: bytes | None = None) -> bool:
        aliases = set(shared_file_keys(user))
        now = _now()
        async with self._lock:
            sf = self._files.get(file_id)
            if sf is None or not self._owned(sf, aliases) or sf.is_expired(now):
                return False
            sf.pinned = True
            if cache_bytes is not None:
                self._cache[file_id] = cache_bytes
            return True

    async def read_cached(self, user: Mapping[str, Any], file_id: str) -> bytes | None:
        aliases = set(shared_file_keys(user))
        async with self._lock:
            sf = self._files.get(file_id)
            if sf is None or not self._owned(sf, aliases):
                return None
            return self._cache.get(file_id)

    async def purge_expired(self) -> int:
        async with self._lock:
            return self._purge(_now())

    def _purge(self, now: int) -> int:
        expired = [fid for fid, sf in self._files.items() if sf.is_expired(now)]
        for fid in expired:
            self._files.pop(fid, None)
            self._cache.pop(fid, None)
        return len(expired)


class SqliteSharedFileStore:
    r"""
    基于 SQLite 的 [feishu.agent.shared_files.SharedFileStore][] 实现，句柄跨重启存活。

    采用两张表：`shared_files`（每个 `file_id` 一行，含重取配方 / 可选 pin 缓存字节 / 过期时间）与
    `shared_file_owners`（每个 (别名, file_id) 一行，供 open_id/union_id/user_id 任一命中且不重复缓存字节）。
    经 `feishu._sqlite.connect` 加固（WAL + 0o700 目录 + 0o600 文件），保护可能缓存的文件字节。

    Args:
        db_path: SQLite 数据库文件路径。

    Examples:
        >>> store = SqliteSharedFileStore(":memory:")  # doctest:+SKIP
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db = _connect(db_path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS shared_files ("
            "file_id TEXT PRIMARY KEY, message_id TEXT, file_key TEXT, recipe TEXT NOT NULL, "
            "cached_blob BLOB, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, "
            "pinned INTEGER NOT NULL DEFAULT 0)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS shared_file_owners ("
            "user_key TEXT NOT NULL, file_id TEXT NOT NULL, PRIMARY KEY (user_key, file_id))"
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_sfo_user ON shared_file_owners (user_key)")
        self._db.commit()
        self._lock = asyncio.Lock()

    async def register(
        self, user: Mapping[str, Any], resource: Mapping[str, Any], *, message: Mapping[str, Any], ttl_seconds: int
    ) -> SharedFile:
        keys = shared_file_keys(user)
        if not keys:
            raise ValueError("cannot register a shared file without a user identity (open_id/union_id/user_id)")
        now = _now()
        message_id = str(message.get("message_id") or "")
        file_key = str(resource.get("key") or "")
        async with self._lock:
            self._purge(now)
            placeholders = ",".join("?" * len(keys))
            row = self._db.execute(
                f"SELECT f.recipe FROM shared_files f JOIN shared_file_owners o ON f.file_id = o.file_id "
                f"WHERE o.user_key IN ({placeholders}) AND f.message_id = ? AND f.file_key = ? "
                f"AND (f.expires_at = 0 OR f.expires_at > ?) LIMIT 1",
                (*keys, message_id, file_key, now),
            ).fetchone()
            if row is not None:
                return SharedFile.from_dict(json.loads(row[0]))  # idempotent: same file re-sent
            sf = SharedFile.from_resource(resource, user_keys=keys, message=message, ttl_seconds=ttl_seconds, now=now)
            self._db.execute(
                "INSERT INTO shared_files "
                "(file_id, message_id, file_key, recipe, cached_blob, created_at, expires_at, pinned) "
                "VALUES (?, ?, ?, ?, NULL, ?, ?, 0)",
                (
                    sf.file_id,
                    sf.message_id,
                    sf.file_key,
                    json.dumps(sf.to_dict(), ensure_ascii=False),
                    sf.created_at,
                    sf.expires_at,
                ),
            )
            self._db.executemany(
                "INSERT OR REPLACE INTO shared_file_owners (user_key, file_id) VALUES (?, ?)",
                [(key, sf.file_id) for key in keys],
            )
            self._db.commit()
            return sf

    async def get(self, user: Mapping[str, Any], file_id: str) -> SharedFile | None:
        keys = shared_file_keys(user)
        if not keys:
            return None
        now = _now()
        async with self._lock:
            placeholders = ",".join("?" * len(keys))
            row = self._db.execute(
                f"SELECT f.recipe FROM shared_files f JOIN shared_file_owners o ON f.file_id = o.file_id "
                f"WHERE f.file_id = ? AND o.user_key IN ({placeholders}) "
                f"AND (f.expires_at = 0 OR f.expires_at > ?) LIMIT 1",
                (file_id, *keys, now),
            ).fetchone()
        return SharedFile.from_dict(json.loads(row[0])) if row is not None else None

    async def recent(self, user: Mapping[str, Any], *, limit: int = 10) -> list[SharedFile]:
        keys = shared_file_keys(user)
        if not keys:
            return []
        now = _now()
        async with self._lock:
            self._purge(now)
            placeholders = ",".join("?" * len(keys))
            rows = self._db.execute(
                f"SELECT f.recipe FROM shared_files f JOIN shared_file_owners o ON f.file_id = o.file_id "
                f"WHERE o.user_key IN ({placeholders}) AND (f.expires_at = 0 OR f.expires_at > ?) "
                f"GROUP BY f.file_id ORDER BY f.created_at DESC LIMIT ?",
                (*keys, now, limit),
            ).fetchall()
        return [SharedFile.from_dict(json.loads(r[0])) for r in rows]

    async def pin(self, user: Mapping[str, Any], file_id: str, *, cache_bytes: bytes | None = None) -> bool:
        keys = shared_file_keys(user)
        if not keys:
            return False
        now = _now()
        async with self._lock:
            placeholders = ",".join("?" * len(keys))
            row = self._db.execute(
                f"SELECT f.recipe FROM shared_files f JOIN shared_file_owners o ON f.file_id = o.file_id "
                f"WHERE f.file_id = ? AND o.user_key IN ({placeholders}) "
                f"AND (f.expires_at = 0 OR f.expires_at > ?) LIMIT 1",
                (file_id, *keys, now),
            ).fetchone()
            if row is None:
                return False
            sf = SharedFile.from_dict(json.loads(row[0]))
            sf.pinned = True
            self._db.execute(
                "UPDATE shared_files SET pinned = 1, recipe = ?, cached_blob = COALESCE(?, cached_blob) "
                "WHERE file_id = ?",
                (json.dumps(sf.to_dict(), ensure_ascii=False), cache_bytes, file_id),
            )
            self._db.commit()
            return True

    async def read_cached(self, user: Mapping[str, Any], file_id: str) -> bytes | None:
        keys = shared_file_keys(user)
        if not keys:
            return None
        async with self._lock:
            placeholders = ",".join("?" * len(keys))
            row = self._db.execute(
                f"SELECT f.cached_blob FROM shared_files f JOIN shared_file_owners o ON f.file_id = o.file_id "
                f"WHERE f.file_id = ? AND o.user_key IN ({placeholders}) LIMIT 1",
                (file_id, *keys),
            ).fetchone()
        if row is None or row[0] is None:
            return None
        return bytes(row[0])

    async def purge_expired(self) -> int:
        async with self._lock:
            return self._purge(_now())

    def _purge(self, now: int) -> int:
        rows = self._db.execute(
            "SELECT file_id FROM shared_files WHERE expires_at != 0 AND expires_at <= ?", (now,)
        ).fetchall()
        ids = [r[0] for r in rows]
        if ids:
            placeholders = ",".join("?" * len(ids))
            self._db.execute(f"DELETE FROM shared_files WHERE file_id IN ({placeholders})", ids)
            self._db.execute(f"DELETE FROM shared_file_owners WHERE file_id IN ({placeholders})", ids)
            self._db.commit()
        return len(ids)


class SharedFileResolver:
    r"""
    用户分享文件的**唯一取字节收口**：按请求用户解析句柄、经 [feishu.im.messages.IMNamespace.get_resource][]
    重取字节（或命中 pin 缓存），施加硬性大小上限，任何失败一律 fail-closed（返回 `None`）。

    字节只会在这里短暂出现，且仅供消费工具在本次调用内使用——绝不进入模型上下文。资源重取使用**租户**
    客户端（机器人对收到的消息天然有 `im:resource` 权限），`user` 仅用于按请求用户做隔离（绝不可由模型指定）。

    Args:
        store: 句柄存储 [feishu.agent.shared_files.SharedFileStore][]。
        client: 租户态飞书客户端，提供 `im.get_resource(message_id, file_key, resource_type=...)`。
        max_materialize_bytes: 单次取字节的硬上限；超限按 fail-closed 处理。默认为 20 MiB。
        logger: 日志器。
    """

    def __init__(
        self,
        store: SharedFileStore,
        client: Any,
        *,
        max_materialize_bytes: int = 20 * 1024 * 1024,
        logger: logging.Logger | None = None,
    ) -> None:
        self.store = store
        self.client = client
        self.max_materialize_bytes = max_materialize_bytes
        self._log = logger or logging.getLogger("feishu")

    async def read_bytes(self, user: Mapping[str, Any], file_id: str) -> tuple[bytes, SharedFile] | None:
        r"""
        返回 `(bytes, SharedFile)`；句柄非本人所有 / 已过期 / 源资源失效 / 超限时一律返回 `None`（fail-closed）。

        步骤：按 `user` 隔离解析句柄 → 命中 pin 缓存直接返回 → 否则以租户客户端 `im.get_resource` 重取 →
        校验大小上限 → 若句柄已 pin 则回填缓存。任何异常都被吞掉并记录，绝不抛给上层（也绝不返回部分/陈旧字节）。
        """
        sf = await self.store.get(user, file_id)
        if sf is None:
            return None  # not found / not owned by this user / expired
        if sf.size is not None and sf.size > self.max_materialize_bytes:
            self._log.warning(
                "shared file %s exceeds size ceiling (%s > %s)", file_id, sf.size, self.max_materialize_bytes
            )
            return None
        cached = await self.store.read_cached(user, file_id)
        if cached is not None:
            return cached, sf
        try:
            data = bytes(await self.client.im.get_resource(sf.message_id, sf.file_key, resource_type=sf.resource_type))
        except (
            Exception
        ) as exc:  # noqa: BLE001 — resource gone / unauthorized: fail closed, never raise or return stale bytes
            self._log.warning("shared file %s could not be materialized: %s", file_id, exc)
            return None
        if len(data) > self.max_materialize_bytes:
            self._log.warning("shared file %s materialized over ceiling (%s bytes)", file_id, len(data))
            return None
        if sf.pinned:
            # Best-effort cache refill for future reads; a cache-write failure must not propagate (the contract
            # is never-raise) — we already hold fresh bytes and return them regardless.
            try:
                await self.store.pin(user, file_id, cache_bytes=data)
            except Exception:  # noqa: BLE001 — caching is best-effort; fresh bytes are returned either way
                self._log.warning("shared file %s could not be re-cached after refetch", file_id, exc_info=True)
        return data, sf

    async def recent(self, user: Mapping[str, Any], *, limit: int = 10) -> list[SharedFile]:
        r"""按请求用户返回最近未过期的句柄（委托给底层 store，便于发现工具读取）。"""
        return await self.store.recent(user, limit=limit)

    async def pin(self, user: Mapping[str, Any], file_id: str) -> bool:
        r"""
        在审批挂起前「钉住」一个文件：立刻取一次字节并缓存，使审批通过后的消费不会因源资源过期而失败。

        已有缓存则仅标记 pinned；否则以租户客户端取字节、校验上限后缓存。非本人所有 / 取字节失败 / 超限返回 `False`。
        """
        sf = await self.store.get(user, file_id)
        if sf is None:
            return False
        cached = await self.store.read_cached(user, file_id)
        if cached is not None:
            return await self.store.pin(user, file_id)
        try:
            data = bytes(await self.client.im.get_resource(sf.message_id, sf.file_key, resource_type=sf.resource_type))
        except Exception as exc:  # noqa: BLE001 — best-effort durability; never raise into the approval flow
            self._log.warning("pin-on-approval: could not fetch shared file %s: %s", file_id, exc)
            return False
        if len(data) > self.max_materialize_bytes:
            return False
        return await self.store.pin(user, file_id, cache_bytes=data)


def _connect(db_path: str | Path) -> sqlite3.Connection:
    # Shared hardened connector: WAL + 0o700 data dir + 0o600 files (protects pinned file bytes at rest).
    from .._sqlite import connect

    return connect(db_path)


__all__ = [
    "SharedFile",
    "SharedFileStore",
    "InMemorySharedFileStore",
    "SqliteSharedFileStore",
    "SharedFileResolver",
    "shared_file_keys",
]
