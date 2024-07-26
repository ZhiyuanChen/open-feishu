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

import json
from typing import TYPE_CHECKING, Mapping

from chanfig import NestedDict

from feishu.api.decorators import authorize, pagination
from feishu.api.request import delete, get, patch, post, put

from .utils import get_stream_message, infer_receive_id_type

if TYPE_CHECKING:
    from openai import Stream


@authorize
def send_message(
    message: str | Mapping | Stream,
    receive_id: str | None = None,
    receive_id_type: str | None = None,
    uuid: str = "",
    **kwargs,
):
    r"""
    发送消息

    See Also:
        [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)

        [回复消息](https://open.feishu.cn/document/server-docs/im-v1/message/reply)

    Args:
        message: 消息内容
        receive_id: 接收者 ID
        receive_id_type: 接收者 ID 类型。默认为 `open_id`。
        uuid: 消息唯一标识，用于消息去重
    """
    if isinstance(message, str):
        message = {
            "msg_type": "text",
            "content": json.dumps({"text": message}),
        }
    if receive_id is not None:
        if receive_id.startswith("om_"):
            return reply_message(message, receive_id, **kwargs)
        message["receive_id"] = receive_id
    receive_id = message.get("receive_id")
    if receive_id is None:
        raise ValueError("receive_id is required")
    if receive_id_type is None:
        receive_id_type = infer_receive_id_type(receive_id)
    if uuid:
        message["uuid"] = uuid
    message = post("im/v1/messages", message, {"receive_id_type": receive_id_type}, **kwargs)
    message.data.body.content = NestedDict.from_jsons(message.data.body.content)
    return message


