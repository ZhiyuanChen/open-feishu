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
from typing import Dict

from feishu import FeishuException
from feishu.utils import authorize, delete, get, pagination, patch, post, put

from .utils import convert_json_to_dict, get_stream_message, infer_receive_id_type

try:
    from openai import Stream

    openai_available = True
except ImportError:
    openai_available = False


@authorize
def send_message(
    message: str | Dict | Stream,
    id: str | None = None,
    receive_id_type: str | None = None,
    message_type: str | None = None,
    uuid: str | None = None,
    **kwargs,
):
    r"""
    发送消息

    当 `id` 以 `om_` 开头时，回复消息。
    否则，发送消息。

    Args:
        message: 消息内容
        id: 接收者 ID 或消息 ID
        receive_id_type: 接收者 ID 类型。默认为 `open_id`。
        message_type: 消息类型。默认为 `text`。
        uuid: 消息唯一标识，用于消息去重。

    飞书文档:
        [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)

        [回复消息](https://open.feishu.cn/document/server-docs/im-v1/message/reply)

    | 功能     | 实现函数                                        |
    |--------|---------------------------------------------|
    | 发送内容消息 | [feishu.im.messages.send_message_content][] |
    | 发送流式消息 | [feishu.im.messages.send_message_stream][]  |
    | 回复消息   | [feishu.im.messages.reply_message][]        |
    """
    if isinstance(message, str):
        message = {
            "content": json.dumps({"text": message}),
        }
    if id is None:
        id = message.get("receive_id")
    if id is None:
        raise ValueError("Unable to identify receiver")
    if id.startswith("om_"):
        return reply_message(message=message, messaege_id=id, **kwargs)
    if openai_available and isinstance(message, Stream):
        return send_message_stream(stream=message, receive_id=id, receive_id_type=receive_id_type, uuid=uuid, **kwargs)
    return send_message_content(
        message=message,
        receive_id=id,
        receive_id_type=receive_id_type,
        message_type=message_type,
        uuid=uuid,
        **kwargs,
    )


@authorize
def send_message_content(
    message: str | Dict,
    receive_id: str | None = None,
    receive_id_type: str | None = None,
    message_type: str | None = None,
    uuid: str | None = None,
    **kwargs,
):
    r"""
    发送消息

    Args:
        message: 消息内容
        receive_id: 接收者 ID
        receive_id_type: 接收者 ID 类型。默认为 `open_id`。
        message_type: 消息类型。默认为 `text`。
        uuid: 消息唯一标识，用于消息去重。

    飞书文档:
        [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)
    """
    if isinstance(message, str):
        message = {
            "content": json.dumps({"text": message}),
        }
    if receive_id is not None:
        message["receive_id"] = receive_id
    else:
        receive_id = message.get("receive_id")
        if receive_id is None:
            raise ValueError("receive_id is required")
    if receive_id_type is None:
        receive_id_type = infer_receive_id_type(receive_id)
    if message_type is not None:
        message["msg_type"] = message_type
    if "msg_type" not in message:
        message["msg_type"] = "text"
    if uuid is not None:
        message["uuid"] = uuid
    message = post("im/v1/messages", message, {"receive_id_type": receive_id_type}, **kwargs)
    message.data.body.content = convert_json_to_dict(message.data.body.content)  # type: ignore[union-attr]
    return message


@authorize
def send_message_stream(
    stream: Stream,
    receive_id: str,
    receive_id_type: str | None = None,
    uuid: str = "",
    **kwargs,
):
    r"""
    发送流式消息

    由于飞书暂时没有提供流式消息的直接支持，我们通过卡片消息的方式来模拟流式消息。

    这个方法会先发送一个空的卡片消息，然后通过不断地更新这个消息来模拟流式消息。

    Args:
        stream: 消息内容流
        receive_id: 接收者 ID
        receive_id_type: 接收者 ID 类型。默认为 `open_id`。
        uuid: 消息唯一标识，用于消息去重。

    飞书文档:
        [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)
    """
    if receive_id_type is None:
        receive_id_type = infer_receive_id_type(receive_id)
    content = ""
    message = get_stream_message(content)
    response = send_message(message, receive_id=receive_id, receive_id_type=receive_id_type, uuid=uuid, **kwargs)
    message_id = response["data"]["message_id"]
    try:
        for chunk in stream:
            content += chunk.choices[0].delta.content
            message = get_stream_message(content, streaming=True)
            patch_message(message, message_id, **kwargs)
    except Exception as e:
        raise e
    finally:
        message = get_stream_message(content)
        patch_message(message, message_id, **kwargs)


