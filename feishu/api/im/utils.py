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

from email.utils import parseaddr
from json import dumps

from feishu.api import variables


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


def get_stream_message(content: str, streaming: bool = False, uuid: str = ""):
    elements = [{"tag": "div", "text": {"content": content, "tag": "lark_md"}}]

    if streaming:
        elements.append(
            {
                "tag": "div",
                "text": {
                    "content": variables.STREAMING_STATUS_TEXT,
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
        "uuid": uuid,
    }
