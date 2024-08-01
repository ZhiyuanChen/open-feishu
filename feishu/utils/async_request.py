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

# pylint: disable=unused-argument, too-many-arguments

from __future__ import annotations

import asyncio
from typing import Callable, Dict

import httpx
from chanfig import NestedDict

from feishu import FeishuException, variables

from .decorators import authorize
from .request import get_tenant_access_token


@authorize
async def async_request(
    method: str | Callable,
    dest: str,
    data: Dict | None = None,
    params: Dict | None = None,
    headers: Dict | None = None,
    timeout: int = 120,
    token: str | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
    max_retries: int | None = None,
    backoff_factor: float | None = None,
    retry_codes: tuple[int, ...] = (500, 502, 503, 504),
    **kwargs,
) -> NestedDict:
    r"""
    向指定目的地发送请求。

    Args:
        method: 请求方法。可以是字符串（'GET'、'POST'、'PUT'、'PATCH'、'DELETE'）或者函数。
        dest: 目的地统一资源定位符。这个地址应该是相对于飞书 API 的。
        data: 请求数据。默认为 `None`。
        params: 请求参数。默认为 `None`。
        headers: 请求头。默认为 `{}`。
        timeout: 超时时间。默认为 120 秒。
        token: 访问凭证。如果为 `None`，则会自动获取。
            如果 token 为 `None`，则 app_id 和 app_secret 必须不为 `None`。
        app_id: 应用唯一标识（以`cli_`开头）。默认为 `None`。
        app_secret: 应用秘钥。默认为 `None`。
        max_retries: 最大重试次数。默认为 `variables.MAX_RETRIES`。
        backoff_factor: 重试间隔因子。默认为 `variables.BACKOFF_FACTOR`。
        retry_codes: 需要重试的状态码。默认为 `(500, 502, 503, 504)`
        kwargs: 其他参数。

    Returns:
        请求结果

    Raises:
        ValueError: 如果 token, app_id, 和 app_secret 都为 `None`。
        FeishuException: 飞书 API 返回的错误。
    """
    if max_retries is None:
        max_retries = variables.MAX_RETRIES
    if backoff_factor is None:
        backoff_factor = variables.BACKOFF_FACTOR
    if dest.startswith("/"):
        dest = dest[1:]
    url = variables.BASE_URL + dest
    if headers is None:
        headers = {}
    if token is None:
        if app_id is None or app_secret is None:
            raise ValueError("token, app_id, and app_secret cannot all be None.")
        token = get_tenant_access_token(app_id, app_secret, timeout)
    headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/json"

    attempt = 0
    while attempt <= max_retries:
        try:
            if callable(method):
                response = await method(url, headers=headers, json=data, params=params, timeout=timeout, **kwargs)
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.request(
                        method, url, headers=headers, json=data, params=params, timeout=timeout, **kwargs
                    )
        except Exception:
            await asyncio.sleep(backoff_factor**attempt)
            attempt += 1
            continue
        ret = response.json()
        if ret.get("code") == 0:
            return NestedDict(ret)
        if response.status_code in retry_codes:
            await asyncio.sleep(backoff_factor**attempt)
            attempt += 1
        raise FeishuException(ret.get("code"), ret.get("msg"))

    raise FeishuException(ret.get("code"), f"Request failed after {max_retries} retries.")