@authorize
def reply_message(
    message: str | Dict | Stream,
    message_id: str,
    message_type: str | None = None,
    reply_in_thread: bool | None = None,
    uuid: str | None = None,
    **kwargs,
):
    r"""
    回复消息

    Args:
        message: 消息内容
        message_id: 消息 ID
        message_type: 消息类型。默认为 `text`。
        reply_in_thread: 是否以话题形式回复；若要回复的消息已经是话题消息，则默认以话题形式进行回复。默认为 `False`。
        uuid: 消息唯一标识，用于消息去重。

    飞书文档:
        [回复消息](https://open.feishu.cn/document/server-docs/im-v1/message/reply)

    | 功能     | 实现函数                                         |
    |--------|----------------------------------------------|
    | 回复内容消息 | [feishu.im.messages.reply_message_content][] |
    | 回复流式消息 | [feishu.im.messages.reply_message_stream][]  |
    """
    if openai_available and isinstance(message, Stream):
        return reply_message_stream(message=message, message_id=message_id, uuid=uuid, **kwargs)
    return reply_message_content(
        message=message,
        message_id=message_id,
        message_type=message_type,
        reply_in_thread=reply_in_thread,
        uuid=uuid,
        **kwargs,
    )


@authorize
def reply_message_content(
    message: str | Dict | Stream,
    message_id: str,
    message_type: str | None = None,
    reply_in_thread: bool | None = None,
    uuid: str | None = None,
    **kwargs,
):
    r"""
    回复消息

    Args:
        message: 消息内容
        message_id: 消息 ID
        message_type: 消息类型。默认为 `text`。
        reply_in_thread: 是否以话题形式回复；若要回复的消息已经是话题消息，则默认以话题形式进行回复。默认为 `False`。
        uuid: 消息唯一标识，用于消息去重。

    飞书文档:
        [回复消息](https://open.feishu.cn/document/server-docs/im-v1/message/reply)
    """
    if openai_available and isinstance(message, Stream):
        return reply_message_stream(message, message_id, uuid, **kwargs)
    if isinstance(message, str):
        message = {
            "content": json.dumps({"text": message}),
        }
    if message_type is not None:
        message["msg_type"] = message_type
    if "msg_type" not in message:
        message["msg_type"] = "text"
    if reply_in_thread is not None:
        message["reply_in_thread"] = reply_in_thread
    if uuid is not None:
        message["uuid"] = uuid
    message = post(f"im/v1/messages/{message_id}/reply", message, **kwargs)
    message.data.body.content = convert_json_to_dict(message.data.body.content)
    return message


@authorize
def reply_message_stream(stream: Stream, message_id: str, uuid: str = "", **kwargs):
    r"""
    回复流式消息

    由于飞书暂时没有提供流式消息的直接支持，我们通过卡片消息的方式来模拟流式消息。

    这个方法会先发送一个空的卡片消息，然后通过不断地更新这个消息来模拟流式消息。

    Args:
        stream: 消息内容流
        message_id: 接收者 ID
        uuid: 消息唯一标识，用于消息去重。

    飞书文档:
        [回复消息](https://open.feishu.cn/document/server-docs/im-v1/message/reply)
    """
    content = ""
    message = get_stream_message(content)
    response = reply_message(message, message_id, uuid=uuid, **kwargs)
    message_id = response["data"]["message_id"]
    try:
        for chunk in stream:
            content += chunk.choices[0].delta.content
            message = get_stream_message(content, streaming=True)
            patch_message(message, message_id, **kwargs)
    except Exception as e:
        raise e
    finally:
        message = get_stream_message(content)
        patch_message(message, message_id, **kwargs)


@authorize
def update_message(message: str | Dict, message_id: str, **kwargs):
    r"""
    编辑消息

    Args:
        message: 消息内容
        message_id: 消息 ID

    飞书文档:
        [编辑消息](https://open.feishu.cn/document/server-docs/im-v1/message/update)
    """
    if isinstance(message, str):
        message = {
            "msg_type": "text",
            "content": json.dumps({"text": message}),
        }
    message = put(f"im/v1/messages/{message_id}", message, **kwargs)
    message.data.body.content = convert_json_to_dict(message.data.body.content)  # type: ignore[union-attr]
    return message


@authorize
def patch_message(message: str | Dict, message_id: str, **kwargs):
    r"""
    更新应用发送的消息卡片

    Args:
        message: 消息内容
        message_id: 消息 ID

    飞书文档:
        [更新应用发送的消息卡片](https://open.feishu.cn/document/server-docs/im-v1/message-card/patch)
    """
    if isinstance(message, str):
        message = {
            "content": json.dumps({"text": message}),
        }
    return patch(f"im/v1/messages/{message_id}", message, **kwargs)


