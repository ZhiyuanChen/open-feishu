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

from urllib.parse import urlencode

from chanfig import NestedDict

from .._namespace import Namespace
from ..consts import API_PREFIX, AUTHORIZE_PATH, OAUTH_TOKEN_PATH, USER_INFO_PATH, resolve_accounts_url
from .credentials import InternalCredential

_CREDENTIAL_ERROR = "user OAuth requires an InternalCredential (app_id/app_secret)"


class OAuthNamespace(Namespace):
    r"""
    用户身份授权（登录）能力，挂载于 `client.oauth`。

    仅封装飞书用户 OAuth 的通用流程：构建授权页 URL、用授权码换取 `user_access_token`、
    刷新该凭证，以及读取已登录用户的 `user_info`。本命名空间是无状态的，
    每位用户或会话的凭证存储由调用方自行负责。

    所有流程均要求客户端配置自建应用凭据 [feishu.auth.credentials.InternalCredential][]，
    否则相关方法会抛出 [ValueError][]。

    飞书文档:
        [网页应用登录](https://open.feishu.cn/document/server-docs/authentication-management/login-state-management)
    """

    def authorize_url(
        self,
        redirect_uri: str,
        *,
        scope: str | list[str] | tuple[str, ...] | None = None,
        state: str | None = None,
        prompt: str | None = None,
    ) -> str:
        r"""
        构建用户授权页 URL（纯字符串拼接，不发起任何网络请求）。

        引导用户跳转到该地址完成授权后，飞书会携带授权码 `code` 回调至 `redirect_uri`，
        再调用 [feishu.auth.oauth.OAuthNamespace.exchange_code][] 换取 `user_access_token`。
        登录站点（accounts 域名）在调用时才根据区域解析，因此未知区域只会在此处抛错，
        而不会在客户端构造时抛错。

        Args:
            redirect_uri: 授权完成后的回调地址，需与应用后台配置的重定向 URL 一致。
            scope: 申请的权限范围，可为以空格分隔的字符串或字符串序列。默认不传。
            state: 透传的状态参数，回调时原样返回，常用于防 CSRF 与会话关联。默认不传。
            prompt: 授权页提示行为，例如 `consent` 强制展示授权确认页。默认不传。

        Returns:
            用户授权页的完整 URL。

        Raises:
            ValueError: 当客户端未配置 [feishu.auth.credentials.InternalCredential][]，
                或区域无法解析出 accounts 域名时抛出。

        飞书文档:
            [获取登录预授权码](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/authentication-management/login-state-management/obtain-oauth-code)

        Examples:
            >>> from feishu import FeishuClient
            >>> client = FeishuClient("cli_demo", "secret", region="feishu")
            >>> url = client.oauth.authorize_url(
            ...     "https://app.example.com/callback",
            ...     scope=["contact:user.id", "contact:user.email"],
            ...     state="xyz",
            ... )
            >>> url.startswith("https://accounts.feishu.cn/open-apis/authen/v1/authorize?")
            True
            >>> "client_id=cli_demo" in url
            True
            >>> "scope=contact%3Auser.id+contact%3Auser.email" in url
            True
            >>> "state=xyz" in url
            True
        """
        app_id = self._internal_credential().app_id
        accounts_url = resolve_accounts_url(self._client.region, getattr(self._client, "accounts_url", None))
        params: list[tuple[str, str]] = [
            ("client_id", app_id),
            ("response_type", "code"),
            ("redirect_uri", redirect_uri),
        ]
        if scope is not None:
            if isinstance(scope, (list, tuple)):
                scope = " ".join(scope)
            params.append(("scope", scope))
        if state is not None:
            params.append(("state", state))
        if prompt is not None:
            params.append(("prompt", prompt))
        query = urlencode(params)
        return f"{accounts_url}{API_PREFIX}/{AUTHORIZE_PATH}?{query}"

    async def exchange_code(self, code: str, redirect_uri: str | None = None) -> NestedDict:
        r"""
        用授权码换取 `user_access_token`。

        授权回调拿到的 `code` 调用本方法换取用户访问凭证。应用凭据在请求体中传递，
        因此该请求不携带 Bearer（`token_type=None`），且响应为非信封格式
        （`expect_envelope=False`），直接返回包含 `access_token`、`refresh_token`、
        `expires_in` 等字段的结果。

        Args:
            code: 授权回调中获得的登录预授权码。
            redirect_uri: 回调地址，需与换取授权码时使用的一致。默认不传。

        Returns:
            包含 `access_token`、`refresh_token`、`expires_in`、`refresh_token_expires_in`、
            `scope` 等字段的结果。

        Raises:
            ValueError: 当客户端未配置 [feishu.auth.credentials.InternalCredential][] 时抛出。
            FeishuAuthError: 当授权码无效或已被使用等鉴权类错误时抛出，
                参见 [feishu.errors.FeishuAuthError][]。
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取用户访问凭证](https://open.feishu.cn/document/server-docs/authentication-management/access-token/get-user-access-token)

        Examples:
            >>> redirect_uri = "https://app.example.com/cb"
            >>> resp = await client.oauth.exchange_code("the-code", redirect_uri=redirect_uri)  # doctest: +SKIP
            >>> resp["access_token"], resp["refresh_token"], resp["expires_in"]  # doctest: +SKIP
            ('u-acc', 'u-ref', 7200)
        """
        credential = self._internal_credential()
        body = {
            "grant_type": "authorization_code",
            "client_id": credential.app_id,
            "client_secret": credential.app_secret,
            "code": code,
        }
        if redirect_uri is not None:
            body["redirect_uri"] = redirect_uri
        return await self._client.request("POST", OAUTH_TOKEN_PATH, json=body, token_type=None, expect_envelope=False)

    async def refresh(self, refresh_token: str) -> NestedDict:
        r"""
        刷新 `user_access_token`。

        飞书的刷新令牌为一次性且会轮换：响应中会返回一个全新的 `refresh_token`，
        调用方必须持久化新令牌并丢弃旧令牌，否则后续刷新将失败。各项有效期
        （`expires_in`、`refresh_token_expires_in`）均来自响应，不应硬编码。
        与 [feishu.auth.oauth.OAuthNamespace.exchange_code][] 一致，凭据在请求体中传递，
        请求不携带 Bearer 且响应为非信封格式。

        Args:
            refresh_token: 当前有效的刷新令牌。

        Returns:
            包含新的 `access_token`、轮换后的 `refresh_token` 及各项有效期的结果。

        Raises:
            ValueError: 当客户端未配置 [feishu.auth.credentials.InternalCredential][] 时抛出。
            FeishuAuthError: 当刷新令牌无效或已过期等鉴权类错误时抛出，
                参见 [feishu.errors.FeishuAuthError][]。
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [刷新用户访问凭证](https://open.feishu.cn/document/server-docs/authentication-management/access-token/refresh-user-access-token)

        Examples:
            >>> resp = await client.oauth.refresh("u-ref-OLD")  # doctest: +SKIP
            >>> resp["access_token"], resp["refresh_token"]  # doctest: +SKIP
            ('u-acc2', 'u-ref-NEW')
        """
        credential = self._internal_credential()
        body = {
            "grant_type": "refresh_token",
            "client_id": credential.app_id,
            "client_secret": credential.app_secret,
            "refresh_token": refresh_token,
        }
        return await self._client.request("POST", OAUTH_TOKEN_PATH, json=body, token_type=None, expect_envelope=False)

    async def user_info(self, user_access_token: str) -> NestedDict:
        r"""
        使用用户访问凭证读取已登录用户的资料。

        该接口为信封格式（`{code, msg, data}`），用户凭证以 Bearer 形式发送。
        返回 `data` 数据体，并通过属性 `raw_envelope` 暴露原始信封（与 IM 命名空间一致）。
        敏感字段（`email`、`enterprise_email`、`mobile`、`user_id`）仅在对应的用户权限
        被申请且已授权时才会出现。

        Args:
            user_access_token: 用户访问凭证，由
                [feishu.auth.oauth.OAuthNamespace.exchange_code][] 换取。

        Returns:
            用户资料数据体，包含 `open_id`、`union_id`、`name` 等字段；
            原始信封可通过 `raw_envelope` 属性访问。

        Raises:
            feishu.errors.FeishuError: 请求失败或返回错误码时抛出。

        飞书文档:
            [获取登录用户信息](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/authentication-management/login-state-management/get-user-info)

        Examples:
            >>> data = await client.oauth.user_info("u-acc-token")  # doctest: +SKIP
            >>> data["open_id"], data["name"]  # doctest: +SKIP
            ('ou_123', 'Ada')
            >>> data.raw_envelope["code"]  # doctest: +SKIP
            0
        """
        return await self._request_data("GET", USER_INFO_PATH, token=user_access_token)

    def _internal_credential(self) -> InternalCredential:
        credential = self._client._credential
        if not isinstance(credential, InternalCredential):
            raise ValueError(_CREDENTIAL_ERROR)
        return credential