async def async_post(
    dest: str,
    data: Dict | None = None,
    params: Dict | None = None,
    headers: Dict | None = None,
    timeout: int = 120,
    token: str | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
    **kwargs,
) -> NestedDict:
    r"""
    向指定目的地发送 POST 请求。

    Args:
        dest: 目的地统一资源定位符。这个地址应该是相对于飞书 API 的。
        headers: 请求头。默认为 `{}`。
        params: 请求参数。默认为 `{}`。
        timeout: 超时时间。默认为 120 秒。
        token: 访问凭证。如果为 `None`，则会自动获取。
            如果 token 为 `None`，则 app_id 和 app_secret 必须不为 `None`。
        app_id: 应用唯一标识（以`cli_`开头）。默认为 `None`。
        app_secret: 应用秘钥。默认为 `None`。

    Returns:
        请求结果

    Raises:
        ValueError: 如果 token, app_id, 和 app_secret 都为 `None`。

    Examples:
        >>> data = {
        ...     "receive_id_type": "open_id",
        ...     "receive_id": "ou_7d8a6e6df7621556ce0d21922b676706ccs",
        ...     "msg_type": "text",
        ...     "content": "{\"text\":\"test content\"}",
        ...     "uuid": "a0d69e20-1dd1-458b-k525-dfeca4015204"
        ... }
        >>> loop = asyncio.get_event_loop()
        >>> loop.run_until_complete(
        ...     async_post(
        ...         'im/v1/messages',
        ...         data=data,
        ...         app_id='cli_slkdjalasdkjasd',
        ...         app_secret='dskLLdkasdjlasdKK'
        ...     )
        ... )  # doctest:+SKIP
        NestedDict(
          ('code'): 0
          ('msg'): 'success'
          ('data'): NestedDict(
            ('message_id'): 'om_dc13264520392913993dd051dba21dcf'
            ('root_id'): 'om_40eb06e7b84dc71c03e009ad3c754195'
            ('parent_id'): 'om_d4be107c616aed9c1da8ed8068570a9f'
            ('thread_id'): 'omt_d4be107c616a'
            ('msg_type'): 'card'
            ('create_time'): '1635675360'
            ('update_time'): '1635675360'
            ('deleted'): False
            ('updated'): False
            ('chat_id'): 'oc_5ad11d72b830411d72b836c20'
            ('sender'): NestedDict(
              ('id'): 'cli_9f427eec54ae901b'
              ('id_type'): 'app_id'
              ('sender_type'): 'app'
              ('tenant_key'): '736588c9260f175e'
            )
            ('body'): NestedDict(
              ('content'): '{"text":"@_user_1 test content"}'
            )
            ('mentions'): [NestedDict(
              ('key'): '@_user_1'
              ('id'): 'ou_155184d1e73cbfb8973e5a9e698e74f2'
              ('id_type'): 'open_id'
              ('name'): 'Chang'
              ('tenant_key'): '736588c9260f175e'
            )]
            ('upper_message_id'): 'om_40eb06e7b84dc71c03e009ad3c754195'
          )
        )
    """
    return await async_request(
        "POST",
        dest,
        data=data,
        params=params,
        headers=headers,
        timeout=timeout,
        token=token,
        app_id=app_id,
        app_secret=app_secret,
        **kwargs,
    )


