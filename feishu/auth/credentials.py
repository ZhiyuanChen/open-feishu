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

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Protocol

from ..errors import FeishuError

if TYPE_CHECKING:
    from .._transport import Transport


class Credential(ABC):
    r"""
    应用凭据抽象基类。

    凭据负责换取应用级访问凭证（`tenant_access_token` 或 `app_access_token`），
    并为缓存提供一个能区分不同凭据、凭证类型与服务器地址的缓存键。
    [feishu.auth.tokens.TokenManager][] 依赖本接口完成凭证的获取与缓存。

    自建应用请使用 [feishu.auth.credentials.InternalCredential][]。
    """

    @abstractmethod
    def cache_key(self, token_type: str, base_url: str) -> str:
        r"""
        生成凭证的缓存键。

        缓存键需在凭据、凭证类型与服务器地址三个维度上互不冲突，
        以保证不同应用、不同凭证类型、不同区域之间的凭证不会相互覆盖。

        Args:
            token_type: 凭证类型，`tenant` 或 `app`。
            base_url: 飞书开放平台服务器地址。

        Returns:
            唯一的缓存键。
        """

    @abstractmethod
    async def fetch(self, transport: Transport, token_type: str) -> tuple[str, int]:
        r"""
        换取应用级访问凭证。

        Args:
            transport: 用于发起请求的传输层。
            token_type: 凭证类型，`tenant` 或 `app`。

        Returns:
            由访问凭证与有效期（秒）组成的二元组。
        """


class InternalCredential(Credential):
    r"""
    自建应用凭据。

    使用应用的 `app_id` 与 `app_secret` 换取 `tenant_access_token` 或
    `app_access_token`。两个秘钥均在请求体中传递，因此换取凭证的请求本身不携带任何鉴权头。

    Args:
        app_id: 应用唯一标识，以 `cli_` 开头。
        app_secret: 应用秘钥。

    Raises:
        ValueError: 当 `app_id` 或 `app_secret` 为空时抛出。

    飞书文档:
        `tenant_access_token` (自建应用):
        https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal

        `app_access_token` (自建应用):
        https://open.feishu.cn/document/server-docs/authentication-management/access-token/app_access_token_internal

    Examples:
        >>> cred = InternalCredential("cli_demo", "secret")
        >>> cred.app_id
        'cli_demo'
        >>> InternalCredential("", "secret")
        Traceback (most recent call last):
            ...
        ValueError: InternalCredential requires both app_id and app_secret
    """

    def __init__(self, app_id: str, app_secret: str) -> None:
        if not app_id or not app_secret:
            raise ValueError("InternalCredential requires both app_id and app_secret")
        self.app_id = app_id
        self.app_secret = app_secret

    def cache_key(self, token_type: str, base_url: str) -> str:
        r"""
        生成自建应用凭证的缓存键。

        缓存键由应用标识、凭证类型与服务器地址组成，确保不同应用、不同凭证类型、
        不同区域之间的凭证互不冲突。

        Args:
            token_type: 凭证类型，`tenant` 或 `app`。
            base_url: 飞书开放平台服务器地址。

        Returns:
            唯一的缓存键。

        Examples:
            >>> InternalCredential("cli_demo", "secret").cache_key("tenant", "https://open.feishu.cn")
            'internal:cli_demo:tenant:https://open.feishu.cn'
        """
        return f"internal:{self.app_id}:{token_type}:{base_url}"

    async def fetch(self, transport: Transport, token_type: str) -> tuple[str, int]:
        r"""
        换取自建应用的访问凭证。

        `app_id` 与 `app_secret` 在请求体中传递，因此该请求不携带任何鉴权头。

        Args:
            transport: 用于发起请求的传输层。
            token_type: 凭证类型，`tenant` 或 `app`。

        Returns:
            由访问凭证与有效期（秒）组成的二元组，有效期为正整数。

        Raises:
            ValueError: 当 `token_type` 不是 `tenant` 或 `app` 时抛出。
            FeishuError: 当响应中的 `expire` 缺失、非整数或不是正整数时抛出。

        飞书文档:
            `tenant_access_token` (自建应用):
        https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal

            `app_access_token` (自建应用):
        https://open.feishu.cn/document/server-docs/authentication-management/access-token/app_access_token_internal

        Examples:
            >>> cred = InternalCredential("cli_demo", "secret")
            >>> token, expire = await cred.fetch(transport, "tenant")  # doctest: +SKIP
            >>> token, expire  # doctest: +SKIP
            ('t-xxxxxxxx', 7200)
        """
        if token_type not in ("tenant", "app"):
            raise ValueError(f"unsupported token_type {token_type!r}")
        path = f"auth/v3/{token_type}_access_token/internal"
        body = {"app_id": self.app_id, "app_secret": self.app_secret}
        envelope = await transport.request("POST", path, json=body, token=None)
        return envelope[f"{token_type}_access_token"], _parse_expire(envelope.get("expire"))


