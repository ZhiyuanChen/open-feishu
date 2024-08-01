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

import requests
from chanfig import FlatDict

from feishu import variables

from ..exceptions import FeishuException


@overload
def get_app_access_token(app_id: str, app_secret: str, timeout: int = 120) -> FlatDict:
    r"""
    获取应用访问凭证

    Args:
        app_id: 应用唯一标识（以`cli_`开头）
        app_secret: 应用秘钥
        timeout: 超时时间。默认为 120 秒。

    Returns:
        应用访问凭证
    """


@overload
def get_app_access_token(app_id: str, app_secret: str, app_ticket: str, timeout: int = 120) -> FlatDict:
    r"""
    获取应用访问凭证

    Args:
        app_id: 应用唯一标识（以`cli_`开头）
        app_secret: 应用秘钥
        app_ticket: 平台定时推送给应用的临时凭证
        timeout: 超时时间。默认为 120 秒。

    Returns:
        应用访问凭证
    """


def get_app_access_token(  # type: ignore[misc]
    app_id: str,
    app_secret: str,
    app_ticket: str | None = None,
    timeout: int = 120,
) -> FlatDict:
    r"""
    获取应用访问凭证

    这是一个通用函数，根据传入的参数自动判断是自建应用还是商店应用，并调用相应的函数。

    Args:
        app_id: 应用唯一标识（以`cli_`开头）
        app_secret: 应用秘钥
        app_ticket: 平台定时推送给应用的临时凭证
        timeout: 超时时间。默认为 120 秒。

    Returns:
        应用访问凭证

    飞书文档:
        [通用参数介绍](https://open.feishu.cn/document/server-docs/api-call-guide/terminology)

    | 功能            | 实现函数                                                           |
    |---------------|----------------------------------------------------------------|
    | 获取自建应用的应用访问凭证 | [feishu.auth.app_access_token.get_app_access_token_internal][] |
    | 获取商店应用的应用访问凭证 | [feishu.auth.app_access_token.get_app_access_token_store][]    |

    Examples:
        >>> get_app_access_token("cli_slkdjalasdkjasd", "dskLLdkasdjlasdKK")  # doctest:+SKIP
        FlatDict(
          ('app_access_token'): 't-g1044ghJRUIJJ5ZPPZMOHKWZISL33E4QSS3abcef'
          ('code'): 0
          ('expire'): 7200
          ('msg'): 'ok'
          ('tenant_access_token'): 't-g1044ghJRUIJJ5ZPPZMOHKWZISL33E4QSS3abcef'
        )
        >>> get_app_access_token("cli_slkdjalasdkjasd", "dskLLdkasdjlasdKK", "dskLLdkasd")  # doctest:+SKIP
        FlatDict(
          ('code'): 0
          ('msg'): 'success'
          ('app_access_token'): 'a-6U1SbDiM6XIH2DcTCPyeub'
          ('expire'): 7200
        )
    """
    if app_ticket is None:
        return get_app_access_token_internal(app_id, app_secret, timeout)
    if isinstance(app_ticket, int):
        return get_app_access_token_internal(app_id, app_secret, app_ticket)
    return get_app_access_token_store(app_id, app_secret, app_ticket, timeout)


def get_app_access_token_internal(app_id: str, app_secret: str, timeout: int = 120) -> FlatDict:
    r"""
    自建应用获取应用访问凭证

    Args:
        app_id: 应用唯一标识（以`cli_`开头）
        app_secret: 应用秘钥
        timeout: 超时时间。默认为 120 秒。

    Returns:
        应用访问凭证

    飞书文档:
        [通用参数介绍](https://open.feishu.cn/document/server-docs/api-call-guide/terminology)

        [自建应用获取应用访问凭证](https://open.feishu.cn/document/server-docs/authentication-management/access-token/app_access_token_internal)

    Examples:
        >>> get_app_access_token_internal("cli_slkdjalasdkjasd", "dskLLdkasdjlasdKK")  # doctest:+SKIP
        FlatDict(
          ('app_access_token'): 't-g1044ghJRUIJJ5ZPPZMOHKWZISL33E4QSS3abcef'
          ('code'): 0
          ('expire'): 7200
          ('msg'): 'ok'
          ('tenant_access_token'): 't-g1044ghJRUIJJ5ZPPZMOHKWZISL33E4QSS3abcef'
        )
    """
    url = variables.BASE_URL + "auth/v3/app_access_token/internal"
    data = {"app_id": app_id, "app_secret": app_secret}
    response = requests.post(url, json=data, timeout=timeout)
    if response.status_code != 200:
        raise FeishuException(response.status_code, response.text)
    ret = response.json()
    if ret.get("code") != 0:
        raise FeishuException(ret.get("code"), ret.get("msg"))
    return FlatDict(ret)


def get_app_access_token_store(app_id: str, app_secret: str, app_ticket: str, timeout: int = 120) -> FlatDict:
    r"""
    商店应用获取应用访问凭证

    Args:
        app_id: 应用唯一标识（以`cli_`开头）
        app_secret: 应用秘钥
        app_ticket: 平台定时推送给应用的临时凭证
        timeout: 超时时间。默认为 120 秒。

    Returns:
        应用访问凭证

    飞书文档:
        [通用参数介绍](https://open.feishu.cn/document/server-docs/api-call-guide/terminology)

        [商店应用获取应用访问凭证](https://open.feishu.cn/document/server-docs/authentication-management/access-token/app_access_token)

    Examples:
        >>> get_app_access_token_store("cli_slkdjalasdkjasd", "dskLLdkasdjlasdKK", "dskLLdkasd")  # doctest:+SKIP
        FlatDict(
          ('code'): 0
          ('msg'): 'success'
          ('app_access_token'): 'a-6U1SbDiM6XIH2DcTCPyeub'
          ('expire'): 7200
        )
    """  # noqa: E501
    url = variables.BASE_URL + "auth/v3/app_access_token"
    data = {"app_id": app_id, "app_secret": app_secret, "app_ticket": app_ticket}
    response = requests.post(url, json=data, timeout=timeout)
    if response.status_code != 200:
        raise FeishuException(response.status_code, response.text)
    ret = response.json()
    if ret.get("code") != 0:
        raise FeishuException(ret.get("code"), ret.get("msg"))
    return FlatDict(ret)
