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

import logging
from functools import partial

from chanfig import NestedDict

from feishu import FeishuException, get_messages, send_message, variables
from feishu.im import get_message_text
from feishu.third_party import get_gpt_completions


def handle_chat(
    request: NestedDict,
    max_num_messages: int | None = None,
    max_message_length: int | None = None,
    stop_words: set[str] | None = None,
    stream: bool = True,
) -> dict:
    r"""
    回复飞书消息。

    这个函数会根据消息的类型选择不同的回复方式。

    对于私聊，会访问所有聊天记录，直到遇到停用词，或者达到最大消息数量。
    对于群聊，会访问消息及其回复链中所有的消息，直到达到最大消息数量。

    Args:
        request: 飞书请求。
        max_num_messages: 最大消息数量。默认为 [feishu.variables][] 中的 `MAX_NUM_MESSAGES`。
        max_message_length: 最大消息长度。默认为 [feishu.variables][] 中的 `MAX_MESSAGE_LENGTH`。
        stop_words: 停用词集合。默认为 [feishu.variables][] 中的 `STOP_WORDS`。
        stream: 是否使用流式 API。默认为 `True`。

    | 功能     | 实现函数                                      |
    |--------|-------------------------------------------|
    | 回复私聊消息 | [feishu.robot.chat.handle_chat_history][] |
    | 回复群聊消息 | [feishu.robot.chat.handle_chat_chain][]   |
    """
    if not isinstance(request, NestedDict):
        request = NestedDict(request)
    chat_type = request.event.message.chat_type
    if chat_type in {
        "p2p",
    }:
        return handle_chat_history(request, max_num_messages, max_message_length, stop_words, stream)
    if chat_type in {"group", "meeting", "private", "public"}:
        return handle_chat_chain(request, max_num_messages, max_message_length, stream)
    return {"status": "error", "message": "Unsupported chat type"}


def handle_chat_chain(
    request: NestedDict,
    max_num_messages: int | None = None,
    max_message_length: int | None = None,
    stream: bool = True,
) -> dict:
    r"""
    回复飞书消息及其回复链中所有的消息。

    Args:
        request: 飞书请求。
        max_num_messages: 最大消息数量。默认为 [feishu.variables][] 中的 `MAX_NUM_MESSAGES`。
        max_message_length: 最大消息长度。默认为 [feishu.variables][] 中的 `MAX_MESSAGE_LENGTH`。
        stream: 是否使用流式 API。默认为 `True`。

    相关函数:
        [feishu.im.messages.get_messages_chain][]
    """
    if not isinstance(request, NestedDict):
        request = NestedDict(request)
    message_id = request.event.message.message_id
    max_num_messages = max_num_messages or variables.MAX_NUM_MESSAGES
    max_message_length = max_message_length or variables.MAX_MESSAGE_LENGTH
    try:
        messages = get_messages(message_id, max_num_messages=max_num_messages, max_message_length=max_message_length)
    except FeishuException as e:
        logging.error("Failed to get Feishu messages: %s", e)
        return {"status": "error", "message": "Failed to get Feishu messages"}
    return _handle_chat(messages, message_id, uuid=request.header.event_id, stream=stream)


def handle_chat_history(
    request: NestedDict,
    max_num_messages: int | None = None,
    max_message_length: int | None = None,
    stop_words: set[str] | None = None,
    stream: bool = True,
) -> dict:
    r"""
    回复飞书对话中所有的消息。

    Args:
        request: 飞书请求。
        max_num_messages: 最大消息数量。默认为 [feishu.variables][] 中的 `MAX_NUM_MESSAGES`。
        max_message_length: 最大消息长度。默认为 [feishu.variables][] 中的 `MAX_MESSAGE_LENGTH`。
        stop_words: 停用词集合。默认为 [feishu.variables][] 中的 `STOP_WORDS`。
        stream: 是否使用流式 API。默认为 `True`。

    相关函数:
        [feishu.im.messages.get_messages_history][]
    """
    if not isinstance(request, NestedDict):
        request = NestedDict(request)
    stop_words = stop_words or variables.STOP_WORDS
    sender_id = request.event.sender.sender_id.open_id
    chat_id = request.event.message.chat_id
    max_num_messages = max_num_messages or variables.MAX_NUM_MESSAGES
    max_message_length = max_message_length or variables.MAX_MESSAGE_LENGTH
    try:
        messages = get_messages(
            chat_id, max_num_items=max_num_messages, early_stop=partial(early_stop, stop_words=stop_words)
        )
    except FeishuException as e:
        logging.error("Failed to get Feishu messages: %s", e)
        return {"status": "error", "message": "Failed to get Feishu messages"}
    return _handle_chat(messages, sender_id, uuid=request.header.event_id, stream=stream)


def early_stop(
    all_messages: list[NestedDict],  # pylint: disable=unused-argument
    message: NestedDict,
    stop_words: set[str],
) -> bool:
    if get_message_text(message) in stop_words:
        return True
    return False


def _handle_chat(messages: NestedDict, message_id, uuid, stream: bool = True) -> dict:
    prompt = build_chat_prompt(messages)
    if not prompt:
        return {"status": "error", "message": "No messages to reply"}
    try:
        response = get_gpt_completions(prompt, stream=stream)
    except Exception as e:
        logging.error("Failed to get GPT completions: %s", e)
        return {"status": "error", "message": "Failed to get GPT completions"}
    try:
        send_message(response, message_id, uuid=uuid)
    except FeishuException as e:
        logging.error("Failed to send Feishu message stream: %s", e)
        return {"status": "error", "message": "Failed to send Feishu message stream"}
    return {"status": "OK", "message_id": message_id}


def build_chat_prompt(messages: NestedDict) -> list[dict[str, str]]:
    return [
        {
            "role": "assistant" if message.sender.sender_type == "app" else "user",
            "content": get_message_text(message),
        }
        for message in sorted(messages.data["items"], key=lambda x: x.create_time)
    ]