class AppTicketStore(Protocol):
    r"""
    `app_ticket` 存储协议。

    商店应用（ISV）无法直接用 `app_id` + `app_secret` 换取 `app_access_token`，
    还需一个由飞书每隔约一小时通过 `app_ticket` 事件推送的 `app_ticket`。
    实现该协议即可作为 [feishu.auth.credentials.StoreCredential][] 的 `app_ticket` 存储后端：
    收到并解密 `app_ticket` 事件后调用 `set` 写入，换取凭证时由凭据调用 `get` 读取。
    默认实现为进程内存储 [feishu.auth.credentials.InMemoryAppTicketStore][]；如需在多个进程或
    实例间共享，可自行实现基于 Redis 等外部存储的后端。
    """

    async def get(self, app_id: str) -> str | None:
        r"""
        读取应用的 `app_ticket`。

        Args:
            app_id: 应用唯一标识，以 `cli_` 开头。

        Returns:
            命中的 `app_ticket`；未命中时返回 `None`。
        """

    async def set(self, app_id: str, app_ticket: str) -> None:
        r"""
        写入应用的 `app_ticket`。

        Args:
            app_id: 应用唯一标识，以 `cli_` 开头。
            app_ticket: 飞书通过 `app_ticket` 事件推送并经解密后的最新 `app_ticket`。
        """


