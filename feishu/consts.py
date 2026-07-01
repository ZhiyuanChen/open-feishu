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

FEISHU_BASE_URL = "https://open.feishu.cn"
LARK_BASE_URL = "https://open.larksuite.com"
REGION_BASE_URLS = {"feishu": FEISHU_BASE_URL, "lark": LARK_BASE_URL}

API_PREFIX = "/open-apis"
DEFAULT_TIMEOUT = 30.0
TOKEN_REFRESH_OFFSET = 1800
# Floor for an effective token TTL after subtracting the refresh offset. A short-lived
# token (expire <= refresh_offset) would otherwise cache with expires_at <= now and be
# re-fetched on the very next read (stampede); cache it briefly instead.
MIN_TOKEN_TTL = 10
MAX_PAGE_SIZE = 50


def resolve_base_url(region: str | None, base_url: str | None) -> str:
    r"""
    解析开放平台 API 的基础地址。

    显式传入的 `base_url` 优先级最高，会去除末尾斜杠后直接返回；否则按 `region`
    选择内置地址（`feishu` 对应飞书国内站，`lark` 对应国际站 Lark）。

    Args:
        region: 区域标识，`feishu` 或 `lark`；为 `None` 时默认 `feishu`。
        base_url: 自定义基础地址，传入时优先于 `region`。

    Returns:
        去除末尾斜杠的基础地址。

    Raises:
        ValueError: 当未提供 `base_url` 且 `region` 不在内置区域列表中时抛出。

    Examples:
        >>> resolve_base_url("feishu", None)
        'https://open.feishu.cn'
        >>> resolve_base_url("lark", None)
        'https://open.larksuite.com'
        >>> resolve_base_url(None, "https://example.com/")
        'https://example.com'
    """
    if base_url is not None:
        return base_url.rstrip("/")
    region = region or "feishu"
    if region not in REGION_BASE_URLS:
        raise ValueError(f"unknown region {region!r}; expected one of {sorted(REGION_BASE_URLS)}")
    return REGION_BASE_URLS[region]


# Accounts hosts for the user-OAuth consent URL.
FEISHU_ACCOUNTS_URL = "https://accounts.feishu.cn"
LARK_ACCOUNTS_URL = "https://accounts.larksuite.com"
REGION_ACCOUNTS_URLS = {"feishu": FEISHU_ACCOUNTS_URL, "lark": LARK_ACCOUNTS_URL}

AUTHORIZE_PATH = "authen/v1/authorize"
OAUTH_TOKEN_PATH = "authen/v2/oauth/token"
USER_INFO_PATH = "authen/v1/user_info"


def resolve_accounts_url(region: str | None, accounts_url: str | None = None) -> str:
    r"""
    解析用户 OAuth 授权页所在的账号中心地址。

    显式传入的 `accounts_url` 优先级最高，会去除末尾斜杠后直接返回；否则按
    `region` 选择内置地址。该地址用于拼接用户登录授权（consent）链接，与
    [feishu.consts.resolve_base_url][] 返回的 API 基础地址不同。

    Args:
        region: 区域标识，`feishu` 或 `lark`；为 `None` 时默认 `feishu`。
        accounts_url: 自定义账号中心地址，传入时优先于 `region`。

    Returns:
        去除末尾斜杠的账号中心地址。

    Raises:
        ValueError: 当未提供 `accounts_url` 且 `region` 不在内置区域列表中时抛出。

    飞书文档:
        [获取登录预授权码](https://open.feishu.cn/document/server-docs/authentication-management/login-state-management/obtain-code)

    Examples:
        >>> resolve_accounts_url("feishu", None)
        'https://accounts.feishu.cn'
        >>> resolve_accounts_url("feishu", "https://accounts.internal.example.com/")
        'https://accounts.internal.example.com'
    """
    if accounts_url is not None:
        return accounts_url.rstrip("/")
    region = region or "feishu"
    if region not in REGION_ACCOUNTS_URLS:
        raise ValueError(f"unknown region {region!r}; expected one of {sorted(REGION_ACCOUNTS_URLS)}")
    return REGION_ACCOUNTS_URLS[region]