async def async_get(
    dest: str,
    params: Dict | None = None,
    headers: Dict | None = None,
    timeout: int = 120,
    token: str | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
    **kwargs,
) -> NestedDict:
    r"""
    向指定目的地发送 GET 请求。

    Args:
        dest: 目的地统一资源定位符。这个地址应该是相对于飞书 API 的。
        headers: 请求头。默认为 `{}`。
        params: 请求参数。默认为 `{}`。
        timeout: 超时时间。默认为 120 秒。
        token: 访问凭证。如果为 `None`，则会自动获取。
            如果 token 为 `None`，则 app_id 和 app_secret 必须不为 `None`。
        app_id: 应用唯一标识（以`cli_`开头）。默认为 `None`。
        app_secret: 应用秘钥。默认为 `None`。

    Returns:
        请求结果

    Raises:
        ValueError: 如果 token, app_id, 和 app_secret 都为 `None`。

    Examples:
        >>> loop = asyncio.get_event_loop()
        >>> loop.run_until_complete(
        ...     async_get(
        ...         'im/v1/messages/:message_id',
        ...         app_id='cli_slkdjalasdkjasd',
        ...         app_secret='dskLLdkasdjlasdKK'
        ...     )
        ... )  # doctest:+SKIP
        NestedDict(
          ('code'): 0
          ('msg'): 'success'
          ('data'): NestedDict(
            ('items'): [NestedDict(
              ('message_id'): 'om_dc13264520392913993dd051dba21dcf'
              ('root_id'): 'om_40eb06e7b84dc71c03e009ad3c754195'
              ('parent_id'): 'om_d4be107c616aed9c1da8ed8068570a9f'
              ('thread_id'): 'omt_d4be107c616a'
              ('msg_type'): 'card'
              ('create_time'): '1635675360'
              ('update_time'): '1635675360'
              ('deleted'): False
              ('updated'): False
              ('chat_id'): 'oc_5ad11d72b830411d72b836c20'
              ('sender'): NestedDict(
                ('id'): 'cli_9f427eec54ae901b'
                ('id_type'): 'app_id'
                ('sender_type'): 'app'
                ('tenant_key'): '736588c9260f175e'
              )
              ('body'): NestedDict(
                ('content'): '{"text":"@_user_1 test content"}'
              )
              ('mentions'): [NestedDict(
                ('key'): '@_user_1'
                ('id'): 'ou_155184d1e73cbfb8973e5a9e698e74f2'
                ('id_type'): 'open_id'
                ('name'): 'Chang'
                ('tenant_key'): '736588c9260f175e'
              )]
            ('upper_message_id'): 'om_40eb06e7b84dc71c03e009ad3c754195'
            )]
          )
        )
    """
    return await async_request(
        "GET",
        dest,
        headers=headers,
        params=params,
        timeout=timeout,
        token=token,
        app_id=app_id,
        app_secret=app_secret,
        **kwargs,
    )


async def async_put(
    dest: str,
    data: Dict | None = None,
    params: Dict | None = None,
    headers: Dict | None = None,
    timeout: int = 120,
    token: str | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
    **kwargs,
) -> NestedDict:
    r"""
    向指定目的地发送 PUT 请求。

    Args:
        dest: 目的地统一资源定位符。这个地址应该是相对于飞书 API 的。
        headers: 请求头。默认为 `{}`。
        params: 请求参数。默认为 `{}`。
        timeout: 超时时间。默认为 120 秒。
        token: 访问凭证。如果为 `None`，则会自动获取。
            如果 token 为 `None`，则 app_id 和 app_secret 必须不为 `None`。
        app_id: 应用唯一标识（以`cli_`开头）。默认为 `None`。
        app_secret: 应用秘钥。默认为 `None`。

    Returns:
        请求结果

    Raises:
        ValueError: 如果 token, app_id, 和 app_secret 都为 `None`。

    Examples:
        >>> data = {"msg_type": "text", "content": "{\"text\":\"test content\"}"}
        >>> loop = asyncio.get_event_loop()
        >>> loop.run_until_complete(
        ...     async_put(
        ...         'im/v1/messages/:message_id',
        ...         data=data,
        ...         app_id='cli_slkdjalasdkjasd',
        ...         app_secret='dskLLdkasdjlasdKK'
        ...     )
        ... )  # doctest:+SKIP
        NestedDict(
          ('code'): 0
          ('msg'): 'success'
          ('data'): NestedDict(
            ('message_id'): 'om_dc13264520392913993dd051dba21dcf'
            ('root_id'): 'om_40eb06e7b84dc71c03e009ad3c754195'
            ('parent_id'): 'om_d4be107c616aed9c1da8ed8068570a9f'
            ('thread_id'): 'omt_d4be107c616a'
            ('msg_type'): 'card'
            ('create_time'): '1635675360'
            ('update_time'): '1635675420'
            ('deleted'): False
            ('updated'): True
            ('chat_id'): 'oc_5ad11d72b830411d72b836c20'
            ('sender'): NestedDict(
              ('id'): 'cli_9f427eec54ae901b'
              ('id_type'): 'app_id'
              ('sender_type'): 'app'
              ('tenant_key'): '736588c9260f175e'
            )
            ('body'): NestedDict(
              ('content'): '{"text":"@_user_1 test content"}'
            )
            ('mentions'): [NestedDict(
              ('key'): '@_user_1'
              ('id'): 'ou_155184d1e73cbfb8973e5a9e698e74f2'
              ('id_type'): 'open_id'
              ('name'): 'Chang'
              ('tenant_key'): '736588c9260f175e'
            )]
            ('upper_message_id'): 'om_40eb06e7b84dc71c03e00ida3c754892'
          )
        )
    """
    return await async_request(
        "PUT",
        dest,
        data=data,
        params=params,
        headers=headers,
        timeout=timeout,
        token=token,
        app_id=app_id,
        app_secret=app_secret,
        **kwargs,
    )


