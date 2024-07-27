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

from contextlib import suppress
from email.utils import parseaddr
from json import JSONDecodeError, dumps

from chanfig import NestedDict

from feishu import variables


def infer_receive_id_type(receive_id: str) -> str:
    r"""
    根据 receive_id 来推断 receive_id_type
    """
    if receive_id.startswith("ou_"):
        return "open_id"
    if receive_id.startswith("on_"):
        return "union_id"
    if receive_id.startswith("oc_"):
        return "chat_id"
    if len(receive_id) == 8:
        return "user_id"
    if parseaddr(receive_id)[1]:
        return "email"
    raise ValueError("unable to infer receive_id_type")


def get_stream_message(content: str, streaming: bool = False, streaming_status_text: str | None = None) -> dict:
    r"""
    构建流消息的消息卡片

    Args:
        content (str): 消息内容
        streaming (bool): 是否正在流式传输
        streaming_status_text (str): 用于显示流式传输状态的文本，默认为 `variables.STREAMING_STATUS_TEXT`。

    阅读更多:
        [消息卡片](https://open.feishu.cn/document/server-docs/im-v1/message-card/overview)
    """
    elements = [{"tag": "div", "text": {"content": content, "tag": "lark_md"}}]

    if streaming:
        elements.append(
            {
                "tag": "div",
                "text": {
                    "content": streaming_status_text or variables.STREAMING_STATUS_TEXT,
                    "tag": "plain_text",
                    "text_size": "notation",
                    "text_color": "gray",
                    "text_align": "right",
                },
            }
        )

    return {
        "content": dumps({"config": {"wide_screen_mode": True}, "elements": elements}),
        "msg_type": "interactive",
    }


def convert_json_to_dict(content: str) -> NestedDict:
    r"""
    将 json 字符串转换为字典
    """
    with suppress(JSONDecodeError):
        return NestedDict.from_jsons(content)