@authorize
def recall_message(message_id: str, **kwargs):
    r"""
    撤回消息

    Args:
        message_id: 消息 ID

    飞书文档:
        [撤回消息](https://open.feishu.cn/document/server-docs/im-v1/message/recall)
    """
    return delete(f"im/v1/messages/{message_id}", **kwargs)


@authorize
def get_message(message_id: str, file_key: str | None = None, file_type: str | None = None, **kwargs):
    r"""
    获取消息

    当 `file_key` 不为空时，获取消息中的资源文件。
    否则，获取消息内容。

    Args:
        message_id: 消息 ID
        file_key: 文件 Key
        file_type: 文件类型

    飞书文档:
        [获取指定消息的内容](https://open.feishu.cn/document/server-docs/im-v1/message/get)

        [获取消息中的资源文件](https://open.feishu.cn/document/server-docs/im-v1/message/get-2)

    | 功能         | 实现函数                                        |
    |------------|---------------------------------------------|
    | 获取消息内容     | [feishu.im.messages.get_message_content][]  |
    | 获取消息中的资源文件 | [feishu.im.messages.get_message_resource][] |
    """
    if file_key is not None:
        return get_message_resource(message_id, file_key, file_type, **kwargs)
    return get_message_content(message_id, **kwargs)


@authorize
def get_message_content(message_id: str, **kwargs):
    r"""
    获取消息内容

    Args:
        message_id: 消息 ID

    飞书文档:
        [获取指定消息的内容](https://open.feishu.cn/document/server-docs/im-v1/message/get)
    """
    message = get(f"im/v1/messages/{message_id}", **kwargs)
    message.data = message.data["items"][0]
    message.data.body.content = convert_json_to_dict(message.data.body.content)
    return message


@authorize
def get_message_resource(message_id: str, file_key: str, type: str, **kwargs):
    r"""
    获取消息中的资源文件

    Args:
        message_id: 消息 ID

    飞书文档:
        [获取消息中的资源文件](https://open.feishu.cn/document/server-docs/im-v1/message/get-2)
    """
    return get(f"im/v1/messages/{message_id}/resources/{file_key}?type={type}", **kwargs)


@authorize
def get_messages(
    id: str,
    container_id_type: str = "chat",
    max_num_messages: int | float = float("inf"),
    max_message_length: int | float = float("inf"),
    start_time: int | None = None,
    end_time: int | None = None,
    sort_type: str = "ByCreateTimeAsc",
    page_size: int = 50,
    page_token: str | None = None,
    **kwargs,
):
    r"""
    获取消息

    当 `id` 以 `om_` 开头时，获取消息及其回复链中所有消息的内容。
    当 `id` 以其他方式开头时，获取会话历史消息。

    Args:
        id: 消息 ID / 会话 ID
        container_id_type: 容器 ID 类型。默认为 `chat`。
            只用于 `id` 以其他方式开头时。
        max_num_messages: 最大历史消息数量。默认为正无穷。
            只用于 `id` 以 `om_` 开头时。
        max_message_length: 最大历史消息长度。默认为正无穷。
            只用于 `id` 以 `om_` 开头时。
        start_time: 开始时间。默认为 `None`。
            只用于 `id` 以其他方式开头时。
        end_time: 结束时间。默认为 `None`。
            只用于 `id` 以其他方式开头时。
        sort_type: 排序方式。默认为 `ByCreateTimeAsc`。
            只用于 `id` 以其他方式开头时。
        page_size: 每页数量。默认为 50。
            只用于 `id` 以其他方式开头时。
        page_token: 分页标识。默认为 `None`。
            只用于 `id` 以其他方式开头时。

    飞书文档:
        [获取指定消息的内容](https://open.feishu.cn/document/server-docs/im-v1/message/get)

        [获取会话历史消息](https://open.feishu.cn/document/server-docs/im-v1/message/list)

    | 功能                | 实现函数                                        |
    |-------------------|---------------------------------------------|
    | 获取消息及其回复链中所有消息的内容 | [feishu.im.messages.get_messages_chain][]   |
    | 获取会话历史消息          | [feishu.im.messages.get_messages_history][] |
    """
    if id.startswith("om_"):
        return get_messages_chain(
            message_id=id, max_num_messages=max_num_messages, max_message_length=max_message_length, **kwargs
        )
    return get_messages_history(
        container_id=id,
        container_id_type=container_id_type,
        start_time=start_time,
        end_time=end_time,
        sort_type=sort_type,
        page_size=page_size,
        page_token=page_token,
        **kwargs,
    )


