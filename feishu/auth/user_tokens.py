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
用户态访问凭证的持久化与按需刷新：把「以用户身份执行」做成一个标准接缝。

[feishu.auth.oauth.OAuthNamespace][] 已封装授权 URL、`exchange_code`、`refresh`、`user_info` 等线缆调用，但
不负责「持久化每个用户的 `user_access_token` / `refresh_token` 并在临近过期时自动刷新」。本模块补上这一层：
[feishu.auth.user_tokens.OAuthTokenStore][] 抽象按用户多别名（open_id/union_id/user_id）存取凭证，
[feishu.auth.user_tokens.UserTokenProvider][] 据此解析并刷新凭证、产出用户态客户端（`client.as_user(token)`）。
其 `as_user(user)` 正好契合 [feishu.agent.context.ToolContext][] 所需的提供方接口，可直接传给
[feishu.agent.loop.Agent][] 的 `user_tokens` 参数。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

_USER_ID_KEYS = ("open_id", "union_id", "user_id")


def _now() -> int:
    return int(time.time())


def user_identity_keys(user: Mapping[str, Any]) -> tuple[str, ...]:
    r"""
    把用户标识映射归一为稳定、**带类型前缀**的多别名键（如 `open_id:ou_x`）——全库唯一的用户身份键表示。

    前缀（`open_id:` / `union_id:` / `user_id:`）避免不同 ID 类型的裸值互相碰撞。用于按用户隔离 token / 分享
    文件 / 收款账户等存储，以及把审批绑定到发起人。逆操作见 [feishu.auth.user_tokens.user_from_identity_keys][]。
    """
    return tuple(f"{key}:{user[key]}" for key in _USER_ID_KEYS if user.get(key))


def user_from_identity_keys(keys: Iterable[str]) -> dict[str, str]:
    r"""[feishu.auth.user_tokens.user_identity_keys][] 的逆：把带前缀的身份键还原为 `{kind: value}` 用户标识映射。"""
    valid = set(_USER_ID_KEYS)
    user: dict[str, str] = {}
    for key in keys:
        kind, _, value = key.partition(":")
        if kind in valid and value and kind not in user:
            user[kind] = value
    return user


def user_keys(user: Mapping[str, Any]) -> tuple[str, ...]:
    r"""用户的稳定多别名键；[feishu.auth.user_tokens.user_identity_keys][] 的别名（全库统一表示）。"""
    return user_identity_keys(user)


def _coerce_int(value: Any) -> int | None:
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
class TokenRecord:
    r"""
    一个用户的凭证记录：用户态访问令牌、（轮换式）刷新令牌、各项过期时间与多别名键。

    飞书的 `refresh_token` 为一次性轮换：每次刷新都会得到全新的刷新令牌，旧令牌随即失效，因此刷新后必须
    整体覆盖保存本记录。

    Examples:
        >>> rec = TokenRecord(user_access_token="u-acc", expires_at=_now() + 7200)
        >>> rec.is_expired(_now())
        False
    """

    user_access_token: str
    refresh_token: str | None = None
    expires_at: int | None = None
    refresh_token_expires_at: int | None = None
    scope: str | None = None
    user_keys: tuple[str, ...] = ()

    def is_expiring(self, skew_seconds: int, now: int) -> bool:
        r"""访问令牌是否将在 `skew_seconds` 内过期。"""
        return self.expires_at is not None and self.expires_at - now <= skew_seconds

    def is_expired(self, now: int) -> bool:
        r"""访问令牌是否已过期。"""
        return self.expires_at is not None and self.expires_at <= now

    def to_dict(self) -> dict[str, Any]:
        data = {
            "user_access_token": self.user_access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "refresh_token_expires_at": self.refresh_token_expires_at,
            "scope": self.scope,
            "user_keys": list(self.user_keys),
        }
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TokenRecord:
        return cls(
            user_access_token=str(data.get("user_access_token") or ""),
            refresh_token=data.get("refresh_token"),
            expires_at=data.get("expires_at"),
            refresh_token_expires_at=data.get("refresh_token_expires_at"),
            scope=data.get("scope"),
            user_keys=tuple(data.get("user_keys") or ()),
        )

    @classmethod
    def from_token_data(cls, token_data: Mapping[str, Any], keys: tuple[str, ...], *, now: int) -> TokenRecord:
        r"""从 `exchange_code` / `refresh` 的响应构造记录（`expires_in` 等折算为绝对过期时间）。"""
        expires_in = _coerce_int(token_data.get("expires_in"))
        refresh_expires_in = _coerce_int(token_data.get("refresh_token_expires_in"))
        refresh_token = token_data.get("refresh_token")
        scope = token_data.get("scope")
        return cls(
            user_access_token=str(token_data.get("access_token") or ""),
            refresh_token=str(refresh_token) if refresh_token else None,
            expires_at=(now + expires_in) if expires_in is not None else None,
            refresh_token_expires_at=(now + refresh_expires_in) if refresh_expires_in is not None else None,
            scope=str(scope) if scope else None,
            user_keys=tuple(keys),
        )


