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
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ..consts import MIN_TOKEN_TTL, TOKEN_REFRESH_OFFSET

if TYPE_CHECKING:
    from .._transport import Transport
    from .credentials import Credential


@dataclass
class CachedToken:
    r"""
    缓存中的访问凭证。

    Args:
        value: 访问凭证。
        expire_at: 凭证的过期时刻，与 [feishu.auth.tokens.TokenManager][] 使用的时钟同源。

    Examples:
        >>> token = CachedToken("t-1", 5400.0)
        >>> token.value
        't-1'
        >>> token.expire_at
        5400.0
    """

    value: str
    expire_at: float


class TokenCache(Protocol):
    r"""
    访问凭证缓存协议。

    实现该协议即可作为 [feishu.auth.tokens.TokenManager][] 的凭证缓存后端。
    默认实现为进程内缓存 [feishu.auth.tokens.InMemoryTokenCache][]；如需在多个进程或
    实例间共享凭证，可自行实现基于 Redis 等外部存储的缓存。
    """

    async def get(self, key: str) -> CachedToken | None:
        r"""
        读取缓存的凭证。

        Args:
            key: 由 [feishu.auth.credentials.Credential.cache_key][] 生成的缓存键。

        Returns:
            命中的凭证；未命中时返回 `None`。
        """

    async def set(self, key: str, token: CachedToken) -> None:
        r"""
        写入缓存的凭证。

        Args:
            key: 由 [feishu.auth.credentials.Credential.cache_key][] 生成的缓存键。
            token: 待缓存的凭证。
        """


class InMemoryTokenCache:
    r"""
    进程内访问凭证缓存。

    [feishu.auth.tokens.TokenCache][] 的默认实现，将凭证保存在进程内存中。
    凭证不会在进程间共享，进程退出后即失效；如需跨进程共享，请自行实现 `TokenCache` 协议。

    Examples:
        >>> import asyncio
        >>> cache = InMemoryTokenCache()
        >>> asyncio.run(cache.set("k", CachedToken("t-1", 5400.0)))
        >>> asyncio.run(cache.get("k")).value
        't-1'
        >>> asyncio.run(cache.get("missing")) is None
        True
    """

    def __init__(self) -> None:
        self._store: dict[str, CachedToken] = {}

    async def get(self, key: str) -> CachedToken | None:
        r"""
        读取缓存的凭证。

        Args:
            key: 缓存键。

        Returns:
            命中的凭证；未命中时返回 `None`。
        """
        return self._store.get(key)

    async def set(self, key: str, token: CachedToken) -> None:
        r"""
        写入缓存的凭证。

        Args:
            key: 缓存键。
            token: 待缓存的凭证。
        """
        self._store[key] = token


class TokenManager:
    r"""
    应用级访问凭证管理器。

    在凭据与传输层之上提供凭证的缓存、过期前刷新与并发去重：同一凭证只会被换取一次并复用，
    临近过期时自动重新换取，且一批并发调用只会触发一次换取（避免惊群）。
    不同凭证类型（如 `tenant` 与 `app`）使用各自独立的锁，互不阻塞。

    Args:
        credential: 用于换取凭证的应用凭据，参见 [feishu.auth.credentials.Credential][]。
        transport: 用于发起请求的传输层。
        cache: 凭证缓存后端。默认为 [feishu.auth.tokens.InMemoryTokenCache][]。
        refresh_offset: 过期前的提前刷新秒数，凭证在距过期不足该秒数时即重新换取。
        min_ttl: 扣除 `refresh_offset` 后的最小有效缓存时长（秒）。当凭证有效期较短
            （`expire <= refresh_offset`）时，过期时刻本会落在当前时刻或之前，导致下一次读取
            立刻重新换取（惊群）；此时改为缓存 `min_ttl` 秒，确保短期凭证仍被短暂复用。
        now: 返回当前时刻的单调时钟函数，主要用于测试注入。

    飞书文档:
        `tenant_access_token`:
        https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token

        `app_access_token`:
        https://open.feishu.cn/document/server-docs/authentication-management/access-token/app_access_token
    """

    def __init__(
        self,
        credential: Credential,
        transport: Transport,
        *,
        cache: TokenCache | None = None,
        refresh_offset: int = TOKEN_REFRESH_OFFSET,
        min_ttl: int = MIN_TOKEN_TTL,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._credential = credential
        self._transport = transport
        self._cache = cache or InMemoryTokenCache()
        self._refresh_offset = refresh_offset
        self._min_ttl = min_ttl
        self._now = now
        # One lock per cache key, so fetching one token type (e.g. tenant) never
        # blocks fetching another (e.g. a user token). Created lazily; safe to do
        # without a guard since asyncio is single-threaded and there is no await
        # between the check and the insert below.
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    async def tenant_access_token(self) -> str:
        r"""
        获取租户访问凭证（`tenant_access_token`）。

        等价于 `token("tenant")`，命中缓存且未临近过期时直接复用，否则重新换取。

        Returns:
            租户访问凭证。

        飞书文档:
            `tenant_access_token`:
            https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token

        Examples:
            >>> token = await manager.tenant_access_token()  # doctest: +SKIP
            >>> token  # doctest: +SKIP
            't-xxxxxxxx'
        """
        return await self.token("tenant")

    async def token(self, token_type: str = "tenant") -> str:
        r"""
        获取指定类型的应用级访问凭证。

        命中缓存且距过期不少于 `refresh_offset` 秒时直接复用缓存值，否则换取新凭证并写入缓存。
        同一凭证类型上的并发调用会在锁内做双重检查，最终只触发一次换取。

        Args:
            token_type: 凭证类型，`tenant` 或 `app`。默认为 `tenant`。

        Returns:
            对应类型的访问凭证。

        飞书文档:
            `tenant_access_token`:
            https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token

            `app_access_token`:
            https://open.feishu.cn/document/server-docs/authentication-management/access-token/app_access_token

        Examples:
            >>> token = await manager.token("app")  # doctest: +SKIP
            >>> token  # doctest: +SKIP
            'a-xxxxxxxx'
        """
        key = self._credential.cache_key(token_type, self._transport.base_url)
        cached = await self._cache.get(key)
        if cached is not None and cached.expire_at > self._now():
            return cached.value
        async with self._lock_for(key):
            cached = await self._cache.get(key)  # double-check after acquiring
            if cached is not None and cached.expire_at > self._now():
                return cached.value
            value, expire = await self._credential.fetch(self._transport, token_type)
            # Clamp the effective TTL: a short token (expire <= refresh_offset) would
            # otherwise yield expire_at <= now and be re-fetched on the next read.
            ttl = max(expire - self._refresh_offset, self._min_ttl)
            expire_at = self._now() + ttl
            await self._cache.set(key, CachedToken(value, expire_at))
            return value