@authorize
def send_message_stream(
    stream: Stream,
    receive_id: str | None = None,
    receive_id_type: str | None = None,
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
    """
    if receive_id_type is None:
        receive_id_type = infer_receive_id_type(receive_id)
    content = ""
    response = send_message(get_stream_message(content, uuid=uuid), **kwargs)
    message_id = response["data"]["message_id"]
    try:
        for chunk in stream:
            content += chunk.choices[0].delta.content
            message = get_stream_message(content, streaming=True, uuid=uuid)
            patch_message(message, message_id, uuid=uuid, **kwargs)
    except Exception as e:
        raise e
    finally:
        message = get_stream_message(content, uuid=uuid)
        patch_message(message, message_id, uuid=uuid, **kwargs)


@authorize
def reply_message(message: str | Mapping, message_id: str, **kwargs):
    r"""
    回复消息

    See Also:
        [回复消息](https://open.feishu.cn/document/server-docs/im-v1/message/reply)

    Args:
        message: 消息内容
        message_id: 消息 ID
    """
    if isinstance(message, str):
        message = {
            "msg_type": "text",
            "content": json.dumps({"text": message}),
        }
    message = post(f"im/v1/messages/{message_id}/reply", message, **kwargs)
    message.data.body.content = NestedDict.from_jsons(message.data.body.content)
    return message


@authorize
def update_message(message: str | Mapping, message_id: str, **kwargs):
    r"""
    编辑消息

    See Also:
        [编辑消息](https://open.feishu.cn/document/server-docs/im-v1/message/update)

    Args:
        message_id: 消息 ID
        message: 消息内容
    """
    if isinstance(message, str):
        message = {
            "msg_type": "text",
            "content": json.dumps({"text": message}),
        }
    message = put(f"im/v1/messages/{message_id}", message, **kwargs)
    message.data.body.content = NestedDict.from_jsons(message.data.body.content)
    return message


@authorize
def recall_message(message_id: str, **kwargs):
    r"""
    撤回消息

    See Also:
        [撤回消息](https://open.feishu.cn/document/server-docs/im-v1/message/recall)

    Args:
        message_id: 消息 ID
    """
    return delete(f"im/v1/messages/{message_id}", **kwargs)


@authorize
def forward_message(message_id: str, receive_id: str, receive_id_type: str | None = None, uuid: str = "", **kwargs):
    r"""
    转发消息

    See Also:
        [转发消息](https://open.feishu.cn/document/server-docs/im-v1/message/forward)

    Args:
        message_id: 消息 ID
        receive_id: 接收者 ID
        receive_id_type: 接收者 ID 类型。默认为 `open_id`。
    """
    if receive_id_type is None:
        receive_id_type = infer_receive_id_type(receive_id)
    message = post(
        f"im/v1/messages/{message_id}/forward",
        {"receive_id": receive_id},
        {"receive_id_type": receive_id_type, "uuid": uuid},
        **kwargs,
    )
    message.data.body.content = NestedDict.from_jsons(message.data.body.content)
    return message


@authorize
def merge_forward(
    message_id_list: list[str],
    receive_id: str,
    receive_id_type: str | None = None,
    uuid: str = "",
    **kwargs,
):
    r"""
    合并转发消息

    See Also:
        [合并转发消息](https://open.feishu.cn/document/server-docs/im-v1/message/merge_forward)

    Args:
        message_id_list: 消息 ID 列表
        receive_id: 接收者 ID
        receive_id_type: 接收者 ID 类型。默认为 `open_id`。
    """
    if receive_id_type is None:
        receive_id_type = infer_receive_id_type(receive_id)
    message = post(
        "im/v1/messages/merge-forward",
        {"message_id_list": message_id_list, "receive_id": receive_id},
        {"receive_id_type": receive_id_type, "uuid": uuid},
        **kwargs,
    )
    message.data.body.content = NestedDict.from_jsons(message.data.body.content)
    return message


@pagination
@authorize
def read_users(message_id: str, user_id_type: str = "open_id", **kwargs):
    r"""
    查询消息已读信息

    See Also:
        [查询消息已读信息](https://open.feishu.cn/document/server-docs/im-v1/message/read_users)

    Args:
        message_id: 消息 ID
        user_id_type: 用户 ID 类型。默认为 `open_id`。可以是 `open_id` 或 "union_id" 或 `user_id`。
    """

    return get(f"im/v1/messages/{message_id}/read_users", {"user_id_type": user_id_type}, **kwargs)


@authorize
def get_message(message_id: str, **kwargs):
    r"""
    获取消息

    See Also:
        [获取指定消息的内容](https://open.feishu.cn/document/server-docs/im-v1/message/get)

    Args:
        message_id: 消息 ID
    """

    message = get(f"im/v1/messages/{message_id}", **kwargs)
    message.data = message.data["items"][0]
    message.data.body.content = NestedDict.from_jsons(message.data.body.content)
    return message


@pagination
@authorize
def get_messages(
    container_id: str,
    container_id_type: str = "chat",
    start_time: int | None = None,
    end_time: int | None = None,
    sort_type: str = "ByCreateTimeAsc",
    page_size: int = 50,
    page_token: str | None = None,
    **kwargs,
):
    r"""
    获取会话历史消息

    See Also:
        [获取会话历史消息](https://open.feishu.cn/document/server-docs/im-v1/message/list)

    Args:
        container_id: 会话 ID
        container_id_type: 会话 ID 类型。默认为 `chat`。可以是 `chat` 或 `group` 或 `meeting`。
        start_time: 开始时间。默认为 `None`。
        end_time: 结束时间。默认为 `None`。
        sort_type: 排序方式。默认为 `ByCreateTimeAsc`。可以是 `ByCreateTimeAsc` 或 `ByCreateTimeDesc`。
        page_size: 每页数量。默认为 50。
        page_token: 分页标识。默认为 `None`。
    """
    message = get(
        "im/v1/messages",
        {
            "container_id": container_id,
            "container_id_type": container_id_type,
            "start_time": start_time,
            "end_time": end_time,
            "sort_type": sort_type,
            "page_size": page_size,
            "page_token": page_token,
        },
        **kwargs,
    )
    message.data["items"] = [NestedDict.from_jsons(i) for i in message.data["items"]]
    return message


@authorize
def get_message_resources(message_id: str, file_key: str, type: str, **kwargs):
    r"""
    获取消息中的资源文件

    See Also:
        [获取消息中的资源文件](https://open.feishu.cn/document/server-docs/im-v1/message/get-2)

    Args:
        message_id: 消息 ID
    """

    return get(f"im/v1/messages/{message_id}/resources/{file_key}?type={type}", **kwargs)


@authorize
def push_follow_up(message_id: str, follow_ups: str | Mapping, **kwargs):
    r"""
    添加跟随气泡

    See Also:
        [添加跟随气泡](https://open.feishu.cn/document/server-docs/im-v1/message/push_follow_up)

    Args:
        message_id: 消息 ID
        follow_ups: 跟随气泡内容
    """

    if isinstance(follow_ups, str):
        follow_ups = {
            "follow_ups": [
                {
                    "content": follow_ups,
                }
            ]
        }
    return post(f"im/v1/messages/{message_id}/push_follow_up", follow_ups, **kwargs)


@authorize
def patch_message(message: str | Mapping, message_id: str, **kwargs):
    r"""
    更新应用发送的消息卡片

    See Also:
        [更新应用发送的消息卡片](https://open.feishu.cn/document/server-docs/im-v1/message-card/patch)

    Args:
        message_id: 消息 ID
        message: 消息内容
    """
    if isinstance(message, str):
        message = {
            "content": json.dumps({"text": message}),
        }
    message = patch(f"im/v1/messages/{message_id}", message, **kwargs)
    message.data.body.content = NestedDict.from_jsons(message.data.body.content)
    return message