class InMemoryAppTicketStore:
    r"""
    进程内 `app_ticket` 存储。

    [feishu.auth.credentials.AppTicketStore][] 的默认实现，将 `app_ticket` 保存在进程内存中。
    `app_ticket` 不会在进程间共享，进程退出后即失效；如需跨进程共享，请自行实现 `AppTicketStore` 协议。

    Examples:
        >>> import asyncio
        >>> store = InMemoryAppTicketStore()
        >>> asyncio.run(store.set("cli_demo", "ticket-1"))
        >>> asyncio.run(store.get("cli_demo"))
        'ticket-1'
        >>> asyncio.run(store.get("cli_missing")) is None
        True
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, app_id: str) -> str | None:
        r"""
        读取应用的 `app_ticket`。

        Args:
            app_id: 应用唯一标识。

        Returns:
            命中的 `app_ticket`；未命中时返回 `None`。
        """
        return self._store.get(app_id)

    async def set(self, app_id: str, app_ticket: str) -> None:
        r"""
        写入应用的 `app_ticket`。

        Args:
            app_id: 应用唯一标识。
            app_ticket: 待存储的 `app_ticket`。
        """
        self._store[app_id] = app_ticket


class StoreCredential(Credential):
    r"""
    商店应用（ISV）凭据。

    商店应用无法直接用 `app_id` + `app_secret` 换取凭证，还需一个由飞书每隔约一小时通过
    `app_ticket` 事件推送的 `app_ticket`。换取流程为：用 `app_id` + `app_secret` + `app_ticket`
    换取 `app_access_token`，再用该 `app_access_token` + `tenant_key` 换取某租户的
    `tenant_access_token`。所有秘钥与凭据均在请求体中传递，因此换取凭证的请求本身不携带任何鉴权头。

    `app_ticket` 由 [feishu.auth.credentials.AppTicketStore][] 提供：在收到并解密 `app_ticket`
    事件后，调用 `await store.set(app_id, app_ticket)` 写入；若存储中没有可用的 `app_ticket`，
    本凭据会先请求飞书重新推送（`auth/v3/app_ticket/resend`）再抛出 [feishu.errors.FeishuError][]，
    待 `app_ticket` 事件到达并写入后重试即可。

    Args:
        app_id: 应用唯一标识，以 `cli_` 开头。
        app_secret: 应用秘钥。
        tenant_key: 租户唯一标识，用于换取对应租户的 `tenant_access_token`。
        app_ticket_store: `app_ticket` 存储后端。默认为
            [feishu.auth.credentials.InMemoryAppTicketStore][]。

    Raises:
        ValueError: 当 `app_id`、`app_secret` 或 `tenant_key` 为空时抛出。

    飞书文档:
        `app_access_token` (商店应用):
        https://open.feishu.cn/document/server-docs/authentication-management/access-token/app_access_token

        `tenant_access_token` (商店应用):
        https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token

    Examples:
        >>> from feishu.auth.credentials import InMemoryAppTicketStore
        >>> store = InMemoryAppTicketStore()
        >>> cred = StoreCredential("cli_demo", "secret", "tenant-1", app_ticket_store=store)
        >>> cred.app_id, cred.tenant_key
        ('cli_demo', 'tenant-1')
        >>> StoreCredential("", "secret", "tenant-1")
        Traceback (most recent call last):
            ...
        ValueError: StoreCredential requires app_id, app_secret and tenant_key

        收到并解密 `app_ticket` 事件后写入存储，再据此构造客户端：

        >>> await store.set("cli_demo", app_ticket)  # doctest: +SKIP
        >>> client = FeishuClient(  # doctest: +SKIP
        ...     credential=StoreCredential("cli_demo", "secret", "tenant-1", app_ticket_store=store)
        ... )
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        tenant_key: str,
        *,
        app_ticket_store: AppTicketStore | None = None,
    ) -> None:
        if not app_id or not app_secret or not tenant_key:
            raise ValueError("StoreCredential requires app_id, app_secret and tenant_key")
        self.app_id = app_id
        self.app_secret = app_secret
        self.tenant_key = tenant_key
        self.app_ticket_store: AppTicketStore = app_ticket_store or InMemoryAppTicketStore()

    def cache_key(self, token_type: str, base_url: str) -> str:
        r"""
        生成商店应用凭证的缓存键。

        缓存键由应用标识、租户标识、凭证类型与服务器地址组成。其中租户标识确保不同租户的凭证
        互不冲突——这是商店应用与自建应用的关键区别，缺少它会导致不同租户的 `tenant_access_token`
        相互覆盖。

        Args:
            token_type: 凭证类型，`tenant` 或 `app`。
            base_url: 飞书开放平台服务器地址。

        Returns:
            唯一的缓存键。

        Examples:
            >>> StoreCredential("cli_demo", "secret", "tenant-1").cache_key("tenant", "https://open.feishu.cn")
            'store:cli_demo:tenant-1:tenant:https://open.feishu.cn'
        """
        return f"store:{self.app_id}:{self.tenant_key}:{token_type}:{base_url}"

    async def _app_access_token(self, transport: Transport) -> tuple[str, int]:
        r"""
        换取商店应用的 `app_access_token`。

        从存储中读取本应用的 `app_ticket`；若缺失或为空，则先请求飞书重新推送 `app_ticket`
        事件（`auth/v3/app_ticket/resend`），再抛出 [feishu.errors.FeishuError][]，待事件到达
        并写入存储后重试。`app_id`、`app_secret` 与 `app_ticket` 均在请求体中传递，因此请求不
        携带任何鉴权头。

        Args:
            transport: 用于发起请求的传输层。

        Returns:
            由 `app_access_token` 与有效期（秒）组成的二元组。

        Raises:
            FeishuError: 当存储中没有可用的 `app_ticket` 时抛出（已同时请求重新推送）。
        """
        app_ticket = await self.app_ticket_store.get(self.app_id)
        if not app_ticket:
            # No app_ticket yet: ask Feishu to re-push the app_ticket event, then bail out.
            # Both secrets travel in the body, so this request carries no auth header either.
            await transport.request(
                "POST",
                "auth/v3/app_ticket/resend",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                token=None,
            )
            raise FeishuError(
                -1,
                f"app_ticket unavailable for app {self.app_id}; a resend was requested "
                "-- retry once the app_ticket event is received and stored",
            )
        body = {"app_id": self.app_id, "app_secret": self.app_secret, "app_ticket": app_ticket}
        envelope = await transport.request("POST", "auth/v3/app_access_token", json=body, token=None)
        return envelope["app_access_token"], _parse_expire(envelope.get("expire"))

    async def fetch(self, transport: Transport, token_type: str) -> tuple[str, int]:
        r"""
        换取商店应用的访问凭证。

        `app` 类型直接返回 `app_access_token`；`tenant` 类型先换取 `app_access_token`，
        再用其与 `tenant_key` 换取对应租户的 `tenant_access_token`。所有秘钥与凭据均在请求体中
        传递，因此这些请求均不携带任何鉴权头。

        Args:
            transport: 用于发起请求的传输层。
            token_type: 凭证类型，`tenant` 或 `app`。

        Returns:
            由访问凭证与有效期（秒）组成的二元组，有效期为正整数。

        Raises:
            ValueError: 当 `token_type` 不是 `tenant` 或 `app` 时抛出。
            FeishuError: 当存储中没有可用的 `app_ticket`，或响应中的 `expire` 缺失、非整数或不是
                正整数时抛出。

        飞书文档:
            `app_access_token` (商店应用):
            https://open.feishu.cn/document/server-docs/authentication-management/access-token/app_access_token

            `tenant_access_token` (商店应用):
            https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token

        Examples:
            >>> token, expire = await cred.fetch(transport, "tenant")  # doctest: +SKIP
            >>> token, expire  # doctest: +SKIP
            ('t-xxxxxxxx', 7200)
        """
        if token_type == "app":
            return await self._app_access_token(transport)
        if token_type == "tenant":
            app_access_token, _ = await self._app_access_token(transport)
            body = {"app_access_token": app_access_token, "tenant_key": self.tenant_key}
            envelope = await transport.request("POST", "auth/v3/tenant_access_token", json=body, token=None)
            return envelope["tenant_access_token"], _parse_expire(envelope.get("expire"))
        raise ValueError(f"unsupported token_type {token_type!r}")