async def async_patch(
    dest: str,
    data: Dict | None = None,
    params: Dict | None = None,
    headers: Dict | None = None,
    timeout: int = 120,
    token: str | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
    **kwargs,
) -> NestedDict:
    r"""
    向指定目的地发送 PUT 请求。

    Args:
        dest: 目的地统一资源定位符。这个地址应该是相对于飞书 API 的。
        headers: 请求头。默认为 `{}`。
        params: 请求参数。默认为 `{}`。
        timeout: 超时时间。默认为 120 秒。
        token: 访问凭证。如果为 `None`，则会自动获取。
            如果 token 为 `None`，则 app_id 和 app_secret 必须不为 `None`。
        app_id: 应用唯一标识（以`cli_`开头）。默认为 `None`。
        app_secret: 应用秘钥。默认为 `None`。

    Returns:
        请求结果

    Raises:
        ValueError: 如果 token, app_id, 和 app_secret 都为 `None`。

    Examples:
        >>> data = {"content": "{\"text\":\"test content\"}"}
        >>> loop = asyncio.get_event_loop()
        >>> loop.run_until_complete(
        ...     async_patch(
        ...         'im/v1/messages/:message_id',
        ...         data=data,
        ...         app_id='cli_slkdjalasdkjasd',
        ...         app_secret='dskLLdkasdjlasdKK'
        ...     )
        ... )  # doctest:+SKIP
        NestedDict(
          ('code'): 0
          ('msg'): 'success'
          ('data'): NestedDict()
        )
    """
    return await async_request(
        "PATCH",
        dest,
        data=data,
        params=params,
        headers=headers,
        timeout=timeout,
        token=token,
        app_id=app_id,
        app_secret=app_secret,
        **kwargs,
    )


async def async_delete(
    dest: str,
    data: Dict | None = None,
    params: Dict | None = None,
    headers: Dict | None = None,
    timeout: int = 120,
    token: str | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
    **kwargs,
) -> NestedDict:
    r"""
    向指定目的地发送 DELETE 请求。

    Args:
        dest: 目的地统一资源定位符。这个地址应该是相对于飞书 API 的。
        headers: 请求头。默认为 `{}`。
        params: 请求参数。默认为 `{}`。
        timeout: 超时时间。默认为 120 秒。
        token: 访问凭证。如果为 `None`，则会自动获取。
            如果 token 为 `None`，则 app_id 和 app_secret 必须不为 `None`。
        app_id: 应用唯一标识（以`cli_`开头）。默认为 `None`。
        app_secret: 应用秘钥。默认为 `None`。

    Returns:
        请求结果

    Raises:
        ValueError: 如果 token, app_id, 和 app_secret 都为 `None`。

    Examples:
        >>> loop = asyncio.get_event_loop()
        >>> loop.run_until_complete(
        ...     async_delete(
        ...         'im/v1/messages/:message_id',
        ...         app_id='cli_slkdjalasdkjasd',
        ...         app_secret='dskLLdkasdjlasdKK'
        ...     )
        ... )  # doctest:+SKIP
        NestedDict(
          ('code'): 0
          ('msg'): 'success'
          ('data'): NestedDict()
        )
    """
    return await async_request(
        "DELETE",
        dest,
        data=data,
        params=params,
        headers=headers,
        timeout=timeout,
        token=token,
        app_id=app_id,
        app_secret=app_secret,
        **kwargs,
    )