@authorize
def get_messages_chain(
    message_id: str,
    max_num_messages: int | float = float("inf"),
    max_message_length: int | float = float("inf"),
    **kwargs,
):
    r"""
    获取消息及其回复链中所有消息的内容

    Args:
        message_id: 消息 ID
        max_num_messages: 最大历史消息数量。默认为正无穷。
        max_message_length: 最大历史消息长度。默认为正无穷。

    飞书文档:
        [获取指定消息的内容](https://open.feishu.cn/document/server-docs/im-v1/message/get)
    """
    num_messages = 0
    message = get_message_content(message_id, **kwargs)
    message_length = len(message.data.body.content)
    all_items = [message.data]
    parent_id = message.data.get("parent_id")
    while parent_id and num_messages < max_num_messages and message_length < max_message_length:
        try:
            response = get_message_content(parent_id, **kwargs).data
            all_items.append(response)
            parent_id = response.get("parent_id")
            num_messages += 1
            message_length += len(response.body.content)
        except FeishuException:
            break
    message.data = all_items
    return message


@pagination
@authorize
def get_messages_history(
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

    Args:
        container_id: 会话 ID
        container_id_type: 会话 ID 类型。默认为 `chat`。可以是 `chat` 或 `group` 或 `meeting`。
        start_time: 开始时间。默认为 `None`。
        end_time: 结束时间。默认为 `None`。
        sort_type: 排序方式。默认为 `ByCreateTimeAsc`。可以是 `ByCreateTimeAsc` 或 `ByCreateTimeDesc`。
        page_size: 每页数量。默认为 50。
        page_token: 分页标识。默认为 `None`。

    飞书文档:
        [获取会话历史消息](https://open.feishu.cn/document/server-docs/im-v1/message/list)
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
    for m in message.data["items"]:
        m.body.content = convert_json_to_dict(m.body.content)
    return message


@authorize
def forward_message(
    message_id: str | list[str], receive_id: str, receive_id_type: str | None = None, uuid: str = "", **kwargs
):
    r"""
    转发消息

    当 `message_id` 为列表时，合并转发消息。
    否则，转发消息。

    Args:
        message_id: 消息 ID
        receive_id: 接收者 ID
        receive_id_type: 接收者 ID 类型。默认为 `open_id`。

    飞书文档:
        [转发消息](https://open.feishu.cn/document/server-docs/im-v1/message/forward)

        [合并转发消息](https://open.feishu.cn/document/server-docs/im-v1/message/merge_forward)

    | 功能       | 实现函数                                        |
    |----------|---------------------------------------------|
    | 转发消息     | [feishu.im.messages.forward_message][]      |
    | 合并转发多条消息 | [feishu.im.messages.forward_message_list][] |
    """
    if isinstance(message_id, list):
        return forward_message_list(message_id, receive_id, receive_id_type, uuid, **kwargs)
    if receive_id_type is None:
        receive_id_type = infer_receive_id_type(receive_id)
    message = post(
        f"im/v1/messages/{message_id}/forward",
        {"receive_id": receive_id},
        {"receive_id_type": receive_id_type, "uuid": uuid},
        **kwargs,
    )
    message.data.body.content = convert_json_to_dict(message.data.body.content)
    return message


@authorize
def forward_message_list(
    message_id_list: list[str],
    receive_id: str,
    receive_id_type: str | None = None,
    uuid: str = "",
    **kwargs,
):
    r"""
    合并转发消息

    Args:
        message_id_list: 消息 ID 列表
        receive_id: 接收者 ID
        receive_id_type: 接收者 ID 类型。默认为 `open_id`。

    飞书文档:
        [合并转发消息](https://open.feishu.cn/document/server-docs/im-v1/message/merge_forward)
    """
    if receive_id_type is None:
        receive_id_type = infer_receive_id_type(receive_id)
    message = post(
        "im/v1/messages/merge-forward",
        {"message_id_list": message_id_list, "receive_id": receive_id},
        {"receive_id_type": receive_id_type, "uuid": uuid},
        **kwargs,
    )
    message.data.body.content = convert_json_to_dict(message.data.body.content)
    return message


@pagination
@authorize
def read_users(message_id: str, user_id_type: str = "open_id", **kwargs):
    r"""
    查询消息已读信息

    Args:
        message_id: 消息 ID
        user_id_type: 用户 ID 类型。默认为 `open_id`。可以是 `open_id` 或 "union_id" 或 `user_id`。

    飞书文档:
        [查询消息已读信息](https://open.feishu.cn/document/server-docs/im-v1/message/read_users)
    """
    return get(f"im/v1/messages/{message_id}/read_users", {"user_id_type": user_id_type}, **kwargs)


@authorize
def push_follow_up(message_id: str, follow_ups: str | Dict, **kwargs):
    r"""
    添加跟随气泡

    Args:
        message_id: 消息 ID
        follow_ups: 跟随气泡内容

    飞书文档:
        [添加跟随气泡](https://open.feishu.cn/document/server-docs/im-v1/message/push_follow_up)
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
