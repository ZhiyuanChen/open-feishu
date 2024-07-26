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

# noqa: E302

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, overload

if TYPE_CHECKING:
    from openai import Stream

@overload
def send_message(
    message: str,
    receive_id: str | None = None,
    receive_id_type: str = "open_id",
    uuid: str = "",
    **kwargs,
):
    r"""
    发送消息

    See Also:
        [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)

    Args:
        message: 消息内容
        receive_id: 接收者 ID
        receive_id_type: 接收者 ID 类型。默认为 `open_id`。
        uuid: 消息唯一标识，用于消息去重
    """

@overload
def send_message(
    message: Mapping,
    receive_id: str | None = None,
    receive_id_type: str = "open_id",
    uuid: str = "",
    **kwargs,
):
    r"""
    发送消息

    See Also:
        [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)

    Args:
        message: 消息内容
        receive_id: 接收者 ID
        receive_id_type: 接收者 ID 类型。默认为 `open_id`。
        uuid: 消息唯一标识，用于消息去重
    """

@overload
def send_message(
    message: Stream,
    receive_id: str | None = None,
    receive_id_type: str = "open_id",
    uuid: str = "",
    **kwargs,
):
    r"""
    发送消息

    See Also:
        [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)

    Args:
        message: 消息内容
        receive_id: 接收者 ID
        receive_id_type: 接收者 ID 类型。默认为 `open_id`。
        uuid: 消息唯一标识，用于消息去重
    """

@overload
def send_message(
    message: str,
    message_id: str,
    **kwargs,
):
    r"""
    发送消息

    See Also:
        [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)

    Args:
        message: 消息内容
        receive_id: 接收者 ID
        receive_id_type: 接收者 ID 类型。默认为 `open_id`。
        uuid: 消息唯一标识，用于消息去重
    """

@overload
def send_message(
    message: Mapping,
    message_id: str,
    **kwargs,
):
    r"""
    发送消息

    See Also:
        [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)

    Args:
        message: 消息内容
        receive_id: 接收者 ID
        receive_id_type: 接收者 ID 类型。默认为 `open_id`。
        uuid: 消息唯一标识，用于消息去重
    """

@overload
def send_message(
    message: Stream,
    message_id: str,
    **kwargs,
):
    r"""
    发送消息

    See Also:
        [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)

    Args:
        message: 消息内容
        receive_id: 接收者 ID
        receive_id_type: 接收者 ID 类型。默认为 `open_id`。
        uuid: 消息唯一标识，用于消息去重
    """
