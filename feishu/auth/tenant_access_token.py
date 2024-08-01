# open-feishu
# Copyright (C) 2024-Present  Zhiyuan Chen

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import annotations

from typing import overload

import httpx
from chanfig import FlatDict

from feishu import variables

from ..exceptions import FeishuException


@overload
def get_tenant_access_token(app_id: str, app_secret: str, timeout: int = 120) -> FlatDict:
    r"""
    获取租户访问凭证

    Args:
        app_id: 应用唯一标识（以`cli_`开头）
        app_secret: 应用秘钥
        timeout: 超时时间。默认为 120 秒。

    Returns:
        租户访问凭证
    """


@overload
def get_tenant_access_token(app_access_token: str, tenant_key: str, timeout: int = 120) -> FlatDict:
    r"""
    获取租户访问凭证

    Args:
        app_access_token: 应用访问凭证（以`a-`开头）
        tenant_key: 租户在飞书上的唯一标识
        timeout: 超时时间。默认为 120 秒。

    Returns:
        租户访问凭证
    """


def get_tenant_access_token(input_a: str, input_b: str, timeout: int = 120) -> FlatDict:  # type: ignore[misc]
    r"""
    获取租户访问凭证

    这是一个通用函数，根据传入的参数自动判断是自建应用还是商店应用，并调用相应的函数。

    Args:
        input_a: 应用唯一标识（对于自建应用，以`cli_`开头）或者应用访问凭证（对于商店应用，以`a-`开头）
        input_b: 应用秘钥（对于自建应用）或者租户在飞书上的唯一标识（对于商店应用）
        timeout: 超时时间。默认为 120 秒。

    Returns:
        租户访问凭证

    飞书文档:
        [通用参数介绍](https://open.feishu.cn/document/server-docs/api-call-guide/terminology)

    | 功能            | 实现函数                                                              |
    |---------------|-------------------------------------------------------------------|
    | 获取自建应用的租户访问凭证 | [feishu.auth.app_access_token.get_tenant_access_token_internal][] |
    | 获取商店应用的租户访问凭证 | [feishu.auth.app_access_token.get_tenant_access_token_store][]    |

    Examples:
        >>> get_tenant_access_token("cli_slkdjalasdkjasd", "dskLLdkasdjlasdKK")  # doctest:+SKIP
        FlatDict(
          ('code'): 0
          ('msg'): 'ok'
          ('tenant_access_token'): 't-g1044ghJRUIJJ5ZPPZMOHKWZISL33E4QSS3abcef'
          ('expire'): 7200
        )
        >>> get_tenant_access_token("a-32bd8551db2f081cbfd26293f27516390b9feb04", "73658811060f175d")  # doctest:+SKIP
        FlatDict(
          ('code'): 0
          ('msg'): 'success'
          ('tenant_access_token'): 't-caecc734c2e3328a62489fe0648c4b98779515d3'
          ('expire'): 7200
        )
    """
    if input_a.startswith("cli_"):
        return get_tenant_access_token_internal(input_a, input_b, timeout)
    if input_a.startswith("a-"):
        return get_tenant_access_token_store(input_a, input_b, timeout)
    raise ValueError("Invalid Request Parameters")


def get_tenant_access_token_internal(app_id: str, app_secret: str, timeout: int = 120) -> FlatDict:
    r"""
    自建应用获取租户访问凭证

    Args:
        app_id: 应用唯一标识（以`cli_`开头）
        app_secret: 应用秘钥
        timeout: 超时时间。默认为 120 秒。

    Returns:
        租户访问凭证

    飞书文档:
        [通用参数介绍](https://open.feishu.cn/document/server-docs/api-call-guide/terminology)

        [自建应用获取租户访问凭证](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal)

    Examples:
        >>> get_tenant_access_token_internal("cli_slkdjalasdkjasd", "dskLLdkasdjlasdKK")  # doctest:+SKIP
        FlatDict(
          ('code'): 0
          ('msg'): 'ok'
          ('tenant_access_token'): 't-g1044ghJRUIJJ5ZPPZMOHKWZISL33E4QSS3abcef'
          ('expire'): 7200
        )
    """
    url = variables.BASE_URL + "auth/v3/tenant_access_token/internal"
    data = {"app_id": app_id, "app_secret": app_secret}
    response = httpx.post(url, json=data, timeout=timeout)
    if response.status_code != 200:
        raise FeishuException(response.status_code, response.text)
    ret = response.json()
    if ret.get("code") != 0:
        raise FeishuException(ret.get("code"), ret.get("msg"))
    return FlatDict(ret)


def get_tenant_access_token_store(app_access_token: str, tenant_key: str, timeout: int = 120) -> FlatDict:
    r"""
    商店应用获取租户访问凭证

    Args:
        app_access_token: 应用访问凭证（以`a-`开头）
        tenant_key: 租户在飞书上的唯一标识
        timeout: 超时时间。默认为 120 秒。

    Returns:
        租户访问凭证

    飞书文档:
        [通用参数介绍](https://open.feishu.cn/document/server-docs/api-call-guide/terminology)

        [商店应用获取租户访问凭证](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token)

    Examples:
        >>> get_tenant_access_token_store("a-32bd8551db2f081cbfd26293f27516390b9feb04", "73658811060f175d")  # doctest:+SKIP
        FlatDict(
          ('code'): 0
          ('msg'): 'success'
          ('tenant_access_token'): 't-caecc734c2e3328a62489fe0648c4b98779515d3'
          ('expire'): 7200
        )
    """  # noqa: E501
    url = variables.BASE_URL + "auth/v3/tenant_access_token"
    data = {"app_access_token": app_access_token, "tenant_key": tenant_key}
    response = httpx.post(url, json=data, timeout=timeout)
    if response.status_code != 200:
        raise FeishuException(response.status_code, response.text)
    ret = response.json()
    if ret.get("code") != 0:
        raise FeishuException(ret.get("code"), ret.get("msg"))
    return FlatDict(ret)