@runtime_checkable
class OAuthTokenStore(Protocol):
    r"""
    用户态凭证存储协议：按用户多别名存取 [feishu.auth.user_tokens.TokenRecord][]。

    内置实现为 [feishu.auth.user_tokens.InMemoryOAuthTokenStore][] 与
    [feishu.auth.user_tokens.SqliteOAuthTokenStore][]。该协议标注了 `runtime_checkable`。
    """

    async def get(self, user: Mapping[str, Any]) -> TokenRecord | None:
        r"""按用户的任一别名键读取凭证记录，未命中返回 `None`。"""
        ...

    async def save(
        self,
        token_data: Mapping[str, Any],
        *,
        user_info: Mapping[str, Any] | None = None,
        user_keys: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        r"""保存一次凭证响应；用户身份取自 `user_info` 或显式 `user_keys`，返回写入的别名键。"""
        ...


def _keys_for_save(user_info: Mapping[str, Any] | None, explicit: tuple[str, ...]) -> tuple[str, ...]:
    keys = user_keys(user_info) if user_info else tuple(explicit)
    if not keys:
        raise ValueError("cannot save a user token without a user identity (open_id/union_id/user_id)")
    return keys


class InMemoryOAuthTokenStore:
    r"""
    基于内存的 [feishu.auth.user_tokens.OAuthTokenStore][] 实现，仅适用于单进程、可接受重启即丢失的场景。

    Examples:
        >>> isinstance(InMemoryOAuthTokenStore(), OAuthTokenStore)
        True
    """

    def __init__(self) -> None:
        self._by_key: dict[str, TokenRecord] = {}
        self._lock = asyncio.Lock()

    async def get(self, user: Mapping[str, Any]) -> TokenRecord | None:
        keys = user_keys(user)
        async with self._lock:
            for key in keys:
                record = self._by_key.get(key)
                if record is not None:
                    return record
        return None

    async def save(
        self,
        token_data: Mapping[str, Any],
        *,
        user_info: Mapping[str, Any] | None = None,
        user_keys: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        keys = _keys_for_save(user_info, user_keys)
        record = TokenRecord.from_token_data(token_data, keys, now=_now())
        async with self._lock:
            # Reconcile aliases: drop prior rows for this identity so a re-auth with fewer aliases
            # cannot leave a stale alias pointing at the dead old record.
            stale: set[str] = set()
            for key in keys:
                old = self._by_key.get(key)
                if old is not None:
                    stale.update(old.user_keys)
            for alias in stale - set(keys):
                self._by_key.pop(alias, None)
            for key in keys:
                self._by_key[key] = record
        return keys


class SqliteOAuthTokenStore:
    r"""
    基于 SQLite 的 [feishu.auth.user_tokens.OAuthTokenStore][] 实现，用户态凭证跨重启存活。

    同一用户的每个别名键写入一行、指向同一份记录 JSON，从而 open_id / union_id / user_id 任一皆可命中。

    Args:
        db_path: SQLite 数据库文件路径。

    Examples:
        >>> store = SqliteOAuthTokenStore(":memory:")  # doctest:+SKIP
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db = _connect(db_path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS oauth_tokens "
            "(user_key TEXT PRIMARY KEY, record TEXT NOT NULL, updated_at INTEGER)"
        )
        self._db.commit()
        self._lock = asyncio.Lock()

    async def get(self, user: Mapping[str, Any]) -> TokenRecord | None:
        keys = user_keys(user)
        async with self._lock:
            for key in keys:
                row = self._db.execute("SELECT record FROM oauth_tokens WHERE user_key = ?", (key,)).fetchone()
                if row is not None:
                    return TokenRecord.from_dict(json.loads(row[0]))
        return None

    async def save(
        self,
        token_data: Mapping[str, Any],
        *,
        user_info: Mapping[str, Any] | None = None,
        user_keys: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        keys = _keys_for_save(user_info, user_keys)
        record = TokenRecord.from_token_data(token_data, keys, now=_now())
        payload = json.dumps(record.to_dict(), ensure_ascii=False)
        now = _now()
        async with self._lock:
            # Reconcile aliases: collect every alias of any existing row that shares a key with the
            # new identity, then delete them all and re-insert only the current key set — so a re-auth
            # with fewer aliases cannot leave a stale row pointing at the dead old record.
            placeholders = ",".join("?" * len(keys))
            rows = self._db.execute(
                f"SELECT record FROM oauth_tokens WHERE user_key IN ({placeholders})", tuple(keys)
            ).fetchall()
            stale: set[str] = set(keys)
            for (record_json,) in rows:
                stale.update(TokenRecord.from_dict(json.loads(record_json)).user_keys)
            delete_placeholders = ",".join("?" * len(stale))
            self._db.execute(f"DELETE FROM oauth_tokens WHERE user_key IN ({delete_placeholders})", tuple(stale))
            self._db.executemany(
                "INSERT INTO oauth_tokens (user_key, record, updated_at) VALUES (?, ?, ?)",
                [(key, payload, now) for key in keys],
            )
            self._db.commit()
        return keys


class UserTokenProvider:
    r"""
    解析并按需刷新用户态凭证，产出用户态飞书客户端。

    `as_user(user)` 正是 [feishu.agent.context.ToolContext][] 所需的提供方接口：传给
    [feishu.agent.loop.Agent][] 的 `user_tokens` 后，工具即可以请求用户的身份执行读写。访问令牌将在临近过期
    （`refresh_skew_seconds`）时用一次性轮换的 `refresh_token` 刷新并整体覆盖保存。

    Args:
        client: 飞书客户端，需提供 `oauth`（授权/刷新/用户信息）与 `as_user(token)`。
        store: 用户态凭证存储 [feishu.auth.user_tokens.OAuthTokenStore][]。
        refresh_skew_seconds: 提前刷新的余量秒数。默认为 `60`。
        logger: 日志器。
    """

    def __init__(
        self,
        client: Any,
        store: OAuthTokenStore,
        *,
        refresh_skew_seconds: int = 60,
        logger: logging.Logger | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.refresh_skew_seconds = refresh_skew_seconds
        self._log = logger or logging.getLogger("feishu")

    async def user_token(self, user: Mapping[str, Any]) -> str | None:
        r"""解析有效的用户态访问令牌；临近过期则刷新；无记录或已失效返回 `None`。"""
        record = await self.store.get(user)
        if record is None:
            return None
        if record.refresh_token and record.is_expiring(self.refresh_skew_seconds, _now()):
            try:
                token_data = await self.client.oauth.refresh(record.refresh_token)
                await self.store.save(token_data, user_keys=record.user_keys)
                record = await self.store.get(user)
            except Exception:  # noqa: BLE001 — fall back to the stored token; drop only if hard-expired
                self._log.warning("user_token: refresh failed; falling back to the stored token", exc_info=True)
        if record is None or record.is_expired(_now()) or not record.user_access_token:
            return None
        return record.user_access_token

    async def as_user(self, user: Mapping[str, Any]) -> Any | None:
        r"""产出当前用户的用户态飞书客户端；无有效凭证时返回 `None`。"""
        token = await self.user_token(user)
        if not token:
            return None
        return self.client.as_user(token)

    async def complete_authorization(self, code: str, *, redirect_uri: str | None = None) -> tuple[str, ...]:
        r"""完成 OAuth 回调：用 `code` 换取凭证、读取用户信息并保存，返回写入的别名键。"""
        token_data = await self.client.oauth.exchange_code(code, redirect_uri=redirect_uri)
        access_token = token_data.get("access_token")
        info = await self.client.oauth.user_info(access_token) if access_token else {}
        return await self.store.save(token_data, user_info=dict(info))

    def authorize_url(
        self, redirect_uri: str, *, scope: str | list[str] | tuple[str, ...] | None = None, state: str | None = None
    ) -> str:
        r"""生成授权跳转 URL（透传给 [feishu.auth.oauth.OAuthNamespace.authorize_url][]）。"""
        return self.client.oauth.authorize_url(redirect_uri, scope=scope, state=state)


def _connect(db_path: str | Path) -> sqlite3.Connection:
    # Shared hardened connector: WAL + 0o700 data dir + 0o600 files (protects token-at-rest sidecars).
    from .._sqlite import connect

    return connect(db_path)