def _parse_expire(value: Any) -> int:
    r"""
    校验并解析凭证有效期（秒）。

    飞书理应返回一个正整数有效期，但响应可能缺失该字段或返回非法值（如负数、字符串或
    `None`）。直接 `int(value)` 会抛出难以排查的 `TypeError`/`ValueError`，且无法拦截负数或
    零。本函数将其统一转换为正整数，对任何非法输入抛出 [feishu.errors.FeishuError][]，
    避免把垃圾有效期写入缓存（详见 [feishu.auth.tokens.TokenManager][] 的过期时刻计算）。

    Args:
        value: 响应中的 `expire` 字段，期望为正整数。

    Returns:
        校验通过的正整数有效期。

    Raises:
        FeishuError: 当 `value` 缺失、不是整数或不是正整数时抛出。

    Examples:
        >>> _parse_expire(7200)
        7200
        >>> _parse_expire(0)
        Traceback (most recent call last):
            ...
        feishu.errors.FeishuError: FeishuError(code=-1, message='invalid token expire: 0', log_id=None)
        >>> _parse_expire("oops")
        Traceback (most recent call last):
            ...
        feishu.errors.FeishuError: FeishuError(code=-1, message="invalid token expire: 'oops'", log_id=None)
    """
    # ``bool`` is an ``int`` subclass; reject it so a stray ``True`` is not read as ``1``.
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise FeishuError(-1, f"invalid token expire: {value!r}")
    return value
